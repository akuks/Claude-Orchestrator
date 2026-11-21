"""Claude Code worker manager.

An in-process asyncio pool that dispatches queued tasks respecting priority and
a concurrency limit. Each task spawns the Claude Code CLI in an isolated
workspace, streams its stream-json output as normalized events, and records
results + output artifacts. No external broker/queue needed for Phase 1.
"""

import asyncio
import json
import mimetypes
import os
import signal
import time
from collections import Counter
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from . import auth, context_store, mcp_manager, memory, notifications, security
from .access import effective_project_id
from .agent_env import PROJECT_ENV_BLOCKED, MissingCredential, build_agent_env
from .config import settings
from .constants import Priority, Status
from .database import SessionLocal
from .events import broker
from .guard import grants as guard_grants
from .guard import policy_inputs
from .guard import runtime as guard_runtime
from .guard.plugin import ensure_plugin
from .models import Agent, Artifact, McpCall, Project, SkillPack, Task, TaskEvent, User
from .redaction import redact, redact_obj, secrets_from_env

_MAX_TOOL_RESULT_CHARS = 2000


def _guard_project(proj: Project, directory: str | None) -> SimpleNamespace:
    """The project's guard and policy settings for this run (``directory``
    None: an ad-hoc thread step keeps its own workspace as the project dir)."""
    return SimpleNamespace(
        id=proj.id,
        directory=directory,
        guard_enabled=getattr(proj, "guard_enabled", True),
        approval_wait_seconds=getattr(proj, "approval_wait_seconds", 300),
        trust=getattr(proj, "trust", "own"),
        quarantine=getattr(proj, "quarantine", False),
        on_no_answer=getattr(proj, "on_no_answer", "deny"),
        readonly_paths=list(getattr(proj, "readonly_paths", None) or []),
        reference_project_ids=list(getattr(proj, "reference_project_ids", None) or []),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def project_name_for(s, task: Task) -> str | None:
    """The CURRENT name of the task's project, used to match project-scoped MCP
    servers (which are keyed by project name). Task.project is a snapshot taken
    at creation and goes stale on rename; trusting it would hand a renamed
    project's old tasks to whichever project later takes the freed name. No
    project link → no project-scoped servers."""
    if not task.project_id:
        return None
    proj = await s.get(Project, task.project_id)
    return proj.name if proj is not None else None


def _snapshot(root: Path) -> dict[str, float]:
    snap: dict[str, float] = {}
    if not root.exists():
        return snap
    for p in root.rglob("*"):
        if p.is_file():
            try:
                snap[str(p.relative_to(root))] = p.stat().st_mtime
            except OSError:
                continue
    return snap


def _collect_artifacts(root: Path, before: dict[str, float]) -> list[dict]:
    out: list[dict] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        try:
            st = p.stat()
        except OSError:
            continue
        if rel not in before or before[rel] != st.st_mtime:
            mime, _ = mimetypes.guess_type(p.name)
            out.append(
                {"filename": p.name, "rel_path": rel, "size": st.st_size, "mime": mime}
            )
    return out


_TRANSIENT_MARKERS = (
    "rate limit",
    "rate_limit",
    "429",
    "overloaded",
    "529",
    "api_error",
    "internal server error",
    " 500",
    " 502",
    " 503",
    "econnreset",
    "etimedout",
    "connection reset",
    "fetch failed",
    "socket hang",
    "network error",
)


def _is_transient(stderr: str) -> bool:
    """True if a failure looks retryable (rate limit / overload / network).
    Deliberately excludes max-turns and other non-transient stops."""
    text = (stderr or "").lower()
    if "max_turns" in text or "max turns" in text:
        return False
    return any(m in text for m in _TRANSIENT_MARKERS)


def _short(value, secrets: list[str] | None = None) -> str:
    if isinstance(value, list):
        parts = []
        for b in value:
            if isinstance(b, dict):
                parts.append(b.get("text") or b.get("content") or "")
            else:
                parts.append(str(b))
        value = " ".join(str(p) for p in parts)
    text = str(value)
    if secrets is not None:  # before the cut, so no secret prefix survives it
        text = redact(text, secrets)
    return text[:_MAX_TOOL_RESULT_CHARS]


def _session_file_exists(config_dir: str | None, session_id: str) -> bool:
    """Whether `<config_dir>/projects/**/<session_id>.jsonl` exists."""
    # The id becomes a glob pattern: refuse separators and glob characters.
    if not config_dir or not session_id or any(c in session_id for c in "/\\*?[]"):
        return False
    projects = Path(config_dir) / "projects"
    if not projects.is_dir():
        return False
    name = f"{session_id}.jsonl"
    return any(p.is_file() for p in projects.rglob(name))


async def _coordinator_context(s, task_id: str, me_id: str) -> str:
    """Build the registry + delegation instructions injected into a coordinator's
    prompt: the other agents it can delegate to, and how to dispatch to them."""
    rows = (await s.execute(select(Agent).order_by(Agent.folder, Agent.name))).scalars().all()
    lines = []
    for a in rows:
        if a.id == me_id or a.coordinator:
            continue  # don't list self or other coordinators
        flags = []
        if a.requires_approval:
            flags.append("approval-gated")
        if a.allow_remote:
            flags.append("ssh")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        where = f" (project: {a.folder})" if a.folder else ""
        lines.append(f'- "{a.name}"{where}{suffix}: {a.description or "—"}')
    registry = "\n".join(lines) or "(no specialist agents available)"
    base = settings.api_base.rstrip("/")
    return (
        "# You are a COORDINATOR (tech lead)\n"
        "Analyse the task below, break it into sub-tasks, and DELEGATE each to the "
        "right specialist agent — do not write the code yourself.\n\n"
        "## Agents you can delegate to\n"
        f"{registry}\n\n"
        "## How to delegate\n"
        "Dispatch all independent sub-tasks in ONE call so they run in parallel. "
        "Use curl against the local orchestrator API:\n"
        f"```\ncurl -s -X POST {base}/agents/dispatch -H \"Authorization: Bearer $CO_API_TOKEN\" -H 'Content-Type: application/json' -d '{{\n"
        '  "parent_task_id": "' + task_id + '",\n'
        '  "items": [\n'
        '    {"agent": "<exact agent name>", "prompt": "<what that agent should do>"},\n'
        '    {"agent": "<exact agent name>", "prompt": "<...>"}\n'
        "  ]\n}'\n```\n"
        "Each item becomes a real child task (shown under you). For a dependent stage "
        "(e.g. test AFTER code, deploy AFTER test), dispatch the first stage, then "
        f"poll each child with `curl -s -H \"Authorization: Bearer $CO_API_TOKEN\" {base}/tasks/<child_id>` until its status is "
        "`completed` (abort the chain if one is `failed`), then dispatch the next "
        "stage. Approval-gated agents (e.g. deploy) will sit in `awaiting_approval` "
        "until a human approves — report that and move on; do not try to approve them.\n"
        "Finish with a short summary: which agents you delegated what, and the child "
        "task ids."
    )


class WorkerManager:
    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._sem = asyncio.Semaphore(settings.worker_concurrency)
        self._running: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()
        # task_id -> the current run's secret values (see _run_job's emit).
        self._run_secrets: dict[str, list[str]] = {}
        # task_id -> {"emit", "reported"} of a live run: POST /guard/report
        # emits through the run's own emit (same seq, same redaction), and the
        # spool ingestion skips what was already reported (Counter of keys).
        self._guard_live: dict[str, dict] = {}
        self._dispatcher: asyncio.Task | None = None

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        self._dispatcher = asyncio.create_task(self._dispatch_loop())
        await self._requeue_pending()

    @staticmethod
    def _signal_group(proc, sig: int) -> None:
        """Signal claude AND every tool it started: it runs in its own process
        group (start_new_session); fall back to the process alone."""
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                pass

    async def stop(self) -> None:
        if self._dispatcher:
            self._dispatcher.cancel()
        for proc in list(self._running.values()):
            self._signal_group(proc, signal.SIGTERM)

    async def _requeue_pending(self) -> None:
        """On boot, re-queue anything left QUEUED, and reset orphaned RUNNING."""
        async with SessionLocal() as s:
            rows = (
                await s.execute(
                    select(Task).where(Task.status.in_([Status.QUEUED, Status.RUNNING]))
                )
            ).scalars().all()
            for t in rows:
                if t.status == Status.RUNNING:
                    t.status = Status.QUEUED
                await self._enqueue(t.id, t.priority, t.created_at)
            await s.commit()

    # ---- public control --------------------------------------------------

    async def submit(self, task_id: str, priority: str, created_at: datetime) -> None:
        await self._enqueue(task_id, priority, created_at)

    async def cancel(self, task_id: str) -> None:
        self._cancelled.add(task_id)
        proc = self._running.get(task_id)
        if proc:
            self._signal_group(proc, signal.SIGTERM)

    async def _retry_after(self, task_id, delay, priority, created_at) -> None:
        await asyncio.sleep(delay)
        if task_id in self._cancelled:
            return
        await self.submit(task_id, priority, created_at)

    # ---- dispatch --------------------------------------------------------

    async def _enqueue(self, task_id: str, priority: str, created_at: datetime) -> None:
        order = Priority.ORDER.get(priority, 2)
        ts = created_at.timestamp() if created_at else 0.0
        await self._queue.put((order, ts, task_id))

    async def _dispatch_loop(self) -> None:
        while True:
            _, _, task_id = await self._queue.get()
            await self._sem.acquire()
            asyncio.create_task(self._run_guarded(task_id))

    async def _run_guarded(self, task_id: str) -> None:
        try:
            await self._run_job(task_id)
        except Exception as exc:  # noqa: BLE001 - never let the pool die
            await self._finish(
                task_id,
                Status.FAILED,
                error=f"worker error: {exc}",
                secrets=self._run_secrets.get(task_id),
            )
        finally:
            self._run_secrets.pop(task_id, None)
            self._guard_live.pop(task_id, None)
            self._sem.release()

    # ---- execution -------------------------------------------------------

    async def _run_job(self, task_id: str) -> None:
        # Continue the event sequence across retries so re-runs append cleanly.
        async with SessionLocal() as s:
            seq = (
                await s.execute(
                    select(func.max(TaskEvent.seq)).where(TaskEvent.task_id == task_id)
                )
            ).scalar() or 0

        # Values of this run's sensitive env (filled once the env is built, and
        # again once CO_API_TOKEN is minted). Every event payload is redacted
        # against it (plus known token shapes) before it is stored or streamed:
        # an agent can print its env and run logs are readable by project members.
        # Kept per task on the worker so _run_guarded's exception path can
        # redact by exact value too.
        run_secrets: list[str] = []
        self._run_secrets[task_id] = run_secrets

        async def emit(etype: str, payload: dict) -> None:
            nonlocal seq
            seq += 1
            payload = redact_obj(payload, run_secrets)
            async with SessionLocal() as s:
                s.add(TaskEvent(task_id=task_id, seq=seq, type=etype, payload=payload))
                await s.commit()
            await broker.publish(task_id, {"seq": seq, "type": etype, "payload": payload})

        self._guard_live[task_id] = {"emit": emit, "reported": Counter()}

        # A disabled user's queued/automated tasks must not run.
        async with SessionLocal() as s:
            t0 = await s.get(Task, task_id)
            owner = await s.get(User, t0.created_by) if t0 and t0.created_by else None
            owner_disabled = owner is not None and not owner.active
        if owner_disabled:
            await emit("error", {"message": "Task owner is disabled"})
            await self._finish(task_id, Status.FAILED, error="Task owner is disabled")
            return

        # Load + mark running (or short-circuit if cancelled while queued).
        async with SessionLocal() as s:
            task = await s.get(Task, task_id)
            if task is None:
                return
            if task_id in self._cancelled:
                self._cancelled.discard(task_id)
                task.status = Status.CANCELLED
                task.completed_at = _now()
                await s.commit()
                await broker.publish(
                    task_id, {"seq": 0, "type": "completed", "payload": {"status": Status.CANCELLED}}
                )
                try:  # co-guard: drop follow-ups deferred on this step
                    await guard_grants.after_task_finished(task_id, submit=self.submit)
                except Exception:  # noqa: BLE001
                    pass
                return
            task.status = Status.RUNNING
            task.started_at = _now()
            prompt = task.prompt
            model = task.model
            max_turns = task.max_turns
            max_budget_usd = task.max_budget_usd
            project = await project_name_for(s, task)
            project_id = task.project_id
            resume_session_id = task.resume_session_id
            attempt = task.attempt
            task_priority = task.priority
            task_created_at = task.created_at
            system_prompt = task.system_prompt
            allow_remote = task.allow_remote
            read_only = task.read_only
            task_agent_id = task.agent_id
            task_owner = task.created_by
            workspace = Path(task.workspace_dir or (settings.workspaces_dir / task_id))
            # Per-project environment (toolchain scoping) for this task's subprocess.
            project_env = {}
            # co-guard: the project's guard and policy settings.
            guard_project = None
            if task.project_id:
                proj = await s.get(Project, task.project_id)
                if proj and proj.env:
                    project_env = dict(proj.env)
                if proj is not None:
                    guard_project = _guard_project(proj, proj.directory)
            else:
                # An ad-hoc step of a project thread (follow-up / dispatched
                # child): the thread root's guard settings, as approvals use
                # (access.effective_project_id); its own workspace stays its
                # project dir.
                eff = await effective_project_id(s, task)
                proj = await s.get(Project, eff) if eff else None
                if proj is not None:
                    guard_project = _guard_project(proj, None)
            # Coordinator: inject the live agent registry + how to delegate — but
            # NOT inside a workflow, where the workflow owns the orchestration and a
            # coordinator agent is used only as a planning step.
            coordinator_block = None
            if task_agent_id and not task.workflow_run_id:
                me = await s.get(Agent, task_agent_id)
                if me is not None and me.coordinator:
                    coordinator_block = await _coordinator_context(s, task_id, me.id)
            # Enabled skill packs (Claude Code plugins) for this task: global ones
            # plus any scoped to this task's project.
            plugin_dirs = []
            packs = (
                await s.execute(select(SkillPack).where(SkillPack.enabled.is_(True)))
            ).scalars().all()
            for p in packs:
                if p.scope == "global" or (p.scope == "project" and p.project_id == task.project_id):
                    if Path(p.local_path).exists():
                        plugin_dirs.append(p.local_path)
            await s.commit()
        # Re-read the prompt now that the task is marked running: a co-guard
        # resume approval may have joined this queued follow-up (its update
        # applies only while the task is still queued).
        async with SessionLocal() as s:
            fresh = await s.get(Task, task_id)
            if fresh is not None:
                prompt = fresh.prompt

        # Build the subprocess environment from scratch (app.agent_env): the
        # allowlisted base + project env + the task creator's own credentials
        # (or the org fallback). Nothing else of the server env is inherited, so
        # server config (CO_*) and the server's own Claude/GitHub credentials
        # never reach the agent, which a member can make print its env. No
        # credential (or a legacy ownerless task): fail before spawning.
        try:
            async with SessionLocal() as s:
                sub_env = await build_agent_env(s, task_owner, project_env=project_env)
            run_secrets[:] = secrets_from_env(sub_env)
        except MissingCredential as e:
            await emit("error", {"message": str(e)})
            await self._finish(task_id, Status.FAILED, error=str(e))
            return

        workspace.mkdir(parents=True, exist_ok=True)
        await emit("started", {"model": model, "max_turns": max_turns})

        # Resume only a session this account actually has: Claude Code keeps it
        # as <CLAUDE_CONFIG_DIR>/projects/<cwd>/<id>.jsonl. If it's gone (another
        # host, cleared config, a different per-user dir), start a fresh session
        # with the same context a new thread gets instead of failing the resume.
        if resume_session_id and not _session_file_exists(
            sub_env.get("CLAUDE_CONFIG_DIR"), resume_session_id
        ):
            resume_session_id = None
            await emit("fresh_session", {"reason": "previous session not available"})

        # Project tasks: prepend assembled context (instructions + memory + relevant
        # past-task summaries) so Claude starts with the project's accumulated state.
        if project_id and not resume_session_id:
            preamble, ctx_log = await memory.assemble_context(project_id, prompt)
            if preamble:
                prompt = f"{preamble}\n\n---\n\n# Task\n\n{prompt}"
                await emit("context", ctx_log)

        # Context Store (tiered): write applicable curated docs (L2) to a per-task
        # snapshot dir outside the cwd, and prepend their one-line abstracts (L0)
        # so the agent opens only what's relevant. Also on resumed threads — each
        # task gets its own snapshot, and the previous one is cleaned up.
        kb_index, kb_log = await context_store.prepare_for_task(project_id, task_id)
        if kb_index:
            prompt = f"{kb_index}\n\n---\n\n# Task\n\n{prompt}"
            await emit("context_store", kb_log)

        # Coordinator: prepend the agent registry + delegation instructions.
        if coordinator_block and not resume_session_id:
            prompt = f"{coordinator_block}\n\n---\n\n# Task\n\n{prompt}"
            await emit("coordinator", {"agents_injected": True})

        # Resolve MCP servers for this task and write the per-task config to a
        # dedicated area that is NEVER any task's working directory (not the repo,
        # not the sandbox cwd), so the config's vault secrets can't be read in — or
        # committed from — the agent's cwd. --mcp-config uses the absolute path.
        mcp_config_dir = settings.workspaces_dir / "_mcp_configs" / task_id
        mcp = await mcp_manager.prepare_for_task(project, mcp_config_dir)

        # co-guard: per-task policy + spool dir (outside the cwd), env, and the
        # settings that could switch its hooks off (Ruling R6 follow-up).
        guard_plugin, setting_sources, guard_inputs = await self._prepare_guard(
            task_id, guard_project, workspace, sub_env, mcp, emit, owner_id=task_owner
        )
        if guard_plugin is None:
            return
        # external projects: forge's data line (also re-added by the SessionStart hook)
        system_prompt = "\n\n".join(filter(None, [system_prompt, guard_inputs.data_line]))

        # Repo-backed project tasks: skip artifact diffing (git tracks changes and
        # scanning a whole repo is slow/noisy). Sandbox tasks still collect outputs.
        before = {} if project_id else _snapshot(workspace)

        cmd = [
            settings.claude_bin,
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--max-turns",
            str(max_turns),
            "--permission-mode",
            settings.claude_permission_mode,
            # (no --include-hook-events: the co-guard watchdog reads the hook's
            # spool; stream hook events would only double the event writes)
            # co-guard first, before any skill pack.
            "--plugin-dir",
            guard_plugin,
        ]
        if setting_sources:
            cmd += ["--setting-sources", setting_sources]
        # Only force a model when one was chosen; otherwise Claude Code uses its
        # own configured default.
        if model:
            cmd += ["--model", model]
        if settings.fallback_model:
            cmd += ["--fallback-model", settings.fallback_model]
        if max_budget_usd:
            cmd += ["--max-budget-usd", str(max_budget_usd)]
        if system_prompt:
            cmd += ["--append-system-prompt", system_prompt]
        if resume_session_id:
            cmd += ["--resume", resume_session_id]
        # Load enabled skill packs (Claude Code plugins) for this task.
        for pd in plugin_dirs:
            cmd += ["--plugin-dir", pd]
        if plugin_dirs:
            await emit("skills", {"plugin_dirs": plugin_dirs})
        allowed_tools = list(mcp["allowed"]) if mcp else []
        disallowed_tools = list(mcp["disallowed"]) if mcp else []
        # co-guard cannot check PowerShell (Ruling R17): never offer it.
        disallowed_tools.append("PowerShell")
        # SSH is opt-in per agent: unless the task is remote-enabled, deny outbound
        # ssh/scp/sftp. Deny rules win even under --permission-mode bypassPermissions.
        if not allow_remote:
            disallowed_tools += ["Bash(ssh:*)", "Bash(scp:*)", "Bash(sftp:*)"]
        # Read-only agents cannot modify code: block file-edit tools and git writes.
        # Deny rules win even under --permission-mode bypassPermissions.
        if read_only:
            disallowed_tools += [
                "Edit", "Write", "MultiEdit", "NotebookEdit",
                "Bash(git commit:*)", "Bash(git push:*)", "Bash(git merge:*)",
                "Bash(git add:*)", "Bash(gh pr merge:*)",
            ]
        if kb_index:
            # Let the agent Read the snapshot even outside bypassPermissions mode.
            cmd += ["--add-dir", kb_log["dir"]]
        # reference projects: readable (read-only under co-guard's readonly-source)
        for ref_dir in guard_inputs.add_dirs:
            cmd += ["--add-dir", ref_dir]
        if mcp:
            # Ruling R23: strict only when the orchestrator supplies MCP servers, so
            # tasks keep the MCP servers of admin-installed skill packs. Subagent
            # frontmatter mcpServers stay closed by R20b: writes to .claude/agents/**
            # need approval (agent-config) and the per-user config dir is hard-protected.
            cmd += ["--mcp-config", mcp["config_path"], "--strict-mcp-config"]
        # Ruling R20c: skills' / slash commands' inline !`cmd` would run outside PreToolUse.
        cmd += ["--settings", json.dumps({"disableSkillShellExecution": True})]
        if mcp:
            await emit(
                "mcp",
                {
                    "servers": mcp["server_names"],
                    "auto_approved": mcp["allowed"],
                    "blocked": mcp["disallowed"],
                },
            )
        if allowed_tools:
            cmd += ["--allowedTools", *allowed_tools]
        if disallowed_tools:
            cmd += ["--disallowedTools", *disallowed_tools]

        result = {
            "result_text": None,
            "num_turns": None,
            "total_cost_usd": None,
            "duration_ms": None,
            "is_error": False,
            "input_tokens": None,
            "output_tokens": None,
        }
        stderr_buf: list[str] = []

        # Names only, never values.
        await emit(
            "env",
            {
                "applied": sorted(k for k in project_env if k not in PROJECT_ENV_BLOCKED),
                "blocked": sorted(k for k in project_env if k in PROJECT_ENV_BLOCKED),
                "credentials": sub_env.credentials,
            },
        )

        # Scoped agent token: lets a coordinator call back into the API as the
        # task's owner (dispatch + read own tree). Env only - never in the prompt,
        # workspace files, events or logs. Ownerless (legacy) tasks run without one.
        if task_owner:
            async with SessionLocal() as s:
                # An interrupted earlier attempt (crash/restart) may have left one live.
                await auth.revoke_task_tokens(s, task_id)
                sub_env["CO_API_TOKEN"] = await auth.mint_token(
                    s,
                    task_owner,
                    "agent-task",
                    task_id,
                    ttl_seconds=min(settings.task_timeout_seconds, 86400),
                )
                await s.commit()
            run_secrets[:] = secrets_from_env(sub_env)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workspace),
                env=dict(sub_env),  # uvloop only accepts a plain dict
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Raise the per-line buffer limit; stream-json events can be large.
                limit=settings.stream_buffer_limit_bytes,
                # own process group, so the co-guard watchdog can stop the tools it started too
                start_new_session=True,
            )
        except FileNotFoundError:
            guard_runtime.remove_task_dir(task_id)
            await emit("error", {"message": f"Claude binary not found: {settings.claude_bin}"})
            await self._finish(
                task_id, Status.FAILED, error=f"Claude binary not found: {settings.claude_bin}"
            )
            return

        self._running[task_id] = proc

        try:
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

        async def read_stderr() -> None:
            assert proc.stderr is not None
            while True:
                chunk = await proc.stderr.read(65536)
                if not chunk:
                    break
                stderr_buf.append(chunk.decode(errors="replace"))

        stderr_task = asyncio.create_task(read_stderr())

        ctx = {
            "task_id": task_id,
            "mcp_ids": {},
            "secrets": run_secrets,
            # co-guard watchdog (Ruling R21): tool_use ids checked through the spool
            "guard": {"calls": {}, "lanes": {}, "done": set(), "seen": set(), "offset": 0, "violation": None,
                      "proc": proc, "emit": emit,
                      "spool": guard_runtime.task_dir(task_id) / guard_runtime.SPOOL_NAME},
        }
        watch = asyncio.create_task(self._guard_watch(ctx))
        try:
            await asyncio.wait_for(
                self._consume_stdout(proc, emit, result, ctx),
                timeout=settings.task_timeout_seconds,
            )
            await proc.wait()
        except asyncio.TimeoutError:
            self._signal_group(proc, signal.SIGKILL)
            await proc.wait()
            await stderr_task
            self._running.pop(task_id, None)
            await self._ingest_guard_spool(task_id, emit)
            await emit("error", {"message": "Task exceeded time limit"})
            await self._finish(task_id, Status.FAILED, error="Task exceeded time limit")
            return
        finally:
            watch.cancel()
            await stderr_task

        self._running.pop(task_id, None)
        exit_code = proc.returncode
        await self._ingest_guard_spool(task_id, emit)

        if ctx["guard"]["violation"]:
            # never retried: the guard was not protecting this run
            self._cancelled.discard(task_id)
            msg = "co-guard was not running — task stopped for safety"
            await emit("error", {"message": msg})
            await self._finish(task_id, Status.FAILED, exit_code=exit_code,
                               error=f"{msg} ({ctx['guard']['violation']})", secrets=run_secrets)
            return

        if task_id in self._cancelled:
            self._cancelled.discard(task_id)
            await emit("completed", {"status": Status.CANCELLED})
            await self._finish(task_id, Status.CANCELLED, exit_code=exit_code)
            return

        artifacts = [] if project_id else _collect_artifacts(workspace, before)
        failed = result["is_error"] or (exit_code not in (0, None))

        # Auto-retry transient failures (rate limits / overload / network) with
        # exponential backoff, up to the configured attempt limit.
        if (
            failed
            and attempt < settings.retry_max_attempts
            and _is_transient("".join(stderr_buf))
        ):
            backoff = settings.retry_backoff_seconds * (2**attempt)
            await emit(
                "retry",
                {"attempt": attempt + 1, "backoff_seconds": backoff, "reason": "transient error"},
            )
            async with SessionLocal() as s:
                t = await s.get(Task, task_id)
                if t is not None:
                    t.attempt = attempt + 1
                    t.status = Status.QUEUED
                # This attempt is over: don't leave its token live through backoff.
                await auth.revoke_task_tokens(s, task_id)
                await s.commit()
            asyncio.create_task(
                self._retry_after(task_id, backoff, task_priority, task_created_at)
            )
            return

        status = Status.FAILED if failed else Status.COMPLETED
        await emit(
            "completed",
            {
                "status": status,
                "num_turns": result["num_turns"],
                "cost": result["total_cost_usd"],
                "artifacts": len(artifacts),
            },
        )
        await self._finish(
            task_id,
            status,
            exit_code=exit_code,
            result_text=result["result_text"],
            num_turns=result["num_turns"],
            total_cost_usd=result["total_cost_usd"],
            duration_ms=result["duration_ms"],
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            error=("".join(stderr_buf).strip() or None) if failed else None,
            artifacts=artifacts,
            secrets=run_secrets,
        )

        # Fold the completed work into the project's living memory (background,
        # non-blocking — it makes its own cheap Claude call).
        if project_id and not failed:
            # Both run Claude as the task's owner (skipped if they can't).
            asyncio.create_task(memory.update_after_task(task_id, task_owner))
            # Security review → extract structured findings into the tracker.
            asyncio.create_task(security.extract_findings(task_id, task_owner))

        # Notify if this run came from a schedule (honors its notify policy).
        asyncio.create_task(notifications.notify_task_finished(task_id))

    # ---- co-guard ----------------------------------------------------------

    GUARD_REPORT_CAP = 500  # live /guard/report events per run (the spool still has the rest)

    _GUARD_KEYS = (
        "event", "decision", "rule_ids", "grant_ids", "tool", "summary", "reason", "status", "request_id",
        "tool_use_id", "agent_id", "agent_type", "source", "ts",
        "hook_event", "error",  # guard_error records (a hook other than PreToolUse failed)
        "background", "what",  # Stop: background tasks still running; seen: what the call was
    )
    BACKGROUND_MAX = 10

    async def _prepare_guard(self, task_id, guard_project, workspace, sub_env, mcp, emit, owner_id=None):
        """Write the task's policy (0600) into a fresh CO_TASK_DIR, add the
        guard env and return (plugin dir, --setting-sources value or None,
        policy_inputs.PolicyInputs); (None, None, None) when the task must not
        start (it is then finished). The Phase 2 keys come from
        ``policy_inputs.gather``; what it skipped is logged as ``guard``
        events."""
        for k in guard_runtime.HOOK_DISABLING_ENV:  # bare/safe mode would skip plugin hooks
            sub_env.pop(k, None)
        root = guard_runtime.plugin_root()
        try:
            # cheap when unchanged; repairs a tampered or missing plugin (no pruning:
            # running tasks may still use an older version)
            await asyncio.to_thread(ensure_plugin, root, False)
            api_base = guard_runtime.guard_api_base()
            if settings.guard_verify_api and not await asyncio.to_thread(guard_runtime.verify_api_base, api_base):
                raise guard_runtime.GuardConfigError(
                    f"co-guard approval address not verified ({api_base} did not prove it is this server)")
            # once grants a previous attempt used (its spool is about to be wiped) stay spent
            await self._spend_spool_grants(task_id, strict=True)
            inputs = await self._gather_policy(task_id, guard_project, owner_id, mcp)
        except Exception as e:  # noqa: BLE001 - no policy, no task (fail closed)
            msg = f"co-guard could not be set up; task not started: {e}"
            await emit("error", {"message": msg})
            await self._finish(task_id, Status.FAILED, error=msg)
            return None, None, None
        config_dir = sub_env.get("CLAUDE_CONFIG_DIR")
        blockers = guard_runtime.hook_blockers(workspace, config_dir)
        if blockers["user"]:
            msg = "co-guard: the user's Claude settings switch hooks off (disableAllHooks); task not started"
            await emit("error", {"message": msg})
            await self._finish(task_id, Status.FAILED, error=msg)
            return None, None, None
        for ev in inputs.events:
            await emit("guard", ev)
        tdir = guard_runtime.make_task_dir(task_id)
        policy = guard_runtime.build_policy(
            task_id=task_id,
            project=guard_project,
            workspace=workspace,
            cwd=workspace,
            claude_config_dir=config_dir,
            mcp_tools=(mcp or {}).get("tool_policies"),
            extra=inputs.policy,
        )
        policy_file = guard_runtime.write_policy(task_id, policy)
        sub_env.update(
            {
                "CO_TASK_ID": task_id,
                "CO_API_BASE": api_base,
                "CO_POLICY_FILE": str(policy_file.resolve()),
                "CO_TASK_DIR": str(tdir.resolve()),
            }
        )
        sources = None
        if blockers["project"]:
            # Claude Code ORs disableAllHooks over its sources: a flag setting
            # cannot undo the repo's. Drop project/local settings for this run.
            sources = "user"
            await emit(
                "guard",
                {
                    "event": "settings_override",
                    "reason": "project settings switch hooks off; project and local settings "
                    "(incl. the repo's CLAUDE.md) are not loaded for this run",
                    "files": blockers["project"],
                },
            )
        return str(root.resolve()), sources, inputs

    @staticmethod
    async def _gather_policy(task_id, guard_project, owner_id, mcp):
        async with SessionLocal() as s:
            task = await s.get(Task, task_id)
            proj = await s.get(Project, guard_project.id) if guard_project is not None else None
            return await policy_inputs.gather(s, task=task, project=proj, owner_id=owner_id, mcp=mcp)

    # Ruling R21: co-guard's hook appends {"event":"PreToolUse","tool_use_id":..,
    # "seen":true} to the spool BEFORE it evaluates a call. Every tool_result in
    # the stream (main thread or subagent) must have its tool_use id in the
    # spool: a successful result without one means the call ran unchecked, so
    # the task is killed; an is_error result without one (schema/validation
    # rejections never reach hooks) is only logged. A call with no result and
    # no spool record after the grace window is killed too. Hook stream events
    # carry no tool_use id or plugin name (phase 1 spike), so they are not used.
    # Limit (same OS user, like R18): the spool proves the hook ran only while
    # hooks are on - once they are off, an agent could append forged `seen`
    # lines itself (CO_TASK_DIR is protected only by the hooks).

    async def _guard_violation(self, ctx, reason: str) -> None:
        g = ctx.get("guard")
        if not g or g["violation"]:
            return
        g["violation"] = reason
        await g["emit"]("guard", {"event": "watchdog", "decision": "killed", "reason": reason})
        self._signal_group(g["proc"], signal.SIGKILL)  # claude and every tool it started

    @staticmethod
    def _guard_read_spool(g) -> None:
        """Read the spool's new complete lines; collect the ids co-guard saw."""
        try:
            with open(g["spool"], "rb") as f:
                f.seek(g["offset"])
                data = f.read()
        except OSError:
            return
        end = data.rfind(b"\n")
        if end < 0:
            return
        g["offset"] += end + 1
        for line in data[: end + 1].splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("seen") is True and rec.get("tool_use_id"):
                g["seen"].add(str(rec["tool_use_id"]))

    def _guard_checked(self, g, tid: str) -> bool:
        if tid not in g["seen"]:
            self._guard_read_spool(g)
        return tid in g["seen"]

    # Ruling R24: calls that cannot run in parallel run one at a time, and their
    # hook (so the `seen` record) starts only when they start. A lane is one
    # assistant message (message.id) in one thread (parent_tool_use_id); a call's
    # grace timer starts only once every EARLIER call in its lane has a result.
    # The tool_result check stays definitive.

    @staticmethod
    def _guard_earlier_open(g, tid: str) -> bool:
        c = g["calls"].get(tid)
        lane = g["lanes"].get(c["lane"], []) if c else []
        return any(t not in g["done"] for t in lane[: lane.index(tid)]) if tid in lane else False

    @staticmethod
    def _guard_start_next(g, lane_key) -> None:
        for t in g["lanes"].get(lane_key, []):
            if t in g["done"]:
                continue
            c = g["calls"].get(t)
            if c is not None and c["start"] is None:
                c["start"] = time.monotonic()
            return  # only the first unfinished call can be running now

    async def _guard_watch(self, ctx) -> None:
        g = ctx["guard"]
        while not g["violation"]:
            await asyncio.sleep(min(1.0, settings.guard_hook_grace_seconds / 2))
            now = time.monotonic()
            for tid, c in list(g["calls"].items()):
                if c["start"] is None or c["checked"] or now - c["start"] <= settings.guard_hook_grace_seconds:
                    continue
                if self._guard_checked(g, tid):
                    c["checked"] = True  # seen; may be running long or waiting for a person
                    continue
                if self._guard_earlier_open(g, tid):
                    continue  # still waiting its turn
                await self._guard_violation(ctx, f"no co-guard check for {c['name']} call {tid}")
                return

    async def _guard_on_event(self, obj: dict, ctx) -> None:
        g = ctx.get("guard")
        if not g:
            return
        etype = obj.get("type")
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        content = msg.get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        if etype == "assistant":
            for block in blocks:
                if block.get("type") == "tool_use" and block.get("id"):
                    tid = str(block["id"])
                    key = (str(msg.get("id") or tid), str(obj.get("parent_tool_use_id") or ""))
                    g["lanes"].setdefault(key, []).append(tid)
                    g["calls"][tid] = {"name": str(block.get("name") or ""), "lane": key, "start": None,
                                       "checked": tid in g["seen"]}
                    if not self._guard_earlier_open(g, tid):
                        g["calls"][tid]["start"] = time.monotonic()
        elif etype == "user":
            for block in blocks:
                if block.get("type") != "tool_result":
                    continue
                tid = str(block.get("tool_use_id") or "")
                g["done"].add(tid)
                c = g["calls"].pop(tid, None)
                name = c["name"] if c else "a tool"
                if c is not None:
                    self._guard_start_next(g, c["lane"])
                if self._guard_checked(g, tid):
                    continue
                if block.get("is_error"):
                    await g["emit"]("guard", {"event": "unchecked_error", "tool_use_id": tid, "tool": name,
                                              "reason": "a call rejected before hooks ran (not executed)"})
                else:
                    await self._guard_violation(ctx, f"{name} call {tid} ran without a co-guard check")

    @classmethod
    def guard_payload(cls, rec: dict, secrets: list[str] | None = None) -> dict:
        """A hook record (spool line or /guard/report body) -> the `guard`
        event payload: known keys only, redacted FIRST (so no partial secret
        survives a cut, and both sources key alike), then bounded."""
        payload = redact_obj({k: rec[k] for k in cls._GUARD_KEYS if k in rec}, secrets or [])
        for k, v in list(payload.items()):
            if k in ("rule_ids", "grant_ids"):
                payload[k] = [str(x)[:64] for x in v][:guard_grants.MAX_GRANT_IDS] if isinstance(v, list) else []
            elif k == "background":
                payload[k] = [{"id": str(b.get("id"))[:200], "what": str(b.get("what"))[:200]}
                              for b in v if isinstance(b, dict)][:cls.BACKGROUND_MAX] if isinstance(v, list) else []
            elif isinstance(v, str):
                payload[k] = v[:2000]
            elif not isinstance(v, (int, float, bool)) and v is not None:
                payload[k] = str(v)[:2000]
        return payload

    @staticmethod
    def _guard_key(payload: dict) -> str:
        # The hook posts the record it spools, minus the spool's ts. An
        # "approved" record is reported with the stored request's fields (the
        # server never trusts the caller's), so it is keyed by its request id:
        # one approval answers exactly one call.
        if payload.get("decision") == "approved" and payload.get("request_id"):
            return json.dumps({"approved": payload["request_id"], "event": payload.get("event")})
        # A null and a missing key are the same record (the spool keeps agent_id: null).
        return json.dumps({k: v for k, v in payload.items() if k != "ts" and v is not None}, sort_keys=True,
                          default=str)

    async def report_guard(self, task_id: str, rec: dict) -> bool:
        """POST /guard/report: emit the hook's record now as a `guard` event of
        the live run. False (dropped) when the task has no live run here; the
        spool still carries the record for a run that is ending."""
        live = self._guard_live.get(task_id)
        if live is None:
            return False
        payload = self.guard_payload(rec, self._run_secrets.get(task_id))
        if not payload or sum(live["reported"].values()) >= self.GUARD_REPORT_CAP:
            return False
        live["reported"][self._guard_key(payload)] += 1
        await live["emit"]("guard", payload)
        return True

    async def _ingest_guard_spool(self, task_id: str, emit) -> None:
        """The hook's spool -> `guard` events (emit redacts), then drop the task
        dir. Records already reported live (POST /guard/report) are skipped."""
        live = self._guard_live.get(task_id)
        reported = live["reported"] if live else Counter()
        try:
            await self._spend_spool_grants(task_id)  # every granted line, durably (retries wipe the spool)
            for rec in guard_runtime.read_spool(task_id):
                if rec.get("seen") is True:
                    continue  # watchdog bookkeeping, not an event
                payload = self.guard_payload(rec, self._run_secrets.get(task_id))
                if not payload:
                    continue
                key = self._guard_key(payload)  # same form as report_guard's
                if reported[key] > 0:
                    reported[key] -= 1
                    continue
                if payload.get("decision") == "granted":
                    await self._count_grant_use(task_id, payload.get("grant_ids"))
                await emit("guard", payload)
        finally:
            guard_runtime.remove_task_dir(task_id)

    @staticmethod
    async def _spend_spool_grants(task_id: str, strict: bool = False) -> None:
        """Spend the once grants the spool's granted lines used. ``strict``
        (before a run): a failure stops the task (fail closed)."""
        try:
            async with SessionLocal() as s:
                await guard_grants.mark_spool_used(s, task_id)
        except Exception:  # noqa: BLE001 - never blocks a finish
            if strict:
                raise

    @staticmethod
    async def _count_grant_use(task_id: str, grant_ids) -> None:
        """An unreported granted record: one more use of each grant it names."""
        try:
            async with SessionLocal() as s:
                task = await s.get(Task, task_id)
                grants = await guard_grants.valid_use(s, task, grant_ids) if task is not None else None
                if grants:
                    await guard_grants.record_use(s, grants, count=True)
                    await s.commit()
        except Exception:  # noqa: BLE001
            pass

    async def _consume_stdout(self, proc, emit, result, ctx) -> None:
        # Read raw chunks and split on newlines ourselves. A single stream-json
        # line can be enormous (big PR diffs / tool results); asyncio's readline
        # caps line length and raises "Separator is not found" past the limit, so
        # we avoid readline entirely — chunked reads have no per-line limit.
        assert proc.stdout is not None
        buf = b""
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                nl = buf.find(b"\n")
                if nl == -1:
                    break
                raw, buf = buf[:nl], buf[nl + 1:]
                await self._process_stdout_line(raw, emit, result, ctx)
        if buf:
            await self._process_stdout_line(buf, emit, result, ctx)

    async def _process_stdout_line(self, raw: bytes, emit, result, ctx) -> None:
        line = raw.decode(errors="replace").strip()
        if not line:
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # Redact before the cut, so no secret prefix survives it.
            await emit("log", {"text": redact(line, ctx.get("secrets") or [])[:2000]})
            return
        await self._handle_stream_event(obj, emit, result, ctx)

    async def _handle_stream_event(self, obj: dict, emit, result, ctx) -> None:
        await self._guard_on_event(obj, ctx)
        etype = obj.get("type")
        if etype == "system":
            if str(obj.get("subtype") or "").startswith("hook_"):
                return  # hook activity: co-guard's records come from its spool / live report
            sid = obj.get("session_id")
            await emit("system", {"subtype": obj.get("subtype"), "session_id": sid})
            if sid:
                await self._store_session_id(ctx["task_id"], sid)
        elif etype == "assistant":
            actual = obj.get("message", {}).get("model")
            if actual and not ctx.get("model_stored"):
                ctx["model_stored"] = True
                await self._store_model_used(ctx["task_id"], actual)
            for block in obj.get("message", {}).get("content", []) or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    await emit("text_output", {"text": block["text"]})
                elif block.get("type") == "tool_use":
                    name = block.get("name")
                    await emit("tool_use", {"name": name, "input": block.get("input"),
                                            "id": block.get("id"), "parent": obj.get("parent_tool_use_id") or None})
                    if name and name.startswith("mcp__"):
                        await self._record_mcp_call(ctx, name, block.get("id"))
        elif etype == "user":
            content = obj.get("message", {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        await emit(
                            "tool_result",
                            {"content": _short(block.get("content"), ctx.get("secrets") or []),
                             "tool_use_id": block.get("tool_use_id"),
                             "is_error": bool(block.get("is_error")),
                             "parent": obj.get("parent_tool_use_id") or None},
                        )
                        if block.get("is_error"):
                            call_id = ctx["mcp_ids"].get(block.get("tool_use_id"))
                            if call_id is not None:
                                await self._mark_mcp_error(call_id)
        elif etype == "result":
            result["result_text"] = obj.get("result")
            result["num_turns"] = obj.get("num_turns")
            result["total_cost_usd"] = obj.get("total_cost_usd")
            result["duration_ms"] = obj.get("duration_ms")
            result["is_error"] = bool(obj.get("is_error"))
            usage = obj.get("usage") or {}
            # Count cache reads/creations as input so totals reflect real usage.
            result["input_tokens"] = (
                (usage.get("input_tokens") or 0)
                + (usage.get("cache_read_input_tokens") or 0)
                + (usage.get("cache_creation_input_tokens") or 0)
            ) or None
            result["output_tokens"] = usage.get("output_tokens")

    async def _store_model_used(self, task_id: str, model_used: str) -> None:
        async with SessionLocal() as s:
            task = await s.get(Task, task_id)
            if task is not None and task.model_used != model_used:
                task.model_used = model_used
                await s.commit()

    async def _store_session_id(self, task_id: str, session_id: str) -> None:
        async with SessionLocal() as s:
            task = await s.get(Task, task_id)
            if task is not None and task.session_id != session_id:
                task.session_id = session_id
                await s.commit()

    async def _record_mcp_call(self, ctx, name: str, tool_use_id) -> None:
        # name is mcp__<server>__<tool>
        rest = name[len("mcp__"):]
        server, _, tool = rest.partition("__")
        async with SessionLocal() as s:
            call = McpCall(task_id=ctx["task_id"], server=server, tool=tool or rest)
            s.add(call)
            await s.commit()
            call_id = call.id
        if tool_use_id:
            ctx["mcp_ids"][tool_use_id] = call_id

    async def _mark_mcp_error(self, call_id: int) -> None:
        async with SessionLocal() as s:
            call = await s.get(McpCall, call_id)
            if call is not None:
                call.is_error = True
                await s.commit()

    # ---- persistence -----------------------------------------------------

    async def _finish(
        self,
        task_id: str,
        status: str,
        *,
        exit_code: int | None = None,
        result_text: str | None = None,
        num_turns: int | None = None,
        total_cost_usd: float | None = None,
        duration_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error: str | None = None,
        artifacts: list[dict] | None = None,
        secrets: list[str] | None = None,
    ) -> None:
        # Agent output (and stderr) can carry the run's credentials: redact
        # before saving (error before its cut). Known token shapes are always
        # scrubbed, even without the run's secret list.
        if result_text is not None and not isinstance(result_text, str):
            # stream-json "result" is normally a string; never store (or skip
            # redacting) anything else raw.
            result_text = json.dumps(result_text, default=str)
        if result_text is not None:
            result_text = redact(result_text, secrets or [])
        if error:
            error = redact(error, secrets or [])
        context_store.cleanup(task_id)  # drop this task's knowledge snapshot
        await self._spend_spool_grants(task_id)  # a spool not ingested (error paths): its once grants stay spent
        guard_runtime.remove_task_dir(task_id)  # co-guard policy + spool (normally gone already)
        async with SessionLocal() as s:
            await auth.revoke_task_tokens(s, task_id)
            await s.commit()
        async with SessionLocal() as s:
            task = await s.get(Task, task_id)
            if task is None:
                return
            task.status = status
            task.completed_at = _now()
            if exit_code is not None:
                task.exit_code = exit_code
            if result_text is not None:
                task.result_text = result_text
            if num_turns is not None:
                task.num_turns = num_turns
            if total_cost_usd is not None:
                task.total_cost_usd = total_cost_usd
            if duration_ms is not None:
                task.duration_ms = duration_ms
            if input_tokens is not None:
                task.input_tokens = input_tokens
            if output_tokens is not None:
                task.output_tokens = output_tokens
            if error:
                task.error = error[:5000]
            for a in artifacts or []:
                s.add(
                    Artifact(
                        task_id=task_id,
                        filename=a["filename"],
                        rel_path=a["rel_path"],
                        size=a["size"],
                        mime=a["mime"],
                    )
                )
            await s.commit()
        # co-guard deny-and-resume: follow-ups deferred while this step was active
        try:
            await guard_grants.after_task_finished(task_id, submit=self.submit)
        except Exception:  # noqa: BLE001 - never breaks a finish
            pass
