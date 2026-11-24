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

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import pwd
import secrets
import shutil
import socket
import sys
import sysconfig
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, build_opener

from ..config import settings
from .co_hook import APPROVAL_WAIT_MAX

# Help text for the project's guard settings (shown by the UI in Task 5).
# frontend/src/guard/guard.js mirrors this text; keep the two in step.
GUARD_SETTINGS_HELP = (
    "co-guard checks every tool call before it runs: dangerous commands are blocked or need a person's "
    "approval, and MCP tools follow their policies. Turning the guard off switches off the built-in command "
    "rules only: MCP approvals and blocks, and self-protection of the orchestrator's own files, always apply. "
    "Inline !`cmd` in skills, slash commands and skill packs is disabled for every task, because it would run "
    "without that check."
)

APPROVAL_WAIT_MIN = 60
APPROVAL_WAIT_DEFAULT = 300
SPOOL_NAME = "spool.jsonl"
POLICY_NAME = "policy.json"
MAX_SPOOL_LINES = 2000

# Env vars that would make Claude Code skip plugin hooks (bare / safe mode, personal hooks off).
HOOK_DISABLING_ENV = ("CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_SAFE_MODE", "CLAUDE_CODE_PERSONAL_HOOKS_OFF")

# The orchestrator's own code and data (app/__init__ -> backend/).
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

# Prefixes / bin dirs shared with the whole system: protect only what we run from them.
_GENERIC_DIRS = frozenset({"/", "/usr", "/usr/local", "/opt/homebrew", "/opt/local", "/usr/bin", "/bin",
                           "/usr/local/bin", "/opt/homebrew/bin", "/opt/local/bin", "/usr/sbin", "/sbin"})


class GuardConfigError(Exception):
    """co-guard cannot be set up safely; the task must not start."""


_API_BASE_CACHE: dict[tuple, str] = {}

# Ruling R22: a per-boot secret held only in this process. GET /guard/whoami
# answers HMAC(nonce, challenge), so the worker can tell this server from a
# listener an agent started on the approval address.
_BOOT_NONCE = secrets.token_bytes(32)


def whoami_proof(challenge: str) -> str:
    return hmac.new(_BOOT_NONCE, challenge.encode(), hashlib.sha256).hexdigest()


def verify_api_base(base: str, timeout: float = 3.0) -> bool:
    """True only if ``base`` is THIS server (blocking; run in a thread)."""
    challenge = secrets.token_hex(16)
    from .co_hook import no_redirect_handler  # a 3xx is "not verified", never followed

    opener = build_opener(ProxyHandler({}), no_redirect_handler())
    try:
        with opener.open(f"{base}/guard/whoami?challenge={challenge}", timeout=timeout) as resp:
            if resp.status != 200:
                return False
            body = json.loads(resp.read(4096).decode() or "null")
    except Exception:  # noqa: BLE001 - no answer / not JSON: not verified
        return False
    proof = body.get("proof") if isinstance(body, dict) else None
    return isinstance(proof, str) and hmac.compare_digest(proof, whoami_proof(challenge))


def guard_api_base() -> str:
    """http://<literal IP>:<port> of this server (Ruling C2/R18): the bind
    address (a wildcard bind means loopback) and the backend port. A hostname
    is resolved once and refused unless it has exactly one address - an agent
    could otherwise listen on another address the name resolves to (e.g. ::1
    for localhost) and answer the hook's approval polls itself."""
    host = (settings.bind_host or "127.0.0.1").strip().strip("[]")
    port = settings.backend_port or urlsplit(settings.api_base).port or 8200
    key = (host, port)
    if key in _API_BASE_CACHE:
        return _API_BASE_CACHE[key]
    if host in ("0.0.0.0", ""):
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as e:
            raise GuardConfigError(f"co-guard: cannot resolve the server address {host!r}") from e
        addrs = {i[4][0] for i in infos}
        if len(addrs) != 1:
            raise GuardConfigError(
                f"co-guard: the server address {host!r} resolves to {len(addrs)} addresses; "
                "set CO_BIND_HOST to a literal IP")
        ip = ipaddress.ip_address(addrs.pop().split("%", 1)[0])
    if ip.is_unspecified:
        ip = ipaddress.ip_address("::1" if ip.version == 6 else "127.0.0.1")
    # an IPv6 scope id is "%25"-escaped in a URL (fe80::1%en0 -> [fe80::1%25en0])
    url = f"http://[{quote(str(ip), safe=':')}]:{port}" if ip.version == 6 else f"http://{ip}:{port}"
    _API_BASE_CACHE[key] = url
    return url


_log = logging.getLogger("app.guard")


def log_approval_address() -> None:
    """Server startup: one line with the address co-guard's hook will use for
    approvals; a loud warning when CO_BIND_HOST is a hostname (it is resolved
    once, and must resolve to exactly one address) or cannot be used."""
    host = (settings.bind_host or "").strip().strip("[]")
    is_name = False
    try:
        ipaddress.ip_address(host or "127.0.0.1")
    except ValueError:
        is_name = True
    try:
        base = guard_api_base()
    except GuardConfigError as e:
        _log.error("co-guard: NO approval address (%s) - every task will fail to start until CO_BIND_HOST "
                   "(now %r) is a literal IP", e, host)
        return
    if is_name:
        _log.warning("co-guard: CO_BIND_HOST is a hostname (%r), resolved once to %s - set CO_BIND_HOST to a "
                     "literal IP so the approval address cannot change or be claimed by another listener",
                     host, base)
    _log.info("co-guard: approval address %s (CO_BIND_HOST=%s, port %s)", base, host or "127.0.0.1",
              base.rsplit(":", 1)[-1])


def _code_path(path: str | None, keep: list[Path]) -> list[str]:
    """``path`` (a dir or file the hook / claude runs from) to protect: the
    whole dir unless it is shared with the system or holds the data dirs."""
    if not path:
        return []
    p = Path(path)
    if str(p) in _GENERIC_DIRS or any(k == p or _is_within(k, p) for k in keep):
        return []
    return [str(p)]


def trusted_code_paths(keep: list[Path]) -> list[str]:
    """The hook's interpreter and stdlib, the server venv, and the claude
    binary's install dir (I1): rewriting any of them runs code as the guard."""
    out: list[str] = []
    exe = Path(sys.executable)
    out += [str(exe), str(exe.resolve())]
    for prefix in {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}:
        rp = str(Path(prefix).resolve())
        if rp in _GENERIC_DIRS or prefix in _GENERIC_DIRS:
            # system python: the stdlib and its site dirs only
            for name in ("stdlib", "platstdlib", "purelib", "platlib"):
                v = sysconfig.get_paths(vars={"base": prefix, "platbase": prefix}).get(name)
                out += _code_path(v, keep)
        else:
            out += _code_path(prefix, keep) + _code_path(rp, keep)
    claude = shutil.which(settings.claude_bin)
    if claude:
        real = Path(claude).resolve()
        out += [claude, str(real)]
        out += _code_path(str(real.parent), keep)
    return out


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
    # the core always, even when the task's project folder resolves inside it
    # (such folders are refused at save time; a folder swapped for a symlink
    # into the core afterwards must not switch its protection off)
    for n in ("app", "migrations", "alembic.ini", ".env", ".venv", "requirements.txt"):
        out.append(str(backend / n))
    url = settings.database_url
    if url.startswith("sqlite") and ":///" in url:
        db = Path(url.split(":///", 1)[1]).expanduser()
        db = (Path.cwd() / db) if not db.is_absolute() else db
        out += [str(db), str(db) + "-wal", str(db) + "-shm", str(db) + "-journal"]
    out.append(str(Path(settings.secret_key_file).resolve()))
    return out


# Files and folders that npm/npx, pnpm, yarn, uv or bun look for in every folder
# above the one they start in (project root, workspaces glob, config, and the
# node_modules/.bin npm puts on PATH for every ancestor).
WALKUP_MARKERS = ("package.json", "package-lock.json", "node_modules", ".npmrc", "pnpm-workspace.yaml",
                  ".pnpmfile.cjs", "uv.toml", "bunfig.toml", ".yarnrc.yml", "pyproject.toml")


def launch_roots() -> list[Path]:
    """The folders under which the server starts connected tools and the code
    map build (their per-task / per-project folders are below these)."""
    ws = settings.workspaces_dir
    roots = [ws / "_mcp_configs", ws / "_codemap" / "run"]
    from ..config import tool_folders_root

    if tool_folders_root() is not None:
        roots.append(tool_folders_root())
    return roots


def _ancestors(p: Path) -> list[str]:
    out, cur = [], p.parent
    while True:
        out.append(str(cur))
        if cur.parent == cur:
            return out
        cur = cur.parent


def walkup_marker_paths(project_dir: str | None) -> list[str]:
    """``WALKUP_MARKERS`` in every ancestor of the launch roots, up to /, in
    normalised and real form, so no agent can plant a launcher-control file
    that a tool or the code map build would find above its own folder. An
    ancestor that is the task's own project folder, or inside it, is left out:
    those files are that project's own work (the orchestrator checkout as a
    project keeps its root package.json, node_modules, ... writable)."""
    dirs: list[str] = []
    for root in launch_roots():
        for form in {os.path.normpath(os.path.abspath(str(root))), os.path.realpath(str(root))}:
            dirs += _ancestors(Path(form))
    own: list[Path] = []
    if project_dir:
        own = [Path(f) for f in {os.path.normpath(os.path.abspath(project_dir)), os.path.realpath(project_dir)}]
    out: list[str] = []
    for d in dict.fromkeys(dirs):
        dp = Path(d)
        if any(dp == o or _is_within(dp, o) for o in own):
            continue
        out += [str(dp / n) for n in WALKUP_MARKERS]
    return list(dict.fromkeys(out))


def build_policy(*, task_id: str, project, workspace: Path, cwd: Path, claude_config_dir: str | None,
                 mcp_tools: dict | None, extra: dict | None = None) -> dict:
    """The per-task policy (rules.py module docstring). ``extra`` (the Phase 2
    keys from ``policy_inputs.gather``) is merged over the base keys."""
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
        str(workspaces / "_gates"),  # gate run logs
        str(workspaces / "_codemap"),  # code map index logs
        str(workspaces / "_mcp_cache"),  # connected tools' own package cache and global folder
    ]
    from ..config import tool_folders_root

    if tool_folders_root() is not None:  # the admin's place for tool start folders (not the whole setting)
        protected.append(str(tool_folders_root()))
    # npm / pnpm / uv / bun look upward from a tool's start folder (and npm puts
    # every ancestor's node_modules/.bin on PATH): their names, in every folder
    # above the tool and code map folders, except the task's own project's
    protected += walkup_marker_paths(project_dir)
    if claude_config_dir:
        protected.append(str(claude_config_dir))
    protected += orchestrator_paths(project_dir, cwd_s)
    keep = [Path(x).resolve() for x in (settings.workspaces_dir, settings.projects_dir, settings.skillpacks_dir,
                                        project_dir, cwd_s)]
    protected += trusted_code_paths(keep)
    env_paths = {"CO_POLICY_FILE": str(policy_file), "CO_TASK_DIR": str(tdir),
                 "CLAUDE_PLUGIN_ROOT": str(proot.resolve() if proot.exists() else proot)}
    if claude_config_dir:
        env_paths["CLAUDE_CONFIG_DIR"] = str(claude_config_dir)
    policy = {
        "task_id": task_id,
        "api_base": guard_api_base(),
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
    if extra:
        policy.update(extra)
    return policy


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


def _truthy(value) -> bool:
    """Claude Code's check: anything but undefined / null / false / "false" counts as set."""
    return value is not None and value is not False and value != "false"


def _disables_us(path: Path) -> bool:
    if _truthy(_settings_value(path, "disableAllHooks")) or _truthy(_settings_value(path, "allowManagedHooksOnly")):
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


MAX_SPOOL_RAW = 10000


def read_spool_raw(task_id: str) -> list[dict]:
    """Every parsable spool line (at most MAX_SPOOL_RAW), bookkeeping included."""
    out: list[dict] = []
    try:
        with open(task_dir(task_id) / SPOOL_NAME, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
                    if len(out) >= MAX_SPOOL_RAW:
                        break
    except OSError:
        pass
    return out


def read_spool(task_id: str) -> list[dict]:
    path = task_dir(task_id) / SPOOL_NAME
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            kept = 0
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("seen") is True or rec.get("done") is True \
                        or "outside_doc" in rec or "band" in rec or "nudge" in rec:
                    continue  # bookkeeping (watchdog, done, temp-doc note, token care): never counts against the cap
                if kept >= MAX_SPOOL_LINES:
                    out.append({"event": "spool", "decision": "truncated"})
                    break
                kept += 1
                out.append(rec)
    except OSError:
        pass
    return out
