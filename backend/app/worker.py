"""Claude Code worker manager.

An in-process asyncio pool that dispatches queued tasks respecting priority and
a concurrency limit. Each task spawns the Claude Code CLI in an isolated
workspace, streams its stream-json output as normalized events, and records
results + output artifacts. No external broker/queue needed for Phase 1.
"""

import asyncio
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from . import mcp_manager, memory, notifications, security
from .config import settings
from .constants import Priority, Status
from .database import SessionLocal
from .events import broker
from .models import Artifact, McpCall, Task, TaskEvent

_MAX_TOOL_RESULT_CHARS = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def _short(value) -> str:
    if isinstance(value, list):
        parts = []
        for b in value:
            if isinstance(b, dict):
                parts.append(b.get("text") or b.get("content") or "")
            else:
                parts.append(str(b))
        value = " ".join(str(p) for p in parts)
    text = str(value)
    return text[:_MAX_TOOL_RESULT_CHARS]


class WorkerManager:
    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._sem = asyncio.Semaphore(settings.worker_concurrency)
        self._running: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()
        self._dispatcher: asyncio.Task | None = None

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        self._dispatcher = asyncio.create_task(self._dispatch_loop())
        await self._requeue_pending()

    async def stop(self) -> None:
        if self._dispatcher:
            self._dispatcher.cancel()
        for proc in list(self._running.values()):
            try:
                proc.terminate()
            except ProcessLookupError:
                pass

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
            try:
                proc.terminate()
            except ProcessLookupError:
                pass

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
            await self._finish(task_id, Status.FAILED, error=f"worker error: {exc}")
        finally:
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

        async def emit(etype: str, payload: dict) -> None:
            nonlocal seq
            seq += 1
            async with SessionLocal() as s:
                s.add(TaskEvent(task_id=task_id, seq=seq, type=etype, payload=payload))
                await s.commit()
            await broker.publish(task_id, {"seq": seq, "type": etype, "payload": payload})

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
                return
            task.status = Status.RUNNING
            task.started_at = _now()
            prompt = task.prompt
            model = task.model
            max_turns = task.max_turns
            max_budget_usd = task.max_budget_usd
            project = task.project
            project_id = task.project_id
            resume_session_id = task.resume_session_id
            attempt = task.attempt
            task_priority = task.priority
            task_created_at = task.created_at
            system_prompt = task.system_prompt
            workspace = Path(task.workspace_dir or (settings.workspaces_dir / task_id))
            await s.commit()

        workspace.mkdir(parents=True, exist_ok=True)
        await emit("started", {"model": model, "max_turns": max_turns})

        # Project tasks: prepend assembled context (instructions + memory + relevant
        # past-task summaries) so Claude starts with the project's accumulated state.
        if project_id and not resume_session_id:
            preamble, ctx_log = await memory.assemble_context(project_id, prompt)
            if preamble:
                prompt = f"{preamble}\n\n---\n\n# Task\n\n{prompt}"
                await emit("context", ctx_log)

        # Resolve MCP servers for this task and write a per-task config BEFORE the
        # baseline snapshot so the generated config file isn't logged as an artifact.
        mcp = await mcp_manager.prepare_for_task(project, workspace)

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
        ]
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
        if mcp:
            cmd += ["--mcp-config", mcp["config_path"], "--strict-mcp-config"]
            if mcp["allowed"]:
                cmd += ["--allowedTools", *mcp["allowed"]]
            if mcp["disallowed"]:
                cmd += ["--disallowedTools", *mcp["disallowed"]]
            await emit(
                "mcp",
                {
                    "servers": mcp["server_names"],
                    "auto_approved": mcp["allowed"],
                    "blocked": mcp["disallowed"],
                },
            )

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

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workspace),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Raise the per-line buffer limit; stream-json events can be large.
                limit=settings.stream_buffer_limit_bytes,
            )
        except FileNotFoundError:
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

        ctx = {"task_id": task_id, "mcp_ids": {}}
        try:
            await asyncio.wait_for(
                self._consume_stdout(proc, emit, result, ctx),
                timeout=settings.task_timeout_seconds,
            )
            await proc.wait()
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            await stderr_task
            self._running.pop(task_id, None)
            await emit("error", {"message": "Task exceeded time limit"})
            await self._finish(task_id, Status.FAILED, error="Task exceeded time limit")
            return
        finally:
            await stderr_task

        self._running.pop(task_id, None)
        exit_code = proc.returncode

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
        )

        # Fold the completed work into the project's living memory (background,
        # non-blocking — it makes its own cheap Claude call).
        if project_id and not failed:
            asyncio.create_task(memory.update_after_task(task_id))
            # Security review → extract structured findings into the tracker.
            asyncio.create_task(security.extract_findings(task_id))

        # Notify if this run came from a schedule (honors its notify policy).
        asyncio.create_task(notifications.notify_task_finished(task_id))

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
            await emit("log", {"text": line[:2000]})
            return
        await self._handle_stream_event(obj, emit, result, ctx)

    async def _handle_stream_event(self, obj: dict, emit, result, ctx) -> None:
        etype = obj.get("type")
        if etype == "system":
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
                    await emit("tool_use", {"name": name, "input": block.get("input")})
                    if name and name.startswith("mcp__"):
                        await self._record_mcp_call(ctx, name, block.get("id"))
        elif etype == "user":
            content = obj.get("message", {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        await emit("tool_result", {"content": _short(block.get("content"))})
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
    ) -> None:
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
