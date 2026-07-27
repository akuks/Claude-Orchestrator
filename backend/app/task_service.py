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
) -> Task:
    """Create a Task row (flushed, not committed) in the given session."""
    project_obj = await s.get(Project, project_id) if project_id else None
    resolved_model = model or (project_obj.default_model if project_obj else "")

    task = Task(
        title=title or default_title(prompt),
        prompt=prompt,
        project=(project_obj.name if project_obj else None),
        project_id=project_id,
        priority=priority,
        tags=tags or [],
        model=resolved_model,
        max_turns=max_turns,
        status=Status.QUEUED,
        schedule_id=schedule_id,
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
