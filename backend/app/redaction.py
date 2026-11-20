"""Scrub secrets from task output before it is stored or streamed.

An agent can print its env (or a file holding a token), and run logs are
readable by every project member. So everything a task run persists or
broadcasts (events, result_text, error) passes through here:

* exact values of the run's sensitive env vars (owner credentials, the minted
  CO_API_TOKEN, secret-looking project env) -> "***";
* plus, defence in depth, well-known token shapes and URL user:password
  credentials even when not in the env.

Redaction is per string: a secret split across two stdout chunks/events would
not be caught (stream-json delivers whole lines, so this is rare)."""

import re
from typing import Any

MASK = "***"
MIN_SECRET_LEN = 8  # shorter values are too likely to collide with normal text

SENSITIVE_ENV_NAMES = frozenset({
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "CO_API_TOKEN",
})
# AUTH only as a whole name segment (BASIC_AUTH, NPM_AUTH_HEADER), so
# GIT_AUTHOR_NAME / GIT_AUTHOR_EMAIL (injected into every run) stay readable.
_SENSITIVE_NAME_RE = re.compile(
    r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|ACCESS_KEY|CREDENTIAL|DSN"
    r"|DATABASE_URL|(?<![A-Z])AUTH(?![A-Z])|_KEY$",
    re.IGNORECASE,
)

# Known token shapes. co_ matches auth.mint_token exactly: "co_" +
# secrets.token_urlsafe(32), i.e. 43 urlsafe-base64 chars.
_URLSAFE = r"A-Za-z0-9_\-"
TOKEN_SHAPES = re.compile(
    "|".join(
        (
            r"sk-ant-[A-Za-z0-9_\-]{20,}",
            r"gh[pousr]_[A-Za-z0-9]{30,}",
            r"github_pat_[A-Za-z0-9_]{30,}",
            rf"(?<![{_URLSAFE}])co_[{_URLSAFE}]{{43}}(?![{_URLSAFE}])",
        )
    )
)
# user:password in URL-style values (postgres://u:p@host, https://x:tok@github.com).
URL_USERINFO = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


def is_sensitive_name(name: str) -> bool:
    return name in SENSITIVE_ENV_NAMES or bool(_SENSITIVE_NAME_RE.search(name))


def secrets_from_env(env: dict[str, str]) -> list[str]:
    """Values of the env's sensitive vars, longest first (>= MIN_SECRET_LEN)."""
    vals = {
        v
        for k, v in env.items()
        if isinstance(v, str) and len(v) >= MIN_SECRET_LEN and is_sensitive_name(k)
    }
    return sorted(vals, key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    # Longest first, so a secret containing another is scrubbed whole.
    for sec in sorted((x for x in secrets if x), key=len, reverse=True):
        text = text.replace(sec, MASK)
    text = TOKEN_SHAPES.sub(MASK, text)
    return URL_USERINFO.sub("://***:***@", text)


def redact_obj(obj: Any, secrets: list[str]) -> Any:
    """A copy of obj with every string leaf (dict keys/values, list items)
    redacted; other leaves unchanged."""
    if isinstance(obj, str):
        return redact(obj, secrets)
    if isinstance(obj, dict):
        return {
            (redact(k, secrets) if isinstance(k, str) else k): redact_obj(v, secrets)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, secrets) for v in obj]
    return obj
