"""co-guard custom rules: ids, reasons, pattern checks, the repo rules file and
budgeted matching.

Python port of jspark-forge ``scripts/lib/rules.js`` (the rules-file syntax)
and ``userHits``/``slowSafeTest``/``mcpText`` in ``scripts/lib/blocklist.js``
(MIT, same owner). Org and project rules are stored by the server; the repo
file ``.claude-orchestrator/rules.md`` is read by the worker for projects with
trust ``own``. All of them reach the hook as policy ``custom_rules``.

RULING R1: this module uses only the Python standard library. It is copied
into the co-guard plugin next to ``rules.py`` and loaded like ``shell.py``.

API
---
``slugify(text)``: lowercase; each run of characters outside ``[a-z0-9]``
becomes ``-``; trimmed of ``-``; at most 40 chars (trimmed again after the
cut); empty -> ``rule``.

``unique_slug(text, taken)``: ``slugify(text)``, or with ``-2``, ``-3`` ...
when it is in ``taken`` (the slugs of the same scope and project); the base is
cut so the result stays within 40 chars.

``rule_id(scope, slug)``: ``org.<slug>`` | ``project.<slug>`` | ``repo.<slug>``
(``ValueError`` for another scope or a slug ``slugify`` cannot produce).
Built-in ids never contain a dot, so the two never collide.

``clean_reason(text)``: removes invisible characters - zero-width and bidi
(U+200B-200F, U+202A-202E, U+2060-2064, U+2066-2069, U+FEFF), every other
format character (Unicode category Cf: U+00AD, U+061C, U+180E ...) and
U+034F - turns control characters (U+0000-001F, U+007F-009F) into spaces,
collapses whitespace and cuts to 200 chars - the agent reads reasons in deny
messages.

``validate_pattern(match, pattern)``: ``None`` or an error message. A pattern
is non-empty text of at most 500 chars with no invisible character (as
above: it could never match what a person reads); a ``regex`` must compile
with ``re.compile(p, re.IGNORECASE)``.

``parse_rules_md(text)``: ``{"confirm": [Rule], "never": [Rule], "errors":
[{"line", "message"}]}``, ``Rule = {"match", "pattern", "reason", "line"}``.
Forge syntax: ``## Confirm`` / ``## Never`` headings (any case), then lines
``- `text` : reason`` (or ``*``; the reason is optional) - a text is matched as
written, any case; ``- `re:regex` : reason`` is a regular expression.
ADD-ONLY: every bullet line under any other heading (``## Settings``,
``## Sources`` ...) or before the first heading is reported (``ONLY_RULES``),
and ``disable:`` / ``database dev:`` lines are reported wherever they are -
a repo file never relaxes policy. Malformed lines are skipped and reported
with their line number. A leading BOM is ignored; lines end at CRLF, LF or a
lone CR. A file over 65536 bytes (UTF-8) is rejected whole (one error, line
0); rules past the 200th are reported.

``match_rules(texts, rules, budget_ms)``: ``[(rule, "match"|"timeout")]`` for
policy entries ``{"id", "kind", "match", "pattern", "reason"}``; ``texts`` is
one text or a list - a rule hits when ANY text matches (Ruling P12; the
caller builds every text once per tool call). Ruling P11: two sources, each
with its OWN ``budget_ms`` budget, always in this order whatever the order of
the list - first org and project rules, then ``repo.*`` rules - and
``never`` before ``confirm`` inside each. Text rules are case-insensitive
substrings and never time out. Regexes are compiled once per call and run
under their source's budget (``signal.setitimer(ITIMER_REAL)``; the previous
SIGALRM handler, timer and signal mask are restored - SIGALRM is unblocked
while the budget runs if the inherited mask blocks it; an earlier timer due
inside the budget cuts it short and still fires). A regex still running when
the budget ends, and every regex of that source after it, is ``"timeout"``;
off the main thread, when the current handler was not set from Python, when
SIGALRM is already pending, or when the mask cannot be read or changed, every
regex is ``"timeout"``. The caller decides what a timeout means (an
org/project ``never`` stays hard; a repo rule or a ``confirm`` rule is
confirmable).

``values_text(value, collapse=False)``: forge's ``mcpText``: every scalar of a
JSON value (depth-first, in order, at most 100 levels), joined by newlines;
``collapse`` collapses the whitespace inside each value.

``policy_rules(value)``: the policy's ``custom_rules`` list, checked;
``ValueError`` if it is not a list of well-formed entries (the caller fails
closed).

Seatbelt limits
---------------
Custom rules see the command text and its parsed, de-quoted words. A word the
parser cannot resolve (``$(..)``, ``$G`` from ``G=git``, a computed name, a
script already on disk) is matched as written, so a ``never`` rule is a
seatbelt, not a boundary.
"""

from __future__ import annotations

import re
import signal
import threading
import time
import unicodedata

__all__ = ["REGEX_BUDGET_MS", "PATTERN_MAX", "REASON_MAX", "SLUG_MAX", "RULES_PER_SCOPE", "REPO_FILE_MAX",
           "REPO_RULES_MAX", "SCOPES", "KINDS", "MATCHES", "ONLY_RULES", "slugify", "unique_slug", "rule_id",
           "clean_reason", "validate_pattern", "parse_rules_md", "match_rules", "policy_rules", "values_text",
           "is_repo_rule"]

REGEX_BUDGET_MS = 250
PATTERN_MAX = 500
REASON_MAX = 200
SLUG_MAX = 40
RULES_PER_SCOPE = 200  # org; each project (enforced by the server)
REPO_FILE_MAX = 65536  # bytes
REPO_RULES_MAX = 200
VALUES_MAX_DEPTH = 100  # forge MCP_MAX_DEPTH
SCOPES = ("org", "project", "repo")
KINDS = ("confirm", "never")
MATCHES = ("text", "regex")
ONLY_RULES = "a repo rules file can only add Confirm/Never rules"
NO_DISABLE = "a repo rules file cannot switch off built-in rules; an admin can, in Settings \u2192 Agent guard"
NO_DB_DEV = "a repo rules file cannot mark a database as dev; an admin can, in Settings \u2192 Agent guard"
RULE_LINE = "expected - `text` : reason"

_SLUG_RUN = re.compile(r"[^a-z0-9]+")
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,%d}[a-z0-9])?$" % (SLUG_MAX - 2))
_ID = re.compile(r"^(?:%s)\.(.+)$" % "|".join(SCOPES))
_INVISIBLE = re.compile("[\u034f\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f]")
_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+")
_RULE = re.compile(r"^\s*[-*]\s+`([^`]+)`\s*(?::\s*(.*?))?\s*$")
_DISABLE = re.compile(r"^\s*[-*]\s+disable\s*:", re.IGNORECASE)
_DB_DEV = re.compile(r"^\s*[-*]\s+database\s+dev\s*:", re.IGNORECASE)
_LINES = re.compile(r"\r\n|\r|\n")


def _invisible(ch):
    return bool(_INVISIBLE.match(ch)) or unicodedata.category(ch) == "Cf"


def _strip_invisible(text):
    return "".join(ch for ch in text if not _invisible(ch))


# ---------------------------------------------------------------------------
# ids and reasons
# ---------------------------------------------------------------------------
def slugify(text):
    s = _SLUG_RUN.sub("-", str(text if text is not None else "").lower()).strip("-")
    return s[:SLUG_MAX].strip("-") or "rule"


def unique_slug(text, taken):
    base = slugify(text)
    slug, k = base, 2
    while slug in taken:
        suffix = f"-{k}"
        slug = (base[: SLUG_MAX - len(suffix)].rstrip("-") or "rule") + suffix
        k += 1
    return slug


def rule_id(scope, slug):
    if scope not in SCOPES:
        raise ValueError(f"unknown rule scope {scope!r}")
    if not isinstance(slug, str) or not _SLUG.match(slug):
        raise ValueError(f"bad rule slug {slug!r}")
    return f"{scope}.{slug}"


def clean_reason(text):
    if text is None:
        return ""
    s = _CONTROL.sub(" ", _strip_invisible(str(text)))
    return " ".join(s.split())[:REASON_MAX].strip()


def _compile(pattern):
    return re.compile(pattern, re.IGNORECASE)


def validate_pattern(match, pattern):
    if match not in MATCHES:
        return "a rule matches text or regex"
    if not isinstance(pattern, str) or not pattern.strip():
        return "the pattern is empty"
    if len(pattern) > PATTERN_MAX:
        return f"the pattern is longer than {PATTERN_MAX} characters"
    if any(_invisible(ch) for ch in pattern):
        return "the pattern contains an invisible (zero-width or format) character"
    if match == "regex":
        try:
            _compile(pattern)
        except (re.error, ValueError, OverflowError, RecursionError, MemoryError) as e:
            return f"bad regular expression: {e}"
    return None


# ---------------------------------------------------------------------------
# the repo rules file (forge rules.js parseRules, add-only)
# ---------------------------------------------------------------------------
def parse_rules_md(text):
    out = {"confirm": [], "never": [], "errors": []}
    if not text:
        return out
    text = str(text)
    if text.startswith("\ufeff"):
        text = text[1:]
    if len(text.encode("utf-8", "surrogatepass")) > REPO_FILE_MAX:
        out["errors"].append({"line": 0, "message": f"the rules file is larger than {REPO_FILE_MAX} bytes, "
                                                    "so none of it is used"})
        return out
    section = None  # None: before any heading; "other": a heading that is not Confirm/Never
    count = 0
    for idx, line in enumerate(_LINES.split(text)):
        n = idx + 1
        h = _HEADING.match(line)
        if h:
            name = h.group(1).lower()
            section = name if name in KINDS else "other"
            continue
        if not _BULLET.match(line):
            continue
        if _DISABLE.match(line):
            out["errors"].append({"line": n, "message": NO_DISABLE})
            continue
        if _DB_DEV.match(line):
            out["errors"].append({"line": n, "message": NO_DB_DEV})
            continue
        if section not in KINDS:
            out["errors"].append({"line": n, "message": ONLY_RULES})
            continue
        m = _RULE.match(line)
        if not m:
            out["errors"].append({"line": n, "message": RULE_LINE})
            continue
        source = m.group(1)
        match, pattern = ("regex", source[3:]) if source.startswith("re:") else ("text", source)
        err = validate_pattern(match, pattern)
        if err:
            out["errors"].append({"line": n, "message": err})
            continue
        if count >= REPO_RULES_MAX:
            out["errors"].append({"line": n, "message": f"only the first {REPO_RULES_MAX} rules are used"})
            continue
        count += 1
        out[section].append({"match": match, "pattern": pattern, "reason": clean_reason(m.group(2) or ""),
                             "line": n})
    return out


# ---------------------------------------------------------------------------
# policy entries
# ---------------------------------------------------------------------------
def policy_rules(value):
    """The policy's ``custom_rules``, checked (ValueError: malformed)."""
    if not isinstance(value, list):
        raise ValueError("custom_rules is not a list")
    for r in value:
        if not isinstance(r, dict):
            raise ValueError("a custom rule is not an object")
        rid = r.get("id")
        m = _ID.match(rid) if isinstance(rid, str) else None
        if not m or not _SLUG.match(m.group(1)):
            raise ValueError(f"bad custom rule id {rid!r}")
        if r.get("kind") not in KINDS:
            raise ValueError(f"custom rule {rid}: bad kind")
        err = validate_pattern(r.get("match"), r.get("pattern"))
        if err:
            raise ValueError(f"custom rule {rid}: {err}")
        if not isinstance(r.get("reason"), str):
            raise ValueError(f"custom rule {rid}: bad reason")
    return value


# ---------------------------------------------------------------------------
# matching (forge userHits / slowSafeTest / mcpText; Rulings P11, P12)
# ---------------------------------------------------------------------------
def is_repo_rule(rule):
    return str(rule.get("id", "")).startswith("repo.")


def values_text(value, collapse=False):
    """forge mcpText: every scalar, depth-first in order, joined by newlines
    (collapse: whitespace inside each value collapsed to single spaces)."""
    parts = []
    todo = [(value, 0)]
    while todo:
        x, d = todo.pop()
        if x is None:
            continue
        if isinstance(x, dict) or isinstance(x, (list, tuple)):
            if d >= VALUES_MAX_DEPTH:
                continue
            vals = list(x.values()) if isinstance(x, dict) else list(x)
            todo.extend((v, d + 1) for v in reversed(vals))
            continue
        text = "true" if x is True else "false" if x is False else str(x)
        parts.append(" ".join(text.split()) if collapse else text)
    return "\n".join(parts)


class _Timeout(BaseException):
    """Raised by our SIGALRM handler inside a running regex."""


def _alarm_usable():
    """SIGALRM can interrupt us here: main thread, a Python-level handler to
    put back, and no SIGALRM already pending. Returns the mask to restore
    (or None when nothing must change), or False."""
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
        return False
    try:
        if signal.getsignal(signal.SIGALRM) is None:  # set outside Python: it could not be put back
            return False
        if not hasattr(signal, "pthread_sigmask"):
            return None
        if signal.SIGALRM in signal.sigpending():
            return False  # unblocking would hand someone else's alarm to our handler
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    except (ValueError, OSError, AttributeError):
        return False
    return mask if signal.SIGALRM in mask else None


def _run_regexes(jobs, texts, budget_ms):
    """``jobs`` = [(index, compiled)]; {index: "match"|"no"|"timeout"} - a
    job matches when it matches any of ``texts``; one budget for all jobs."""
    if not jobs:
        return {}
    timeout = {i: "timeout" for i, _rx in jobs}
    if budget_ms <= 0:
        return timeout
    blocked = _alarm_usable()
    if blocked is False:
        return timeout
    old_handler = signal.getsignal(signal.SIGALRM)
    budget = budget_ms / 1000.0
    live = [True]  # the handler raises only while regexes may still be running

    def on_alarm(_sig, _frame):
        if live[0]:
            raise _Timeout()

    results = {}
    start = time.monotonic()
    old_delay = old_interval = 0.0
    installed = unblocked = False
    try:
        try:
            # take over the timer first (an earlier timer firing before this runs its own handler) ...
            old_delay, old_interval = signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, on_alarm)
            installed = True
            if blocked is not None:  # the inherited mask blocks SIGALRM: let ours through
                signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGALRM])
                unblocked = True
            # ... an earlier timer due inside the budget ends it (fail safe) and fires after the restore
            signal.setitimer(signal.ITIMER_REAL, min(budget, old_delay) if old_delay else budget)
            for i, rx in jobs:
                results[i] = "match" if any(rx.search(t) for t in texts) else "no"
            live[0] = False
        except _Timeout:
            live[0] = False
        except (ValueError, OSError):  # the signal machinery refused: nothing is known
            live[0] = False
            results = {}
    finally:
        live[0] = False
        signal.setitimer(signal.ITIMER_REAL, 0)
        if unblocked:
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        if installed:
            signal.signal(signal.SIGALRM, old_handler)
        if old_delay:
            left = old_delay - (time.monotonic() - start)
            signal.setitimer(signal.ITIMER_REAL, max(left, 1e-6), old_interval)
    for i, _rx in jobs:
        results.setdefault(i, "timeout")
    return results


def _match_source(texts, lowers, rules, budget_ms):
    ordered = sorted(rules, key=lambda r: r.get("kind") != "never")  # never first: they get the budget first
    verdicts = {}
    jobs = []
    for i, r in enumerate(ordered):
        if r.get("match") == "regex":
            try:
                jobs.append((i, _compile(r["pattern"])))
            except (re.error, ValueError, OverflowError, RecursionError, MemoryError, KeyError, TypeError):
                verdicts[i] = "timeout"  # cannot check it: never a pass
        else:
            pat = str(r.get("pattern", "")).lower()
            if any(pat in t for t in lowers):
                verdicts[i] = "match"
    verdicts.update(_run_regexes(jobs, texts, budget_ms))
    return [(r, verdicts[i]) for i, r in enumerate(ordered) if verdicts.get(i) in ("match", "timeout")]


def match_rules(texts, rules, budget_ms):
    if isinstance(texts, str):
        texts = [texts]
    texts = list(dict.fromkeys(str(t) for t in texts))
    lowers = [t.lower() for t in texts]
    rules = list(rules)
    # Ruling P11: org/project first, repo after, each under its own budget
    first = [r for r in rules if not is_repo_rule(r)]
    repo = [r for r in rules if is_repo_rule(r)]
    return _match_source(texts, lowers, first, budget_ms) + _match_source(texts, lowers, repo, budget_ms)
