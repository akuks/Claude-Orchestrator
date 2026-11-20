"""Server-side co-guard wiring for one task run (used by the worker).

* ``plugin_root()`` / ``task_dir(task_id)``: where the plugin and the per-task
  policy + spool live (``workspaces/_system/co-guard``, ``workspaces/_tasks/<id>``,
  both outside every task's cwd).
* ``build_policy(...)``: the policy dict ``rules.evaluate`` reads (see the
  ``rules`` module docstring), written 0600 by ``write_policy``.
* ``hook_blockers(cwd, config_dir)``: settings that would switch co-guard's
  hooks off (Ruling R6 follow-up).
* ``read_spool(dir)``: the hook's spool lines, for the worker to ingest as
  ``guard`` events.
"""

from __future__ import annotations

import json
import os
import pwd
import shutil
from pathlib import Path

from ..config import settings
from .co_hook import APPROVAL_WAIT_MAX

APPROVAL_WAIT_MIN = 60
APPROVAL_WAIT_DEFAULT = 300
SPOOL_NAME = "spool.jsonl"
POLICY_NAME = "policy.json"
MAX_SPOOL_LINES = 2000

# Env vars that would make Claude Code skip plugin hooks (bare / safe mode, personal hooks off).
HOOK_DISABLING_ENV = ("CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_SAFE_MODE", "CLAUDE_CODE_PERSONAL_HOOKS_OFF")

# The orchestrator's own code and data (app/__init__ -> backend/).
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


def plugin_root() -> Path:
    return settings.workspaces_dir / "_system" / "co-guard"


def task_dir(task_id: str) -> Path:
    return settings.workspaces_dir / "_tasks" / task_id


def make_task_dir(task_id: str) -> Path:
    d = task_dir(task_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(d.parent, 0o700)
    d.mkdir(mode=0o700)
    return d


def remove_task_dir(task_id: str) -> None:
    shutil.rmtree(task_dir(task_id), ignore_errors=True)


def real_home() -> str:
    """The server user's real home (not $HOME, which the server may run with changed)."""
    try:
        return pwd.getpwuid(os.getuid()).pw_dir
    except (KeyError, OSError):
        return os.path.expanduser("~")


def clamp_wait(value) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = APPROVAL_WAIT_DEFAULT
    return max(APPROVAL_WAIT_MIN, min(v, APPROVAL_WAIT_MAX))


def _is_within(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


def _protect_except(root: Path, keep: list[Path]) -> list[str]:
    """Every entry of ``root`` except the ``keep`` dirs (and the folders on the
    way to them, whose other entries are protected in turn)."""
    out: list[str] = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return [str(root)] if not any(_is_within(k, root) for k in keep) else []
    for child in children:
        c = child.resolve() if not child.is_symlink() else child.absolute()
        holds = [k for k in keep if _is_within(k, c)]
        if not holds:
            out.append(str(child))
        elif all(k != c for k in holds):
            out += _protect_except(child, holds)
    return out


def orchestrator_paths(project_dir: str, cwd: str) -> list[str]:
    """The orchestrator's own code, config, venv and DB (never the data dirs
    that live inside it by default: workspaces, projects, skillpacks, nor the
    task's own project or cwd)."""
    keep = [Path(p).resolve() for p in (settings.workspaces_dir, settings.projects_dir,
                                        settings.skillpacks_dir, project_dir, cwd)]
    backend = BACKEND_DIR
    roots = [backend]
    repo = backend.parent
    if (repo / "frontend").is_dir() and (repo / "backend").resolve() == backend:
        roots = [repo]  # the orchestrator checkout: start.sh, frontend, .git too
    out: list[str] = []
    for r in roots:
        if not any(k == r or _is_within(r, k) for k in keep):
            out += _protect_except(r, keep)
    # the core always, unless it holds the task's own project / cwd (a project
    # that IS the orchestrator checkout still cannot touch the running server)
    work = keep[3:]
    for n in ("app", "migrations", "alembic.ini", ".env", ".venv", "requirements.txt"):
        c = backend / n
        if not any(_is_within(w, c) for w in work):
            out.append(str(c))
    url = settings.database_url
    if url.startswith("sqlite") and ":///" in url:
        db = Path(url.split(":///", 1)[1]).expanduser()
        db = (Path.cwd() / db) if not db.is_absolute() else db
        out += [str(db), str(db) + "-wal", str(db) + "-shm", str(db) + "-journal"]
    out.append(str(Path(settings.secret_key_file).resolve()))
    return out


def build_policy(*, task_id: str, project, workspace: Path, cwd: Path, claude_config_dir: str | None,
                 mcp_tools: dict | None) -> dict:
    """The per-task policy (rules.py module docstring)."""
    home = real_home()
    project_dir = str(Path(getattr(project, "directory", None) or workspace).resolve())
    cwd_s = str(Path(cwd).resolve())
    tdir = task_dir(task_id).resolve()
    policy_file = tdir / POLICY_NAME
    proot = plugin_root()
    workspaces = settings.workspaces_dir.resolve()
    protected = [
        str(proot), str(proot.resolve()) if proot.exists() else str(proot),
        str(tdir), str(policy_file), str(tdir / SPOOL_NAME),
        str(Path(home) / ".claude"), str(Path(home) / ".claude.json"),  # Ruling R13
        str(workspaces / "_system"),
        # other tasks' policies/spools, every user's private state and MCP configs
        str(workspaces / "_tasks"), str(workspaces / "_users"), str(workspaces / "_mcp_configs"),
    ]
    if claude_config_dir:
        protected.append(str(claude_config_dir))
    protected += orchestrator_paths(project_dir, cwd_s)
    env_paths = {"CO_POLICY_FILE": str(policy_file), "CO_TASK_DIR": str(tdir),
                 "CLAUDE_PLUGIN_ROOT": str(proot.resolve() if proot.exists() else proot)}
    if claude_config_dir:
        env_paths["CLAUDE_CONFIG_DIR"] = str(claude_config_dir)
    return {
        "task_id": task_id,
        "api_base": settings.api_base.rstrip("/"),
        "project_dir": project_dir,
        "cwd": cwd_s,
        "home": home,
        "claude_config_dir": claude_config_dir,
        "guard_enabled": bool(getattr(project, "guard_enabled", True)) if project is not None else True,
        "approval_wait_seconds": clamp_wait(getattr(project, "approval_wait_seconds", APPROVAL_WAIT_DEFAULT)
                                            if project is not None else APPROVAL_WAIT_DEFAULT),
        "mcp_tools": dict(mcp_tools or {}),
        "env_paths": env_paths,
        "protected_paths": list(dict.fromkeys(protected)),
    }


def write_policy(task_id: str, policy: dict) -> Path:
    path = task_dir(task_id) / POLICY_NAME
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(policy, f, indent=2)
    os.chmod(path, 0o600)
    return path


def _settings_value(path: Path, key: str):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data.get(key) if isinstance(data, dict) else None


def _disables_us(path: Path) -> bool:
    if _settings_value(path, "disableAllHooks") is True:
        return True
    plugins = _settings_value(path, "enabledPlugins")
    return isinstance(plugins, dict) and any(
        str(k).split("@", 1)[0] == "co-guard" and v is False for k, v in plugins.items())


def hook_blockers(cwd: Path, claude_config_dir: str | None) -> dict:
    """{"project": [files], "user": [files]}: settings files that switch our
    hooks off. Claude Code ORs ``disableAllHooks`` over its sources (CLI
    2.1.329: user/flag and project/local are checked separately), so a
    ``--settings`` override cannot undo a project's ``true``; the worker drops
    project/local settings for that run instead (``--setting-sources user``)."""
    project: list[str] = []
    d = Path(cwd).resolve()
    home = Path(real_home()).resolve()
    # the cwd up to its git root (the project root Claude Code reads settings from)
    chain = [d, *d.parents]
    stop = next((p for p in chain if (p / ".git").exists()), d)
    for folder in chain:
        if folder == home:
            break
        for name in ("settings.json", "settings.local.json"):
            f = folder / ".claude" / name
            if f.is_file() and _disables_us(f):
                project.append(str(f))
        if folder == stop:
            break
    user = []
    if claude_config_dir:
        for name in ("settings.json", "settings.local.json"):
            f = Path(claude_config_dir) / name
            if f.is_file() and _disables_us(f):
                user.append(str(f))
    return {"project": project, "user": user}


def read_spool(task_id: str) -> list[dict]:
    path = task_dir(task_id) / SPOOL_NAME
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= MAX_SPOOL_LINES:
                    out.append({"event": "spool", "decision": "truncated"})
                    break
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        pass
    return out
