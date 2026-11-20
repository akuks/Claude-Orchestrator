"""The environment an agent subprocess runs with.

Built from scratch, never inherited: the allowlisted slice of the server env,
then the project's env (admin-managed), then per-user config dirs and the task
creator's own credentials (or the org fallback key), which win over both.
The server's own ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN / GH_TOKEN /
GITHUB_TOKEN, CO_* config and ~/.claude, ~/.config/gh, ~/.gitconfig never
reach a task."""

import logging
import os
import uuid
from pathlib import Path

from . import credentials
from .config import settings

log = logging.getLogger(__name__)

# Auth / routing / token variables project env may not set: the only ones a
# task gets are the creator's own (or the org fallback key) injected below.
# Routing (base URL, Bedrock/Vertex/Foundry switches, custom headers) is
# included so project env can't send the injected credential elsewhere or run
# on another account. Defence in depth, not exhaustive: other project-env
# keys (proxies, NODE_OPTIONS, PATH_PREPEND, ...) are admin-trusted.
PROJECT_ENV_BLOCKED = frozenset({
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "AWS_BEARER_TOKEN_BEDROCK",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
})
_DIR_MODE = 0o700
_FILE_MODE = 0o600


class MissingCredential(Exception):
    """The task's creator has no Claude credential and there is no fallback."""

    def __init__(self, message: str = credentials.MISSING_CLAUDE):
        super().__init__(message)


class AgentEnv(dict):
    """A plain env dict that also carries the labels of the credentials it
    injected (names only, for the task's `env` event)."""

    credentials: list[str]


def user_root(user_id: str) -> Path:
    return Path(settings.workspaces_dir) / "_users" / user_id


def _private_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, _DIR_MODE)
    return p


def _git_value(v: str) -> str:
    # Quoted git-config value; control chars are already refused at the API.
    v = "".join(c for c in v if c.isprintable())
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_gitconfig(path: Path, name: str | None, email: str | None) -> None:
    lines = []
    if name or email:
        lines.append("[user]")
        if name:
            lines.append(f"\tname = {_git_value(name)}")
        if email:
            lines.append(f"\temail = {_git_value(email)}")
    # Repos are owned by the server user, not this one.
    lines += ["[safe]", "\tdirectory = *", ""]
    # Write-then-rename so a concurrent task of the same user never reads a
    # half-written file.
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path)


def _apply_project_env(env: dict[str, str], project_env: dict) -> None:
    project_env = dict(project_env)
    # PATH_PREPEND is prepended to PATH (toolchain scoping, e.g. a project-
    # specific PHP/Node) rather than replacing it.
    prepend = project_env.pop("PATH_PREPEND", None)
    env.update(
        {k: str(v) for k, v in project_env.items() if k not in PROJECT_ENV_BLOCKED}
    )
    if prepend:
        env["PATH"] = f"{prepend}{os.pathsep}{env.get('PATH', '')}"


async def build_agent_env(
    s,
    user_id: str | None,
    *,
    project_env: dict | None = None,
    allow_fallback: bool = True,
) -> AgentEnv:
    """Env for an agent run as `user_id`. Raises MissingCredential (message =
    credentials.MISSING_CLAUDE) when the user has no Claude credential and no
    org fallback is set (or allow_fallback is False), or when there is no user
    (legacy ownerless task)."""
    if user_id is None:
        raise MissingCredential()
    claude = await credentials.get_claude_secret(s, user_id)
    label = "claude"
    if claude is None:
        fallback = await credentials.get_fallback_key(s) if allow_fallback else None
        if not fallback:
            raise MissingCredential()
        claude = (credentials.CLAUDE_ENV["api_key"], fallback)
        label = "claude (org fallback)"
    gh_token, git_name, git_email = await credentials.get_github(s, user_id)

    env = AgentEnv(credentials.base_env())
    if project_env:
        _apply_project_env(env, project_env)

    # Per-user CLI state, redirected away from the server user's HOME.
    _private_dir(user_root(user_id).parent)
    root = _private_dir(user_root(user_id))
    gitconfig = root / "gitconfig"
    _write_gitconfig(gitconfig, git_name, git_email)
    env["CLAUDE_CONFIG_DIR"] = str(_private_dir(root / "claude"))
    env["GH_CONFIG_DIR"] = str(_private_dir(root / "gh"))
    env["GIT_CONFIG_GLOBAL"] = str(gitconfig)

    # Exactly one Claude variable (project env's were dropped above).
    env[claude[0]] = claude[1]
    labels = [label]
    if gh_token:
        env["GH_TOKEN"] = gh_token
        labels.append("github")
    if git_name:
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = git_name
    if git_email:
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = git_email
    env.credentials = labels
    return env


async def agent_env_or_none(
    s, user_id: str | None, *, project_env: dict | None = None, purpose: str
) -> AgentEnv | None:
    """build_agent_env for background server-side calls (memory, findings):
    None instead of MissingCredential, logged at info (no secret). Any other
    error is logged as a warning (purpose and user id only) and also yields
    None, so a background job never dies with an unretrieved exception."""
    try:
        return await build_agent_env(s, user_id, project_env=project_env)
    except MissingCredential:
        log.info("%s skipped: no Claude credential for user %s", purpose, user_id)
        return None
    except Exception as exc:  # noqa: BLE001 - background callers must not crash
        log.warning(
            "%s skipped: could not build env for user %s (%s)",
            purpose, user_id, type(exc).__name__,
        )
        return None
