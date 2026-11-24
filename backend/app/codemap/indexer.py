"""The code map index job: runs the configured index command (argv, never a shell) in the gate-style clean
environment, with a timeout that kills the process group, a capped private log, one job per project and at most
`code_map_max_jobs` at once. The row ends `ok` (sha = HEAD read before the run) or `failed` with back-off.

The command never runs inside the project folder: `npx` would read that folder's `.npmrc` and prefer its own
`node_modules`, so a hostile repository could swap the indexer for its own code. It runs from a private
folder (`workspaces/_codemap/run/<project_id>/`, holding only inert marker files that stop npm and uv from
looking further up) with npm's user/global config switched off, and the project's
real absolute path is only the last argument."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select, update

from .. import redaction
from ..config import settings
from ..database import SessionLocal
from ..gates import executor as gx
from ..models import Project, ProjectIndex
from ..skillpacks.update import current_sha_fast

log = logging.getLogger(__name__)

LOG_CAP = 1024 * 1024
MAX_BACKOFF_MIN = 60
# a folder with no commit to compare (not a git folder, an unborn branch, a worktree whose `.git` is a file):
# the automatic refresh re-runs at most this often; a manual refresh always runs
NO_COMMIT_REFRESH = timedelta(days=1)
# the failure reasons the job stores (status.failure_kind turns them into plain words)
ERR_TIMEOUT = "Timed out after"
ERR_START = "could not start the index command"
ERR_EXIT = "The index command exited with code"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_inflight: set[str] = set()  # projects with a job claimed (running or waiting for a slot)
_tasks: set[asyncio.Task] = set()
_started: set[str] = set()  # jobs that wrote their `running` row
_sem: tuple[object, asyncio.Semaphore] | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def codemap_root() -> Path:
    return settings.workspaces_dir / "_codemap"


def log_path(project_id: str) -> Path:
    if not _SAFE_ID.match(str(project_id)):
        raise ValueError("bad project id")
    return codemap_root() / f"{project_id}.log"


def run_dir(project_id: str) -> Path:
    """The neutral, private folder the index command runs from. It holds only
    inert marker files (mcp_manager.place_stoppers), so npm/uv do not climb out
    of it to a node_modules / package.json / .npmrc / uv.toml in workspaces/."""
    if not _SAFE_ID.match(str(project_id)):
        raise ValueError("bad project id")
    root = codemap_root()
    d = root / "run" / project_id
    for x in (root, root / "run", d):
        x.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(x, 0o700)
    from ..mcp_manager import place_stoppers

    place_stoppers(d)
    return d


def home_dir() -> Path:
    """The index jobs' own HOME (npm's cache lives here), apart from the gates' shared one."""
    root = codemap_root()
    home = root / "_home"
    for x in (root, home):
        x.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(x, 0o700)
    return home


def index_env(project) -> dict[str, str]:
    """The gate env (no credentials; the project's own env values are passed) with its own HOME, npm's
    user/global config off and the repo-local git config's command hooks switched off for every git the tool
    runs (the flags `gitsafe` passes)."""
    env = gx.gate_env(project)
    env.pop("INIT_CWD", None)
    env.pop("PWD", None)
    home = home_dir()
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "core.hooksPath",
        "GIT_CONFIG_VALUE_1": "/dev/null",
        "npm_config_userconfig": "/dev/null",
        "npm_config_globalconfig": "/dev/null",
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        "npm_config_fund": "false",
        "npm_config_audit": "false",
    })
    return env


def inflight(project_id: str) -> bool:
    return project_id in _inflight


def _semaphore() -> asyncio.Semaphore:
    global _sem
    loop = asyncio.get_running_loop()
    if _sem is None or _sem[0] is not loop:
        _sem = (loop, asyncio.Semaphore(max(1, settings.code_map_max_jobs)))
    return _sem[1]


def _claim(project_id: str) -> bool:
    if project_id in _inflight:
        return False
    _inflight.add(project_id)
    return True


def _scrub(text: str, project) -> str:
    text = redaction.redact(text, [])
    return redaction.mask_values(text, redaction.env_values([getattr(project, "env", None)]))


def backoff_minutes(fail_count: int) -> int:
    return min(2 ** max(0, min(fail_count, 10)), MAX_BACKOFF_MIN)


async def _run_argv(argv, *, cwd, env, path: Path, timeout_s: float) -> tuple[str, int | None]:
    """Run argv (no shell) to a capped 0600 log. Returns (ok | failed | timed_out | error:<why>, exit code)."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    proc = None
    try:
        os.fchmod(fd, 0o600)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(cwd), env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        except (OSError, ValueError) as e:
            return f"error:{ERR_START} ({type(e).__name__})", None
        state: dict = {"written": 0, "error": None}
        pump = asyncio.ensure_future(gx._pump(proc, fd, state, LOG_CAP))
        outcome = "failed"
        try:
            await asyncio.wait_for(proc.wait(), timeout_s)
            outcome = "ok" if proc.returncode == 0 else "failed"
        except asyncio.TimeoutError:
            outcome = "timed_out"
            await gx.kill_group(proc)
        except asyncio.CancelledError:
            await asyncio.shield(gx.kill_group(proc))
            pump.cancel()
            raise
        try:
            await asyncio.wait_for(pump, 2.0 if outcome == "timed_out" else 5.0)
        except asyncio.TimeoutError:
            pump.cancel()
        except asyncio.CancelledError:
            pump.cancel()
            await asyncio.shield(gx.kill_group(proc))
            raise
        # leftover background children of the command must not outlive it
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError):
            pass
        return outcome, (None if outcome == "timed_out" else proc.returncode)
    finally:
        os.close(fd)


def _tail_line(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            f.seek(max(0, size - 4096))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


async def _get_row(s, project_id: str) -> ProjectIndex:
    row = await s.get(ProjectIndex, project_id)
    if row is None:
        row = ProjectIndex(project_id=project_id, status="never", fail_count=0)
        s.add(row)
    return row


async def _job(project_id: str) -> str:
    """The claimed job (the caller holds the in-flight claim). Never raises except on cancellation."""
    try:
        async with _semaphore():
            return await _job_locked(project_id)
    except asyncio.CancelledError:
        if project_id in _started:  # a job still waiting for a slot leaves the row as it was
            await _finish_row(project_id, "failed", "Interrupted (the server is stopping)", None)
        raise
    except Exception as e:  # noqa: BLE001 - a job never takes the server down
        log.warning("codemap: job for %s crashed: %s", project_id, e)
        await _finish_row(project_id, "failed", f"The index job crashed ({type(e).__name__})", None)
        return "failed"
    finally:
        _started.discard(project_id)
        _inflight.discard(project_id)


async def _finish_row(project_id: str, status: str, error: str | None, sha: str | None) -> None:
    try:
        async with SessionLocal() as s:
            row = await _get_row(s, project_id)
            now = _now()
            row.started_at = None
            if status == "ok":
                row.status, row.sha, row.indexed_at = "ok", sha, now
                row.error, row.fail_count, row.next_try_at = None, 0, None
            else:
                row.status = "failed"
                row.error = (error or "failed")[:500]
                row.fail_count = (row.fail_count or 0) + 1
                row.next_try_at = now + timedelta(minutes=backoff_minutes(row.fail_count))
            await s.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("codemap: could not record the result for %s: %s", project_id, e)


async def _job_locked(project_id: str) -> str:
    async with SessionLocal() as s:
        project = await s.get(Project, project_id)
        if project is None:
            return "failed"
        directory = project.directory
        env_project = SimpleNamespace(env=dict(project.env or {}))
        if not directory or not Path(directory).is_dir():
            await _finish_row(project_id, "failed", "The project folder is missing", None)
            return "failed"
        sha_before = current_sha_fast(directory)  # HEAD before the run: what the index is built from
        row = await _get_row(s, project_id)
        row.status, row.started_at = "running", _now()
        await s.commit()
        _started.add(project_id)
    cwd = run_dir(project_id)
    path = log_path(project_id)
    argv = [*settings.code_map_index_command, os.path.realpath(directory)]
    outcome, code = await _run_argv(
        argv, cwd=cwd, env=index_env(env_project), path=path,
        timeout_s=settings.code_map_timeout_seconds)
    if outcome == "ok":
        await _finish_row(project_id, "ok", None, sha_before)
        return "ok"
    if outcome == "timed_out":
        err = f"{ERR_TIMEOUT} {max(1, round(settings.code_map_timeout_seconds / 60))} minutes"
    elif outcome.startswith("error:"):
        err = outcome[6:]
    else:
        last = _scrub(_tail_line(path), env_project)[:300]
        err = f"{ERR_EXIT} {code}" + (f": {last}" if last else "")
    await _finish_row(project_id, "failed", _scrub(err, env_project), None)
    return "failed"


async def _current(project_id: str) -> str:
    async with SessionLocal() as s:
        row = await s.get(ProjectIndex, project_id)
        return row.status if row is not None else "never"


def _in_backoff(row: ProjectIndex | None) -> bool:
    nxt = _aware(row.next_try_at) if row is not None else None
    return nxt is not None and _now() < nxt


async def refresh(project_id: str, *, manual: bool = False) -> str:
    """Index now and wait for it; returns the resulting status. Returns `running` at once when a job is already
    in flight for the project. A non-manual refresh inside the back-off window does nothing."""
    if not _claim(project_id):
        return "running"
    try:
        if not manual:
            async with SessionLocal() as s:
                row = await s.get(ProjectIndex, project_id)
            if _in_backoff(row):
                _inflight.discard(project_id)
                return row.status
    except BaseException:
        _inflight.discard(project_id)
        raise
    return await _job(project_id)


def start(project_id: str) -> bool:
    """Start the job as a background task (the claim is taken here, so two starts run one job). It never checks
    back-off: callers (`maybe_refresh`, the scan) decide that."""
    if not _claim(project_id):
        return False
    t = asyncio.ensure_future(_job(project_id))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return True


async def wait_idle() -> None:
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def shutdown() -> None:
    for t in list(_tasks):
        t.cancel()
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def _due(project: Project, row: ProjectIndex | None, registered: bool) -> bool:
    """Whether the automatic refresh should index `project` now. Never without a registered code map server.
    With no commit to compare (HEAD unreadable), an `ok` index is re-run at most once per NO_COMMIT_REFRESH."""
    if not registered or not project.code_map or project.archived or not project.directory:
        return False
    if project.id in _inflight or (row is not None and row.status == "running" and not _stale(row)):
        return False
    head = current_sha_fast(project.directory)
    if row is not None and row.status == "ok":
        if head is not None and row.sha == head:
            return False
        if head is None:
            built = _aware(row.indexed_at)
            if built is not None and _now() - built < NO_COMMIT_REFRESH:
                return False
    return not _in_backoff(row)


async def _registered(s, project) -> bool:
    from . import status  # lazy: status imports this module

    return await status.server_registered(s, project)


def _stale(row: ProjectIndex) -> bool:
    started = _aware(row.started_at)
    return started is None or (_now() - started).total_seconds() > settings.code_map_stale_seconds


async def maybe_refresh(project_id: str) -> None:
    """After a task: start a refresh when the project is switched on, a code map server is registered for it,
    HEAD differs from the index and the back-off has passed. Never raises."""
    try:
        async with SessionLocal() as s:
            project = await s.get(Project, project_id)
            if project is None:
                return
            row = await s.get(ProjectIndex, project_id)
            registered = await _registered(s, project)
        if _due(project, row, registered):
            start(project_id)
    except Exception as e:  # noqa: BLE001
        log.warning("codemap: maybe_refresh failed for %s: %s", project_id, e)


async def reset_stale(*, all_running: bool = False) -> int:
    """`running` rows with no live job (a restart) become `failed`. At boot nothing is live: all of them.
    The UPDATE only touches a row that is still `running`, so a job that just finished `ok` is never overwritten."""
    n = 0
    async with SessionLocal() as s:
        rows = (await s.execute(select(ProjectIndex).where(ProjectIndex.status == "running"))).scalars().all()
        todo = [(r.project_id, (r.fail_count or 0) + 1) for r in rows
                if r.project_id not in _inflight and (all_running or _stale(r))]
        for pid, fails in todo:
            if pid in _inflight:
                continue
            res = await s.execute(
                update(ProjectIndex).where(ProjectIndex.project_id == pid, ProjectIndex.status == "running")
                .values(status="failed", error="Interrupted (the server restarted)", fail_count=fails,
                        next_try_at=_now() + timedelta(minutes=backoff_minutes(fails)), started_at=None))
            n += res.rowcount or 0
        await s.commit()
    return n


async def scan_once(*, startup: bool = False) -> None:
    """The scheduler pass: reset stale `running` rows, then refresh every due switched-on project that has a
    code map server registered."""
    from . import status  # lazy: status imports this module

    await reset_stale(all_running=startup)
    async with SessionLocal() as s:
        projects = (await s.execute(select(Project).where(Project.code_map.is_(True)))).scalars().all()
        rows = {r.project_id: r for r in (await s.execute(select(ProjectIndex))).scalars().all()}
        servers = await status.enabled_servers(s)
    for p in projects:
        try:
            if _due(p, rows.get(p.id), status.registered_for(servers, p)):
                start(p.id)
        except Exception as e:  # noqa: BLE001
            log.warning("codemap: scan failed for %s: %s", p.id, e)
