"""Built-in workflow templates, Markdown export/import and repo workflow files."""

import asyncio
import json
import os
import re
import stat

from app import flowgraph

MAX_IMPORT_BYTES = 64 * 1024
MAX_REPO_FILES = 20
REPO_DIR = (".claude-orchestrator", "workflows")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}\.md$")
GATE_HINT = "Set the project's own test command in the gate."
COMMIT_NOTE = "Commit on the current branch. Do not merge or push."
_JSON_BLOCK = re.compile(r"^```json[ \t]*\r?\n(.*?)\r?\n```[ \t]*$", re.S | re.M)


def _agent(name: str, items: list[tuple[str, str]]) -> dict:
    return {"name": name, "items": [{"agent": a, "prompt": p} for a, p in items]}


def _gate(name: str) -> dict:
    return {"name": name, "kind": "gate", "run": ["npm test"], "timeout_min": 60, "before": []}


def _approval(name: str, message: str, ahead: bool = False) -> dict:
    """An approval step; only ahead=True may be approved before the run reaches it."""
    st = {"name": name, "kind": "approval", "message": message}
    if ahead:
        st["ahead"] = True
    return st


def _build(task: str) -> str:
    return f"{task}\n\nWork: {{{{input}}}}\n\n{COMMIT_NOTE}"


TEMPLATES: dict[str, dict] = {
    "software": {
        "name": "Software change",
        "description": "Build the change, run the tests, then review it.",
        "stages": [
            _agent("Build", [("Builder", _build("Implement this change, test first."))]),
            _gate("Test"),
            _agent("Review", [("Reviewer", "Review the change on this branch and rank your findings.\n\nWork: {{input}}")]),
        ],
    },
    "planned": {
        "name": "Planned change",
        "description": "Plan, approve the plan, build in parallel, run the tests, review, then approve the release.",
        "stages": [
            _agent("Plan", [("Planner", "Plan this change and write the briefs. Do not implement it.\n\nWork: {{input}}")]),
            _approval("Approve plan", "Read the plan and approve it before anyone builds.", ahead=True),
            _agent("Build", [
                ("Builder", _build("Implement the first half of the plan, test first.\n\nPlan:\n{{stage1}}")),
                ("Builder", _build("Implement the second half of the plan, test first.\n\nPlan:\n{{stage1}}")),
            ]),
            _gate("Test"),
            _agent("Review", [("Reviewer", "Review the change on this branch against the plan and rank your findings.\n\nPlan:\n{{stage1}}\n\nWork: {{input}}")]),
            _approval("Approve release", "Check the review and approve the release."),
        ],
    },
    "documents": {
        "name": "Documents",
        "description": "Outline the document, write it, then review it. No tests.",
        "stages": [
            _agent("Outline", [("Planner", "Outline the document: sections and what each must say.\n\nWork: {{input}}")]),
            _agent("Write", [("Builder", _build("Write the document from the outline.\n\nOutline:\n{{prev}}"))]),
            _agent("Review", [("Reviewer", "Review the document for accuracy and clarity and rank your findings.\n\nWork: {{input}}")]),
        ],
    },
    "spike": {
        "name": "Spike",
        "description": "A quick experiment: build it, check that it runs, then review what was learned.",
        "stages": [
            _agent("Explore", [("Builder", _build("Build a small experiment that answers the question."))]),
            _agent("Check", [("Checker", "Run the experiment and report what happens, facts only.\n\nExperiment:\n{{prev}}\n\nWork: {{input}}")]),
            _agent("Review", [("Reviewer", "Review what the experiment showed and what it leaves open.\n\nWork: {{input}}")]),
        ],
    },
    "ui": {
        "name": "UI change",
        "description": "Build the screen, run the tests, check the production build, review, then approve.",
        "stages": [
            _agent("Build", [("Builder", _build("Implement this UI change, test first."))]),
            _gate("Test"),
            _agent("Build check", [("Checker", "Run the production build and report totals and errors, facts only.\n\nWork: {{input}}")]),
            _agent("Review", [("Reviewer", "Review the UI change for correctness and accessibility and rank your findings.\n\nWork: {{input}}")]),
            _approval("Approve", "Look at the result and approve it."),
        ],
    },
}


def templates_out() -> list[dict]:
    out = []
    for key, t in TEMPLATES.items():
        has_gate = any(st.get("kind") == "gate" for st in t["stages"])
        out.append({
            "key": key, "name": t["name"], "description": t["description"],
            "stages": json.loads(json.dumps(t["stages"])),
            "hint": GATE_HINT if has_gate else None,
        })
    return out


def export_markdown(wf) -> str:
    desc = wf.description or ""
    doc = {"name": wf.name, "description": desc, "stages": wf.stages or []}
    if wf.budget_usd is not None:
        doc["budget_usd"] = wf.budget_usd
    mp = flowgraph.get_max_parallel(wf.stages or [])
    if mp != flowgraph.DEFAULT_MAX_PARALLEL:
        doc["max_parallel"] = mp
    shown = desc.replace("```", "'''")
    title = " ".join((wf.name or "").split())
    parts = [f"# {title}", ""]
    if shown.strip():
        parts += [shown.strip(), ""]
    parts += ["```json", json.dumps(doc, indent=2, ensure_ascii=False), "```", ""]
    return "\n".join(parts)


def parse_import(text: str) -> dict:
    if not isinstance(text, str):
        raise ValueError("The import text must be text")
    if len(text.encode("utf-8", "replace")) > MAX_IMPORT_BYTES:
        raise ValueError("The import is larger than 64 KB")
    blocks = _JSON_BLOCK.findall(text)
    if len(blocks) != 1:
        raise ValueError(
            "Expected exactly one ```json block, found " + ("none" if not blocks else str(len(blocks)))
        )
    try:
        data = json.loads(blocks[0])
    except ValueError as e:
        raise ValueError(f"The json block is not valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise ValueError("The json block must be an object with name, description and stages")
    out = {"name": data.get("name"), "description": data.get("description"), "stages": data.get("stages")}
    if data.get("budget_usd") is not None:
        out["budget_usd"] = data["budget_usd"]  # WorkflowCreate validates it like a save
    if data.get("max_parallel") is not None:
        out["max_parallel"] = data["max_parallel"]  # checked by flowgraph.validate on save
    return out


# ---- repo workflow files ----------------------------------------------------


def _read_regular(path: str) -> bytes | None:
    """Read a regular, non-symlink file of at most 64 KB; None otherwise. Never blocks on a FIFO."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_IMPORT_BYTES:
            return None
        data = b""
        while len(data) < MAX_IMPORT_BYTES + 1:
            chunk = os.read(fd, MAX_IMPORT_BYTES + 1 - len(data))
            if not chunk:
                break
            data += chunk
        return data if len(data) <= MAX_IMPORT_BYTES else None
    except OSError:
        return None
    finally:
        os.close(fd)


def _repo_dir(project) -> tuple[str | None, str | None]:
    if getattr(project, "trust", "own") != "own":
        return None, "Repo workflow files are read only from projects marked own"
    base = (project.directory or "").strip()
    if not base:
        return None, "The project has no directory"
    path = base
    try:
        if not stat.S_ISDIR(os.stat(base).st_mode):
            return None, "The project directory is not a folder"
        for part in REPO_DIR:
            path = os.path.join(path, part)
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                return None, "No .claude-orchestrator/workflows folder in the project"
            if stat.S_ISLNK(st.st_mode):
                return None, "The workflows folder is a symlink, so it was not read"
            if not stat.S_ISDIR(st.st_mode):
                return None, "No .claude-orchestrator/workflows folder in the project"
    except OSError:
        return None, "The project directory cannot be read"
    return path, None


def _list_sync(project) -> dict:
    path, reason = _repo_dir(project)
    if path is None:
        return {"files": [], "reason": reason}
    try:
        names = sorted(n for n in os.listdir(path) if FILE_RE.fullmatch(n))
    except OSError:
        return {"files": [], "reason": "The workflows folder cannot be read"}
    files = []
    for n in names:
        data = _read_regular(os.path.join(path, n))
        if data is None:
            continue
        files.append({"name": n, "size": len(data)})
        if len(files) >= MAX_REPO_FILES:
            break
    return {"files": files, "reason": None if files else "No usable .md files in the workflows folder"}


async def list_repo_files(project) -> dict:
    return await asyncio.to_thread(_list_sync, project)


def _read_sync(project, name: str) -> str:
    if not isinstance(name, str) or not FILE_RE.fullmatch(name):
        raise ValueError("Not a valid workflow file name")
    path, reason = _repo_dir(project)
    if path is None:
        raise ValueError(reason)
    data = _read_regular(os.path.join(path, name))
    if data is None:
        raise ValueError("That file is missing, not a regular file, or larger than 64 KB")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("That file is not UTF-8 text") from None


async def read_repo_file(project, name: str) -> str:
    return await asyncio.to_thread(_read_sync, project, name)
