"""The code map index job: runs the configured index command (argv, never a shell) in the gate-style clean
environment, with a timeout that kills the process group, a capped private log, one job per project and at most
`code_map_max_jobs` at once. The row ends `ok` (sha = HEAD read before the run) or `failed` with back-off."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from .. import redaction
from ..config import settings
from ..database import SessionLocal
from ..gates import executor as gx
from ..models import Project, ProjectIndex
from ..skillpacks.update import current_sha_fast

log = logging.getLogger(__name__)

LOG_CAP = 1024 * 1024
MAX_BACKOFF_MIN = 60
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_inflight: set[str] = set()  # projects with a job claimed (running or waiting for a slot)
_tasks: set[asyncio.Task] = set()
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
            return f"error:could not start the index command ({type(e).__name__})", None
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
        await _finish_row(project_id, "failed", "Interrupted (the server is stopping)", None)
        raise
    except Exception as e:  # noqa: BLE001 - a job never takes the server down
        log.warning("codemap: job for %s crashed: %s", project_id, e)
        await _finish_row(project_id, "failed", f"The index job crashed ({type(e).__name__})", None)
        return "failed"
    finally:
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
    root = codemap_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    path = log_path(project_id)
    argv = [*settings.code_map_index_command, str(directory)]
    outcome, code = await _run_argv(
        argv, cwd=directory, env=gx.gate_env(env_project), path=path,
        timeout_s=settings.code_map_timeout_seconds)
    if outcome == "ok":
        await _finish_row(project_id, "ok", None, sha_before)
        return "ok"
    if outcome == "timed_out":
        err = f"Timed out after {max(1, round(settings.code_map_timeout_seconds / 60))} minutes"
    elif outcome.startswith("error:"):
        err = outcome[6:]
    else:
        last = _scrub(_tail_line(path), env_project)[:300]
        err = f"The index command exited with code {code}" + (f": {last}" if last else "")
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


def start(project_id: str, *, manual: bool = False) -> bool:
    """Start the job as a background task (the claim is taken here, so two starts run one job)."""
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


def _due(project: Project, row: ProjectIndex | None) -> bool:
    if not project.code_map or project.archived or not project.directory:
        return False
    if project.id in _inflight or (row is not None and row.status == "running" and not _stale(row)):
        return False
    head = current_sha_fast(project.directory)
    if head is None:
        return False
    if row is not None and row.sha == head and row.status == "ok":
        return False
    return not _in_backoff(row)


def _stale(row: ProjectIndex) -> bool:
    started = _aware(row.started_at)
    return started is None or (_now() - started).total_seconds() > settings.code_map_stale_seconds


async def maybe_refresh(project_id: str) -> None:
    """After a task: start a refresh when the project is switched on, HEAD differs from the index and the
    back-off has passed. Never raises."""
    try:
        async with SessionLocal() as s:
            project = await s.get(Project, project_id)
            if project is None:
                return
            row = await s.get(ProjectIndex, project_id)
        if _due(project, row):
            start(project_id)
    except Exception as e:  # noqa: BLE001
        log.warning("codemap: maybe_refresh failed for %s: %s", project_id, e)


async def reset_stale(*, all_running: bool = False) -> int:
    """`running` rows with no live job (a restart) become `failed`. At boot nothing is live: all of them."""
    n = 0
    async with SessionLocal() as s:
        rows = (await s.execute(select(ProjectIndex).where(ProjectIndex.status == "running"))).scalars().all()
        for row in rows:
            if row.project_id in _inflight or not (all_running or _stale(row)):
                continue
            row.status = "failed"
            row.error = "Interrupted (the server restarted)"
            row.fail_count = (row.fail_count or 0) + 1
            row.next_try_at = _now() + timedelta(minutes=backoff_minutes(row.fail_count))
            row.started_at = None
            n += 1
        await s.commit()
    return n


async def scan_once(*, startup: bool = False) -> None:
    """The scheduler pass: reset stale `running` rows, then refresh every due switched-on project."""
    await reset_stale(all_running=startup)
    async with SessionLocal() as s:
        projects = (await s.execute(select(Project).where(Project.code_map.is_(True)))).scalars().all()
        rows = {r.project_id: r for r in (await s.execute(select(ProjectIndex))).scalars().all()}
    for p in projects:
        try:
            if _due(p, rows.get(p.id)):
                start(p.id)
        except Exception as e:  # noqa: BLE001
            log.warning("codemap: scan failed for %s: %s", p.id, e)
