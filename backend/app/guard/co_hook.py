"""co-guard hook: the one entry point Claude Code runs for every hook event.

Run as ``<server python> -I <plugin>/hooks/co_hook.py <Event>`` with the hook
payload (JSON) on stdin. Stdlib only (Ruling R1); ``rules.py`` and ``shell.py``
are loaded by path from ``<plugin>/lib``.

PreToolUse
    ``rules.evaluate`` on the tool call with the task's policy
    (``$CO_POLICY_FILE``):

    * ``allow``   -> no output (Claude Code's own rules, e.g. the worker's
      ``--disallowedTools``, still apply);
    * ``deny``    -> ``permissionDecision: deny`` with the reason;
    * ``approve`` -> file a request (``POST {api_base}/guard/approvals``, Bearer
      ``$CO_API_TOKEN``, Ruling R2), poll ``GET /guard/approvals/{id}`` every
      2 s until approved / rejected / expired or ``approval_wait_seconds``
      pass. Approved -> no decision (only ``additionalContext``), so deny rules
      still apply; anything else -> deny. Network errors while filing or
      waiting -> retry until the deadline, then deny.

    FAIL CLOSED: Claude Code lets a call through when a hook crashes or times
    out (spike c), so any exception, malformed stdin, a missing or unreadable
    policy or an unknown event denies with "co-guard error - action blocked for
    safety", and a watchdog denies ``EXIT_MARGIN`` seconds before the hook's
    configured timeout.

Every non-allow decision is appended to ``$CO_TASK_DIR/spool.jsonl`` (decision,
rule ids, tool, a redacted summary of at most 300 chars - never raw secrets)
and best-effort POSTed to ``/guard/report`` (0.5 s timeout). Other events
(SessionStart, Stop, SessionEnd, SubagentStart, SubagentStop, PostToolUse of
an approved call) are spooled only.
"""

import hashlib
import importlib.util
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

# hooks.json PreToolUse timeout = APPROVAL_WAIT_MAX + 30 (Global Constraints).
APPROVAL_WAIT_MAX = 900
HOOK_TIMEOUT = APPROVAL_WAIT_MAX + 30
EXIT_MARGIN = 10  # always answer this long before HOOK_TIMEOUT (>= 5 s)
POLL_SECONDS = 2.0
REPORT_TIMEOUT = 0.5
SUMMARY_MAX = 300
ERROR_REASON = "co-guard error — action blocked for safety"
EVENTS = ("PreToolUse", "PostToolUse", "SessionStart", "Stop", "SessionEnd", "SubagentStart", "SubagentStop")
TOOL_MATCHER = "Bash|Edit|Write|NotebookEdit|MultiEdit|mcp__.*"

# Local copy of app.redaction's token shapes (the hook cannot import the app).
_URLSAFE = r"A-Za-z0-9_\-"
_TOKEN_SHAPES = re.compile("|".join((
    r"sk-ant-[A-Za-z0-9_\-]{20,}",
    r"gh[pousr]_[A-Za-z0-9]{30,}",
    r"github_pat_[A-Za-z0-9_]{30,}",
    rf"(?<![{_URLSAFE}])co_[{_URLSAFE}]{{43}}(?![{_URLSAFE}])",
)))
_URL_USERINFO = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
_SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|ACCESS_KEY|CREDENTIAL|DSN"
                          r"|DATABASE_URL|(?<![A-Z])AUTH(?![A-Z])|_KEY$", re.IGNORECASE)

_out_lock = threading.Lock()
_answered = False


def _secrets():
    vals = {v for k, v in os.environ.items() if len(v) >= 8 and _SECRET_NAME.search(k)}
    return sorted(vals, key=len, reverse=True)


def redact(text, limit=SUMMARY_MAX):
    text = str(text)
    for sec in _secrets():
        text = text.replace(sec, "***")
    text = _URL_USERINFO.sub("://***:***@", _TOKEN_SHAPES.sub("***", text))
    return text if len(text) <= limit else text[:limit - 1] + "…"


# -- output -----------------------------------------------------------------
def _emit(obj):
    """Print the hook's single answer (first caller wins: main or watchdog)."""
    global _answered
    with _out_lock:
        if _answered:
            return False
        _answered = True
        if obj is not None:
            sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()
        return True


def deny(reason):
    return _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                         "permissionDecisionReason": reason}})


def context_only(text):
    return _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}})


def _watchdog(seconds):
    def fire():
        if deny(ERROR_REASON + " (co-guard ran out of time)"):
            _spool({"event": "PreToolUse", "decision": "deny", "rule_ids": [], "reason": "hook watchdog"})
        os._exit(0)

    t = threading.Timer(max(0.0, seconds), fire)
    t.daemon = True
    t.start()
    return t


# -- policy, rules ----------------------------------------------------------
def load_policy():
    path = os.environ.get("CO_POLICY_FILE")
    if not path:
        raise RuntimeError("CO_POLICY_FILE is not set")
    with open(path, encoding="utf-8") as f:
        policy = json.load(f)
    if not isinstance(policy, dict):
        raise ValueError("policy is not an object")
    return policy


def load_rules():
    here = os.path.dirname(os.path.abspath(__file__))
    lib = os.path.join(os.path.dirname(here), "lib")
    spec = importlib.util.spec_from_file_location("rules", os.path.join(lib, "rules.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rules"] = mod
    spec.loader.exec_module(mod)
    return mod


# -- spool / report ---------------------------------------------------------
def _spool(record):
    tdir = os.environ.get("CO_TASK_DIR")
    if not tdir:
        return
    record = dict(record, ts=round(time.time(), 3))
    line = json.dumps(record, default=str) + "\n"
    try:
        fd = os.open(os.path.join(tdir, "spool.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
    except OSError:
        pass


def _api(policy):
    base = str(policy.get("api_base") or os.environ.get("CO_API_BASE") or "").rstrip("/")
    if not base:
        raise RuntimeError("no api_base")
    return base


def _request(method, url, body=None, timeout=5.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + os.environ.get("CO_API_TOKEN", ""))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=max(0.1, timeout)) as resp:
        return json.loads(resp.read().decode() or "null")


def report(policy, record):
    _spool(record)
    try:
        _request("POST", _api(policy) + "/guard/report", dict(record, task_id=policy.get("task_id")),
                 timeout=REPORT_TIMEOUT)
    except Exception:  # noqa: BLE001 - best effort only
        pass


# -- approvals --------------------------------------------------------------
def wait_seconds(policy):
    try:
        w = float(policy.get("approval_wait_seconds", 300))
    except (TypeError, ValueError):
        w = 300.0
    return max(0.0, min(w, float(APPROVAL_WAIT_MAX)))


def request_approval(policy, payload, verdict, summary, deadline):
    """'approved' | 'rejected' | 'expired' | 'timeout' plus the request id."""
    base = _api(policy)
    tool = str(payload.get("tool_name") or "")
    canon = json.dumps({"tool_name": tool, "tool_input": payload.get("tool_input")}, sort_keys=True, default=str)
    body = {
        "task_id": policy.get("task_id") or os.environ.get("CO_TASK_ID"),
        "rule_ids": [h.rule_id for h in verdict.hits],
        "tool": tool,
        "summary": redact(summary, 2000),
        "cwd": str(payload.get("cwd") or policy.get("cwd") or ""),
        "reason": redact("; ".join(h.reason for h in verdict.hits), 2000),
        "command_hash": hashlib.sha256(canon.encode()).hexdigest(),
    }
    poll = float(policy.get("approval_poll_seconds") or POLL_SECONDS)
    req_id = None
    while time.monotonic() < deadline:
        left = deadline - time.monotonic()
        try:
            if req_id is None:
                res = _request("POST", base + "/guard/approvals", body, timeout=min(5.0, left))
                req_id = res.get("id") if isinstance(res, dict) else None
                status = res.get("status") if isinstance(res, dict) else None
            else:
                res = _request("GET", f"{base}/guard/approvals/{req_id}", timeout=min(5.0, left))
                status = res.get("status") if isinstance(res, dict) else None
            if req_id is not None and status in ("approved", "rejected", "expired"):
                return status, req_id
        except Exception:  # noqa: BLE001 - API down / bad answer: keep trying until the deadline
            pass
        time.sleep(max(0.0, min(poll, deadline - time.monotonic())))
    return "timeout", req_id


# -- events -----------------------------------------------------------------
def pre_tool_use(payload, policy, started):
    rules = load_rules()
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or not tool:
        raise ValueError("no tool_name")
    cwd = payload.get("cwd") or policy.get("cwd")
    verdict = rules.evaluate(tool, payload.get("tool_input"), policy, cwd)
    if verdict.decision == "allow":
        return _emit(None)
    ids = [h.rule_id for h in verdict.hits]
    summary = verdict.hits[0].summary if verdict.hits else tool
    reason = "; ".join(f"{h.reason} ({h.rule_id})" for h in verdict.hits) or "blocked"
    base = {"event": "PreToolUse", "tool": tool, "rule_ids": ids, "summary": redact(summary),
            "tool_use_id": payload.get("tool_use_id"), "agent_id": payload.get("agent_id")}
    if verdict.decision != "approve":
        report(policy, dict(base, decision="deny", reason=redact(reason)))
        return deny("CO-GUARD: blocked — " + redact(reason, 1000))
    hard_deadline = started + HOOK_TIMEOUT - EXIT_MARGIN
    deadline = min(time.monotonic() + wait_seconds(policy), hard_deadline - 2)
    status, req_id = request_approval(policy, payload, verdict, summary, deadline)
    rec = dict(base, decision="approved" if status == "approved" else "deny", status=status, request_id=req_id,
               reason=redact(reason))
    report(policy, rec)
    if status == "approved":
        return context_only(f"co-guard: a person approved this action (request {req_id}).")
    why = {"rejected": "a person rejected it", "expired": "the request expired",
           "timeout": "no decision in time"}[status]
    return deny(f"CO-GUARD: blocked — needs approval and {why} (request {req_id or 'not filed'}): "
                + redact(reason, 1000) + ". Do not retry the same action; explain what you needed instead.")


def _approved_ids():
    tdir = os.environ.get("CO_TASK_DIR")
    out = set()
    try:
        with open(os.path.join(tdir, "spool.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("decision") == "approved" and r.get("tool_use_id"):
                    out.add(r["tool_use_id"])
    except (OSError, TypeError):
        pass
    return out


def other_event(event, payload):
    if event == "PostToolUse":
        tid = payload.get("tool_use_id")
        if tid and tid in _approved_ids():
            _spool({"event": event, "tool": payload.get("tool_name"), "tool_use_id": tid, "decision": "ran"})
        return
    rec = {"event": event}
    for k in ("source", "reason", "agent_id", "agent_type", "session_id"):
        if payload.get(k) is not None:
            rec[k] = redact(payload[k], 200)
    _spool(rec)


def main(argv):
    started = time.monotonic()
    event = argv[1] if len(argv) > 1 else ""
    is_pre = event == "PreToolUse"
    watchdog = _watchdog(HOOK_TIMEOUT - EXIT_MARGIN) if is_pre or event not in EVENTS else None
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        if event not in EVENTS:
            raise ValueError("unknown event")
        if is_pre:
            if payload.get("hook_event_name") not in (None, "PreToolUse"):
                raise ValueError("event mismatch")
            pre_tool_use(payload, load_policy(), started)
        else:
            other_event(event, payload)
            _emit(None)
    except BaseException:  # noqa: BLE001 - fail closed on anything
        if is_pre or event not in EVENTS:
            deny(ERROR_REASON)
            _spool({"event": event or "?", "decision": "deny", "rule_ids": [], "reason": "co-guard error"})
        else:
            _emit(None)
    finally:
        if watchdog is not None:
            watchdog.cancel()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
