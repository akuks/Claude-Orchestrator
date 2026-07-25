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

from sqlalchemy import select

from .config import settings
from .constants import Priority, Status
from .database import SessionLocal
from .events import broker
from .models import Artifact, Task, TaskEvent

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
        seq = 0

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
            workspace = Path(task.workspace_dir or (settings.workspaces_dir / task_id))
            await s.commit()

        workspace.mkdir(parents=True, exist_ok=True)
        await emit("started", {"model": model, "max_turns": max_turns})

        before = _snapshot(workspace)

        cmd = [
            settings.claude_bin,
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--model",
            model,
            "--max-turns",
            str(max_turns),
            "--permission-mode",
            settings.claude_permission_mode,
        ]

        result = {
            "result_text": None,
            "num_turns": None,
            "total_cost_usd": None,
            "duration_ms": None,
            "is_error": False,
        }
        stderr_buf: list[str] = []

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workspace),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
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
            async for line in proc.stderr:
                stderr_buf.append(line.decode(errors="replace"))

        stderr_task = asyncio.create_task(read_stderr())

        try:
            await asyncio.wait_for(
                self._consume_stdout(proc, emit, result),
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

        artifacts = _collect_artifacts(workspace, before)
        failed = result["is_error"] or (exit_code not in (0, None))
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
            error=("".join(stderr_buf).strip() or None) if failed else None,
            artifacts=artifacts,
        )

    async def _consume_stdout(self, proc, emit, result) -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                await emit("log", {"text": line})
                continue
            await self._handle_stream_event(obj, emit, result)

    async def _handle_stream_event(self, obj: dict, emit, result) -> None:
        etype = obj.get("type")
        if etype == "system":
            await emit(
                "system",
                {"subtype": obj.get("subtype"), "session_id": obj.get("session_id")},
            )
        elif etype == "assistant":
            for block in obj.get("message", {}).get("content", []) or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    await emit("text_output", {"text": block["text"]})
                elif block.get("type") == "tool_use":
                    await emit(
                        "tool_use",
                        {"name": block.get("name"), "input": block.get("input")},
                    )
        elif etype == "user":
            content = obj.get("message", {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        await emit("tool_result", {"content": _short(block.get("content"))})
        elif etype == "result":
            result["result_text"] = obj.get("result")
            result["num_turns"] = obj.get("num_turns")
            result["total_cost_usd"] = obj.get("total_cost_usd")
            result["duration_ms"] = obj.get("duration_ms")
            result["is_error"] = bool(obj.get("is_error"))

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
