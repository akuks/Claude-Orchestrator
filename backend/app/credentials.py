"""Personal credentials and the org fallback key.

Secrets are stored only as vault blobs (app.crypto, {"v": secret}) and are
write-only: nothing here returns one except to code that injects it into a
subprocess env. Probe results are redacted before they leave this module."""

import asyncio
import os

import httpx

from .config import settings
from .crypto import decrypt_json, encrypt_json
from .models import OrgSetting, UserCredential

FALLBACK_KEY = "fallback_anthropic_api_key"
MISSING_CLAUDE = (
    "No Claude credential: add one under My credentials (or ask an admin to set an org fallback key)."
)
CLAUDE_ENV = {"oauth_token": "CLAUDE_CODE_OAUTH_TOKEN", "api_key": "ANTHROPIC_API_KEY"}
CLAUDE_KINDS = tuple(CLAUDE_ENV)

# Env names whose values are secrets; their values are scrubbed from probe output.
SECRET_ENV_NAMES = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "GH_TOKEN")
BASE_ENV_NAMES = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "TMPDIR", "TZ")
PROBE_TIMEOUT = 30
DETAIL_MAX = 200


def seal(secret: str) -> str:
    return encrypt_json({"v": secret})


def unseal(blob: str | None) -> str | None:
    return decrypt_json(blob).get("v") if blob else None


async def get_claude_secret(s, user_id: str) -> tuple[str, str] | None:
    """(env_name, value) of the user's own Claude credential, or None."""
    row = await s.get(UserCredential, user_id)
    if row is None or not row.claude_enc or row.claude_kind not in CLAUDE_ENV:
        return None
    value = unseal(row.claude_enc)
    return (CLAUDE_ENV[row.claude_kind], value) if value else None


async def get_fallback_key(s) -> str | None:
    row = await s.get(OrgSetting, FALLBACK_KEY)
    return unseal(row.value_enc) if row else None


async def get_github(s, user_id: str) -> tuple[str | None, str | None, str | None]:
    """(token, git_name, git_email); each None when unset."""
    row = await s.get(UserCredential, user_id)
    if row is None:
        return None, None, None
    return unseal(row.github_enc), row.git_name, row.git_email


async def credential_status(s, user_id: str) -> dict:
    row = await s.get(UserCredential, user_id)
    own = bool(row and row.claude_enc and row.claude_kind in CLAUDE_ENV)
    if own:
        claude = "own"
    else:
        fb = await s.get(OrgSetting, FALLBACK_KEY)
        claude = "fallback" if fb else "none"
    return {
        "claude": claude,
        "claude_kind": row.claude_kind if own else None,
        "github": bool(row and row.github_enc),
        "git_name": row.git_name if row else None,
        "git_email": row.git_email if row else None,
    }


def base_env() -> dict[str, str]:
    """The allowlisted slice of the server env; nothing else is inherited."""
    return {
        k: v
        for k, v in os.environ.items()
        if k in BASE_ENV_NAMES or k.startswith("LC_")
    }


def redact(text: str, secrets: list[str]) -> str:
    # Longest first, so a secret containing another is scrubbed whole.
    for sec in sorted((x for x in secrets if x), key=len, reverse=True):
        text = text.replace(sec, "***")
    return text


async def run_claude_probe(env: dict[str, str]) -> tuple[bool, str]:
    """Run a one-turn `claude -p` with exactly `env`. ok iff exit 0; the detail is
    stderr with every credential value in `env` replaced by "***" (redacted
    before truncation, so no secret prefix survives the cut)."""
    secrets = [env[n] for n in SECRET_ENV_NAMES if env.get(n)]
    try:
        proc = await asyncio.create_subprocess_exec(
            settings.claude_bin,
            "-p", "Reply with OK",
            "--model", "claude-haiku-4-5",
            "--max-turns", "1",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except (FileNotFoundError, PermissionError):
        return False, "claude binary not found"
    try:
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=PROBE_TIMEOUT)
        except asyncio.TimeoutError:
            return False, f"timed out after {PROBE_TIMEOUT}s"
    finally:
        # Timeout, cancellation (client gone) or any error: never leave it running.
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
    ok = proc.returncode == 0
    detail = redact(err.decode(errors="replace"), secrets).strip()[:DETAIL_MAX]
    if not detail:
        detail = "Claude responded" if ok else f"claude exited with code {proc.returncode}"
    return ok, detail


async def _refuse(detail: str) -> tuple[bool, str]:
    return False, detail


async def prepare_claude_probe(s, user_id: str):
    """Read what the probe needs, return a zero-arg coroutine function that runs
    it (so the caller can release the DB session first). Tests the user's OWN
    Claude credential only - succeeding on the org key would say nothing about
    theirs - in the exact env a task of theirs gets (app.agent_env: per-user
    CLAUDE_CONFIG_DIR etc., never the server's ~/.claude)."""
    from .agent_env import MissingCredential, build_agent_env  # imports this module

    try:
        env = dict(await build_agent_env(s, user_id, allow_fallback=False))
    except MissingCredential:
        return lambda: _refuse(MISSING_CLAUDE)
    return lambda: run_claude_probe(env)


def _http_client() -> httpx.AsyncClient:
    """Seam for tests (monkeypatched with an httpx.MockTransport client)."""
    return httpx.AsyncClient(timeout=15)


async def run_github_probe(token: str) -> tuple[bool, str]:
    try:
        async with _http_client() as c:
            r = await c.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
    except httpx.HTTPError as e:
        return False, f"GitHub request failed ({type(e).__name__})"
    if r.status_code != 200:
        # The body is not echoed: only the status reaches the caller.
        return False, f"GitHub returned HTTP {r.status_code}"
    try:
        data = r.json()
    except ValueError:
        data = None
    login = data.get("login") if isinstance(data, dict) else None
    return True, redact(str(login or "GitHub token accepted"), [token])[:DETAIL_MAX]


async def prepare_github_probe(s, user_id: str):
    token, _, _ = await get_github(s, user_id)
    if not token:
        return lambda: _refuse("No GitHub token: add one under My credentials.")
    return lambda: run_github_probe(token)
