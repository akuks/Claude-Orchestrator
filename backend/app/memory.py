"""Project memory + context assembly (Phase 3).

Two responsibilities:
  * assemble_context() — build a compact context preamble (project instructions,
    living memory, and the most relevant past-task summaries) within a token
    budget, prepended to a project task's prompt.
  * update_after_task() — after a project task finishes, run a cheap Claude call
    to write a one-paragraph task summary and fold it into the project's memory.
"""

import asyncio
import re
from datetime import datetime, timezone

from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .events import broker
from .models import Project, Task, TaskSummary

_WORD = re.compile(r"[a-z0-9]+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _words(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


async def _run_claude(prompt: str, model: str, cwd: str | None) -> str:
    """Run a one-shot non-streaming Claude call and return its text output."""
    try:
        proc = await asyncio.create_subprocess_exec(
            settings.claude_bin,
            "--print",
            "--model",
            model,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=cwd,
        )
    except (FileNotFoundError, OSError):
        return ""
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(prompt.encode()),
            timeout=settings.memory_call_timeout_seconds,
        )
        return out.decode(errors="replace").strip()
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return ""


async def _relevant_summaries(project_id: str, prompt: str, limit: int = 10) -> list[str]:
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(TaskSummary)
                .where(TaskSummary.project_id == project_id)
                .order_by(TaskSummary.created_at.desc())
                .limit(50)
            )
        ).scalars().all()
    if not rows:
        return []
    kw = _words(prompt)
    scored = []
    for i, r in enumerate(rows):  # rows are newest-first
        overlap = len(kw & _words(f"{r.title} {r.summary}"))
        recency = 1.0 / (1 + i)
        scored.append((overlap * 2 + recency, r.summary))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in scored[:limit]]


async def assemble_context(project_id: str, prompt: str) -> tuple[str | None, dict]:
    """Return (preamble, log). preamble is None if there's nothing to inject."""
    async with SessionLocal() as s:
        project = await s.get(Project, project_id)
    if project is None:
        return None, {}

    budget_chars = settings.context_budget_tokens * 4
    parts: list[str] = []
    used = 0
    log: dict = {"budget_tokens": settings.context_budget_tokens, "included": []}

    def _fits(block: str) -> bool:
        return used + len(block) <= budget_chars

    if project.instructions:
        block = f"## Project Instructions\n{project.instructions.strip()}\n"
        if _fits(block):
            parts.append(block)
            used += len(block)
            log["included"].append("instructions")

    if project.memory:
        block = f"## Project Memory\n{project.memory.strip()}\n"
        if _fits(block):
            parts.append(block)
            used += len(block)
            log["included"].append("memory")

    summaries = await _relevant_summaries(project_id, prompt)
    included = 0
    if summaries:
        header = "## Recent related work\n"
        lines: list[str] = []
        for smry in summaries:
            line = f"- {smry}\n"
            if used + len(header) + sum(len(x) for x in lines) + len(line) > budget_chars:
                break
            lines.append(line)
            included += 1
        if lines:
            parts.append(header + "".join(lines))
            used += len(header) + sum(len(x) for x in lines)
            log["included"].append("summaries")
    log["summaries_included"] = included

    if not parts:
        return None, log

    preamble = (
        "# Project Context (auto-assembled by Claude Orchestrator)\n"
        "# Reference only — the actual task follows the divider.\n\n"
        + "\n".join(parts)
    )
    log["approx_tokens"] = used // 4
    return preamble, log


async def update_after_task(task_id: str) -> None:
    """Post-task: write a summary and fold it into the project's living memory."""
    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        if task is None or not task.project_id:
            return
        project = await s.get(Project, task.project_id)
        if project is None or not project.memory_enabled:
            return
        prompt, result_text, title = task.prompt, task.result_text or "", task.title
        pid, cwd, old_memory = project.id, project.directory, project.memory or ""

    model = settings.memory_model

    summary_prompt = (
        "In 1-2 sentences, summarize what this task accomplished — specific and "
        "factual. Output only the summary.\n\n"
        f"Task: {prompt}\n\nResult: {result_text[:4000]}"
    )
    summary = (await _run_claude(summary_prompt, model, cwd)).strip()
    summary = summary[:600] or (result_text[:300] or prompt[:200])

    mem_prompt = (
        "You maintain a concise living memory for a software project. Keep it under "
        f"{settings.memory_max_chars} characters. Capture durable facts only: what "
        "exists, key decisions, known issues, resolved issues. Drop stale detail. "
        "Output ONLY the updated memory as markdown.\n\n"
        f"=== CURRENT MEMORY ===\n{old_memory or '(empty)'}\n\n"
        f"=== JUST COMPLETED ===\n{summary}\n\n=== UPDATED MEMORY ==="
    )
    new_memory = (await _run_claude(mem_prompt, model, cwd)).strip()

    async with SessionLocal() as s:
        project = await s.get(Project, pid)
        if project is None:
            return
        s.add(
            TaskSummary(task_id=task_id, project_id=pid, title=title, summary=summary)
        )
        if new_memory:
            project.memory_prev = project.memory
            project.memory = new_memory[: settings.memory_max_chars]
            project.memory_updated_at = _now()
        await s.commit()

    await broker.publish(
        task_id, {"seq": 0, "type": "memory_updated", "payload": {"summary": summary}}
    )


async def rebuild_memory(project_id: str) -> str | None:
    """Regenerate a project's memory from scratch out of its task summaries."""
    async with SessionLocal() as s:
        project = await s.get(Project, project_id)
        if project is None:
            return None
        cwd = project.directory
        rows = (
            await s.execute(
                select(TaskSummary)
                .where(TaskSummary.project_id == project_id)
                .order_by(TaskSummary.created_at.asc())
            )
        ).scalars().all()

    history = "\n".join(f"- {r.summary}" for r in rows) or "(no task history yet)"
    prompt = (
        "Build a concise project memory in markdown, under "
        f"{settings.memory_max_chars} characters, from this task history. Capture "
        "durable facts, key decisions, and known issues. Output ONLY the memory.\n\n"
        f"=== TASK HISTORY ===\n{history}"
    )
    new_memory = (await _run_claude(prompt, settings.memory_model, cwd)).strip()

    async with SessionLocal() as s:
        project = await s.get(Project, project_id)
        if project is not None and new_memory:
            project.memory_prev = project.memory
            project.memory = new_memory[: settings.memory_max_chars]
            project.memory_updated_at = _now()
            await s.commit()
    return new_memory
