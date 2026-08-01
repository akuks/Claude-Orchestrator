"""Shared task construction used by the REST API and the scheduler.

Centralizes project resolution, model defaulting, workspace setup, and thread
root assignment so the create endpoint and scheduled runs stay in sync.
"""

import base64
import os
from pathlib import Path

from .config import settings
from .constants import Status
from .models import Project, Task


def default_title(prompt: str) -> str:
    line = prompt.strip().splitlines()[0] if prompt.strip() else "Untitled task"
    return line[:80]


# Patterns that make an action hard to reverse / high-impact.
_CRITICAL_PATTERNS = (
    "merge",
    "rm -rf",
    "drop table",
    "force push",
    "force-push",
    "--force",
    "push --force",
    "reset --hard",
    "deploy",
    "revert",
    "delete branch",
    "git push",
)


def classify_risk(prompt: str, project_id: str | None) -> str:
    p = (prompt or "").lower()
    if any(k in p for k in _CRITICAL_PATTERNS):
        return "critical"
    # A project task acts on a real repo/directory → at least a warning.
    if project_id:
        return "warning"
    return "info"


async def build_task(
    s,
    *,
    prompt: str,
    title: str | None = None,
    project_id: str | None = None,
    model: str | None = None,
    max_turns: int = 25,
    priority: str = "normal",
    tags: list | None = None,
    claude_md: str | None = None,
    input_files: list | None = None,
    schedule_id: str | None = None,
    requires_approval: bool = False,
) -> Task:
    """Create a Task row (flushed, not committed) in the given session.

    When requires_approval is set, the task starts in `awaiting_approval` and is
    NOT submitted to the worker until a human approves it.
    """
    project_obj = await s.get(Project, project_id) if project_id else None
    resolved_model = model or (project_obj.default_model if project_obj else "")

    risk = classify_risk(prompt, project_id)
    # Auto-gate critical-risk work so it can't run unattended (config-controlled).
    gated = requires_approval or (settings.gate_critical_approval and risk == "critical")

    task = Task(
        title=title or default_title(prompt),
        prompt=prompt,
        project=(project_obj.name if project_obj else None),
        project_id=project_id,
        priority=priority,
        tags=tags or [],
        model=resolved_model,
        max_turns=max_turns,
        status=Status.AWAITING_APPROVAL if gated else Status.QUEUED,
        schedule_id=schedule_id,
        requires_approval=gated,
        risk=risk,
    )
    s.add(task)
    await s.flush()  # assign id
    task.root_id = task.id

    if project_obj:
        task.workspace_dir = project_obj.directory  # run in the repo
    else:
        ws = settings.workspaces_dir / task.id
        ws.mkdir(parents=True, exist_ok=True)
        if claude_md:
            (ws / "CLAUDE.md").write_text(claude_md)
        for f in input_files or []:
            safe = os.path.basename(f["name"])
            if safe:
                (ws / safe).write_bytes(base64.b64decode(f["content_base64"]))
        task.workspace_dir = str(ws)
    return task
