"""Scrub secrets from task output before it is stored or streamed.

An agent can print its env (or a file holding a token), and run logs are
readable by every project member. So everything a task run persists or
broadcasts (events, result_text, error) passes through here:

* exact values of the run's sensitive env vars (owner credentials, the minted
  CO_API_TOKEN, secret-looking project env) -> "***";
* plus, defence in depth, well-known token shapes (API keys, AWS key ids,
  Slack tokens, JWTs, PEM private keys), URL user:password credentials and
  labelled values (``password=``, ``token:``, ``Bearer``, ``mysql -p``...)
  even when not in the env (final wave: forge F90b).

Redaction is per string: a secret split across two stdout chunks/events would
not be caught (stream-json delivers whole lines, so this is rare)."""

import re
from typing import Any

# Characters that can make text read differently from what it says (the frontend's
# guard.js HIDDEN_CHARS): control characters except \t and \n, soft hyphen, zero
# widths, bidi controls, tag characters.
HIDDEN_CHARS = re.compile(
    "[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u00ad\u034f\u061c\u115f\u180e\u200b-\u200f\u2028\u2029"
    "\u202a-\u202e\u2060-\u2064\u2066-\u2069\u3164\ufeff\uffa0\U000e0000-\U000e007f]")

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
# secrets.token_urlsafe(32), i.e. 43 urlsafe-base64 chars. The rest follow
# jspark-forge's scripts/lib/redact.js (v0.6.0, MIT) - generic sk- keys, AWS
# key ids (AKIA, and ASIA for temporary ones), Slack tokens, JWTs - plus PEM
# private key blocks (to the end of the text when the END line was cut off).
# co_hook.py keeps an identical copy (a test compares the patterns).
_URLSAFE = r"A-Za-z0-9_\-"
TOKEN_SHAPES = re.compile(
    "|".join(
        (
            r"sk-ant-[A-Za-z0-9_\-]{20,}",
            r"\bsk-[A-Za-z0-9_\-]{20,}",
            r"gh[pousr]_[A-Za-z0-9]{30,}",
            r"github_pat_[A-Za-z0-9_]{30,}",
            rf"(?<![{_URLSAFE}])co_[{_URLSAFE}]{{43}}(?![{_URLSAFE}])",
            r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
            r"\bxox[abposr]-[A-Za-z0-9\-]{6,}",
            r"\beyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*",
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
        )
    )
)
# user:password in URL-style values (postgres://u:p@host, https://x:tok@github.com).
URL_USERINFO = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
# Labelled secrets: the label (group 1) stays, the value becomes MASK.
# Forge's LABELLED set, widened to keys that CONTAIN a secret word
# (DB_PASSWORD=, aws_secret_access_key:, accessToken:) but not where the word
# runs on (max_tokens, tokenizer, passwords), narrowed so code and prose
# survive (redact() also runs on result_text, memory and summaries): no
# "pwd" (the working directory), never "==", and the value rules below.
# Every repeat is bounded (linear time on long input).
_VALUE_END = r"""[\s&;'",]"""
# The value after a label: quoted -> everything to the closing quote (any
# length, but not a bare $VAR / $( reference); unquoted -> at least 8
# characters, not purely numeric, not none/null/true/false, and no code
# (a "(", "[" or "." means an expression, not a secret), nor a $VAR / $(.
# Group 2 is the opening quote (it does not take part when unquoted).
_LABEL_VALUE = (
    r"""(?(2)(?!\$\{?[A-Za-z_][A-Za-z0-9_]*\}?\2|\$\()[^'"\n]{1,500}?(?=\2)"""
    rf"""|(?!(?:none|null|true|false)(?={_VALUE_END}|\Z))(?!\d+(?={_VALUE_END}|\Z))"""
    rf"""(?!\$\{{?[A-Za-z_][A-Za-z0-9_]*\}}?(?={_VALUE_END}|\Z)|\$\()(?=[^\s&;'",]{{8}})"""
    rf"""[^\s&;'",(\[.]+(?={_VALUE_END}|\Z))"""
)
LABELLED = (
    re.compile(r"(\bBearer\s+)[A-Za-z0-9._~+/=\-]{6,}", re.IGNORECASE),
    # key=value / key: value / "key": "value" / "key is: value"; never "=="
    re.compile(
        r"((?<![A-Za-z0-9_.\-])[A-Za-z0-9_.\-]{0,40}?"
        r"(?:password|passwd|secret|token|api[_-]?key)(?![a-z])[A-Za-z0-9_.\-]{0,40}"
        r"""["']?(?:\s+is)?\s*[:=](?!=)\s*(["'])?)""" + _LABEL_VALUE,
        re.IGNORECASE,
    ),
    # --password <value>, --api-key <value> (a space, not "=")
    re.compile(r"""((?<![\w-])--(?:password|passwd|api[_-]?key)\s+(["'])?)(?!-)""" + _LABEL_VALUE,
               re.IGNORECASE),
    # mysql -p<password>, -p'<password>' (not -p alone, not --port)
    re.compile(r"""((?:^|\s)(?:mysql|mysqldump|mariadb)\b[^\n]*?\s-p(["'])?)"""
               r"""(?(2)[^'"\n]{1,500}?(?=\2)|(?!ort\b)[^\s'"]+)""", re.IGNORECASE),
)


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
    text = URL_USERINFO.sub("://***:***@", TOKEN_SHAPES.sub(MASK, text))
    for pattern in LABELLED:
        text = pattern.sub(lambda m: m.group(1) + MASK, text)
    return text


ENV_VALUE_MIN = 6  # project env values at least this long are scrubbed from gate output
ENV_MASK = "[redacted]"


def env_values(envs) -> list[str]:
    """Every string value (>= ENV_VALUE_MIN chars) of the given env dicts, longest first.
    Gate output is scrubbed of ALL project env values, not only sensitive-looking names."""
    vals: set[str] = set()
    for env in envs or []:
        if isinstance(env, dict):
            vals.update(v for v in env.values() if isinstance(v, str) and len(v) >= ENV_VALUE_MIN)
    return sorted(vals, key=len, reverse=True)


def mask_values(text: str, values: list[str]) -> str:
    """Replace each value (longest first) with ENV_MASK."""
    for v in sorted((x for x in values if x), key=len, reverse=True):
        text = text.replace(v, ENV_MASK)
    return text


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


def clean_text(text, limit: int | None = None) -> str:
    """Strip HIDDEN_CHARS (``\\n`` and ``\\t`` stay), then cut to ``limit``.
    None gives "". Redact BEFORE cutting (this only cleans and cuts)."""
    if text is None:
        return ""
    out = HIDDEN_CHARS.sub("", str(text))
    return out if limit is None else out[:limit]
