"""Context Store — curated knowledge docs loaded into agent tasks in tiers.

Inspired by OpenViking's tiered loading, built first-party:
  * L0 (abstract): a one-line summary per doc. The index of all applicable
    abstracts is injected into the task prompt — cheap, always present.
  * L2 (body): the full doc, written to a per-task snapshot directory outside
    the agent's working dir. The agent opens a doc with Read only when the
    abstract says it is relevant — so tokens are spent on what's needed.

Scoping: global docs apply to every task; project docs apply to that project's
tasks and override a global doc at the same path.
"""

import re
import shutil
from pathlib import Path, PurePosixPath

from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .models import ContextDoc

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ABSTRACT_MAX = 300


def normalize_path(raw: str) -> str:
    """Validate and normalize a doc path to a safe relative POSIX path ending in
    .md. Raises ValueError for absolute paths, traversal, or odd characters."""
    p = (raw or "").strip().replace("\\", "/").strip("/")
    if not p:
        raise ValueError("path is required")
    parts = PurePosixPath(p).parts
    if len(parts) > 6:
        raise ValueError("path is too deep (max 6 levels)")
    for seg in parts:
        if seg in (".", "..") or not _SEGMENT.match(seg):
            raise ValueError(
                f"invalid path segment {seg!r}: use letters, digits, '.', '_' or '-'"
            )
    if not p.endswith(".md"):
        p += ".md"
    return p


def derive_title(path: str) -> str:
    stem = PurePosixPath(path).stem
    return re.sub(r"[-_]+", " ", stem).strip().capitalize() or path


def derive_abstract(body: str) -> str:
    """First meaningful line/paragraph of the body, trimmed (no LLM call)."""
    for line in (body or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:ABSTRACT_MAX]
    return ""


async def docs_for(project_id: str | None) -> list[ContextDoc]:
    """Enabled docs that apply to a task: global + its project's, with project
    docs overriding a global doc at the same path. Sorted by path."""
    async with SessionLocal() as s:
        rows = (
            await s.execute(select(ContextDoc).where(ContextDoc.enabled.is_(True)))
        ).scalars().all()
    by_path: dict[str, ContextDoc] = {}
    for d in rows:
        if d.scope == "global":
            by_path.setdefault(d.path, d)
    for d in rows:
        if d.scope == "project" and project_id and d.project_id == project_id:
            by_path[d.path] = d
    return [by_path[k] for k in sorted(by_path)]


def build_index(docs: list[ContextDoc], root: Path) -> str:
    """The L0 index injected into the prompt, capped at context_index_max_chars."""
    header = (
        "## Knowledge Base (Context Store)\n"
        f"Curated reference docs are at `{root}`. Below is a one-line abstract of "
        "each. Open a doc with the Read tool ONLY if its abstract is relevant to "
        "your task — don't read them all.\n\n"
    )
    budget = settings.context_index_max_chars - len(header)
    lines: list[str] = []
    used = 0
    for i, d in enumerate(docs):
        abstract = d.abstract or derive_abstract(d.body) or "(no abstract)"
        line = f"- `{d.path}` — **{d.title}**: {abstract}\n"
        if used + len(line) > budget:
            lines.append(f"- …and {len(docs) - i} more doc(s) — list `{root}` to see them.\n")
            break
        lines.append(line)
        used += len(line)
    return header + "".join(lines)


async def prepare_for_task(project_id: str | None, task_id: str) -> tuple[str | None, dict]:
    """Materialize applicable docs to a per-task snapshot dir and return
    (index_block, log). index_block is None when no docs apply."""
    docs = await docs_for(project_id)
    root = context_dir(task_id)
    if not docs:
        return None, {}
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for d in docs:
        dest = (root / d.path).resolve()
        if not dest.is_relative_to(root.resolve()):  # defence in depth
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"# {d.title}\n\n> {d.abstract}\n\n{d.body}\n" if d.abstract else f"# {d.title}\n\n{d.body}\n")
    index = build_index(docs, root)
    return index, {
        "docs": len(docs),
        "dir": str(root),
        "paths": [d.path for d in docs],
        "index_approx_tokens": len(index) // 4,
    }


def context_dir(task_id: str) -> Path:
    # Under the dedicated workspaces area, never inside a task's cwd/repo.
    return settings.workspaces_dir / "_context" / task_id


def cleanup(task_id: str) -> None:
    shutil.rmtree(context_dir(task_id), ignore_errors=True)
