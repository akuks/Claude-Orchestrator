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

Ruling R21: the very first thing PreToolUse does (before any evaluation) is
append ``{"event":"PreToolUse","tool_use_id":..,"seen":true}`` to the spool;
the worker's watchdog requires that record for every tool result. If it
cannot be written, the call is denied.

A request that was never filed (connection refused, 4xx, or not accepted
within FILE_BUDGET seconds) denies at once with "approval service
unreachable - action blocked"; after filing, 3 refused connections in a row
do the same.

The approval API must be ``http://<literal IP>:<port>``, the POST must answer
``status: "pending"`` with an id, and every GET must carry that id and this
call's ``command_hash``; anything else (and any 4xx - e.g. 409 when a request
with this idempotency key but a different body is already pending - or no
``CO_API_TOKEN``) denies at once. The POST also sends ``wait_left`` (seconds to
this hook's deadline) so the server expires the request no later. The decision is
printed BEFORE the best-effort report.

ConfigChange (unverified with a real CLI): exit 2 blocks a settings change
that sets ``disableAllHooks`` / ``allowManagedHooksOnly`` or disables the
co-guard plugin.

Every non-allow decision is appended to ``$CO_TASK_DIR/spool.jsonl`` (decision,
rule ids, tool, a redacted summary of at most 300 chars - never raw secrets)
and best-effort POSTed to ``/guard/report`` (0.5 s timeout). Other events
(SessionStart, Stop, SessionEnd, SubagentStart, SubagentStop, PostToolUse of
an approved call) are spooled only.
"""

import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# hooks.json PreToolUse timeout = APPROVAL_WAIT_MAX + 30 (Global Constraints).
APPROVAL_WAIT_MAX = 900
HOOK_TIMEOUT = APPROVAL_WAIT_MAX + 30
EXIT_MARGIN = 10  # always answer this long before HOOK_TIMEOUT (>= 5 s)
POLL_SECONDS = 2.0
POLL_MIN = 0.5
FILE_BUDGET = 10.0  # seconds to get a request filed before giving up
UNREACHABLE = "approval service unreachable \u2014 action blocked"
REPORT_TIMEOUT = 0.5
SUMMARY_MAX = 300
ERROR_REASON = "co-guard error — action blocked for safety"
EVENTS = ("PreToolUse", "PostToolUse", "SessionStart", "Stop", "SessionEnd", "SubagentStart", "SubagentStop",
          "ConfigChange")
# Ruling R17: every tool goes through the hook; rules.evaluate decides by tool name.
TOOL_MATCHER = ".*"
CONFIG_MATCHER = "user_settings|project_settings|local_settings"


class Refused(Exception):
    """The approval API cannot be trusted or used: deny now, do not wait."""

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
def _spool_strict(record):
    """Append one line to the spool; raises when it cannot."""
    tdir = os.environ.get("CO_TASK_DIR")
    if not tdir:
        raise RuntimeError("CO_TASK_DIR is not set")
    record = dict(record, ts=round(time.time(), 3))
    line = json.dumps(record, default=str) + "\n"
    fd = os.open(os.path.join(tdir, "spool.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if os.write(fd, line.encode()) != len(line.encode()):
            raise OSError("short spool write")
    finally:
        os.close(fd)


def _spool(record):
    try:
        _spool_strict(record)
    except (OSError, RuntimeError):
        pass


def _api(policy):
    """The approval API: http://<literal IP>:<port> only (C2: a hostname such as
    localhost may resolve to an address an agent can listen on)."""
    base = str(policy.get("api_base") or "").rstrip("/")
    u = urllib.parse.urlsplit(base)
    try:
        ipaddress.ip_address(urllib.parse.unquote(u.hostname or ""))
        ok = u.scheme == "http" and u.port is not None and u.path in ("", "/") and not u.username
    except ValueError:
        ok = False
    if not ok:
        raise Refused("the approval API address is not a literal IP and port")
    return base


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # never follow a redirect away from the approval API


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _request(method, url, body=None, timeout=5.0, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + os.environ.get("CO_API_TOKEN", ""))
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with _OPENER.open(req, timeout=max(0.1, timeout)) as resp:
        return json.loads(resp.read().decode() or "null")


def post_report(policy, record):
    """Best effort, AFTER the decision is printed."""
    try:
        _request("POST", _api(policy) + "/guard/report", dict(record, task_id=policy.get("task_id")),
                 timeout=REPORT_TIMEOUT)
    except Exception:  # noqa: BLE001 - best effort only
        pass


# -- approvals --------------------------------------------------------------
def poll_seconds(policy):
    try:
        v = float(policy.get("approval_poll_seconds") or POLL_SECONDS)
    except (TypeError, ValueError):
        v = POLL_SECONDS
    return max(POLL_MIN, v)


def wait_seconds(policy):
    try:
        w = float(policy.get("approval_wait_seconds", 300))
    except (TypeError, ValueError):
        w = 300.0
    return max(0.0, min(w, float(APPROVAL_WAIT_MAX)))


def request_approval(policy, payload, verdict, summary, deadline):
    """('approved' | 'rejected' | 'expired' | 'timeout' | 'refused', request id, detail).

    Only an approval this hook filed counts: the POST must answer
    ``{"id": ..., "status": "pending"}`` (Ruling R2) and every GET must carry
    that same id. 4xx answers, a missing token or an untrusted API address
    refuse at once; network errors and 5xx are retried until the deadline."""
    token = os.environ.get("CO_API_TOKEN")
    if not token:
        return "refused", None, "no CO_API_TOKEN for this task"
    try:
        base = _api(policy)
    except Refused as e:
        return "refused", None, str(e)
    tool = str(payload.get("tool_name") or "")
    canon = json.dumps({"tool_name": tool, "tool_input": payload.get("tool_input")}, sort_keys=True, default=str)
    command_hash = hashlib.sha256(canon.encode()).hexdigest()
    body = {
        "task_id": policy.get("task_id") or os.environ.get("CO_TASK_ID"),
        "rule_ids": [h.rule_id for h in verdict.hits],
        "tool": tool,
        "summary": redact(summary, 2000),
        "cwd": str(payload.get("cwd") or policy.get("cwd") or ""),
        "reason": redact("; ".join(h.reason for h in verdict.hits), 2000),
        "command_hash": command_hash,
    }
    idem = hashlib.sha256((command_hash + ":" + str(payload.get("tool_use_id") or "")).encode()).hexdigest()
    poll = poll_seconds(policy)
    req_id = None
    refused = 0  # consecutive connection-refused errors
    file_by = time.monotonic() + FILE_BUDGET  # no person can see an unfiled request: do not wait long
    while time.monotonic() < deadline:
        if req_id is None and time.monotonic() > file_by:
            return "refused", None, UNREACHABLE
        left = deadline - time.monotonic()
        try:
            if req_id is None:
                # the server never keeps the request open past this hook's own deadline
                body["wait_left"] = round(max(0.0, left), 1)
                res = _request("POST", base + "/guard/approvals", body, timeout=min(5.0, left),
                               headers={"Idempotency-Key": idem})
                rid = res.get("id") if isinstance(res, dict) else None
                if not isinstance(rid, (str, int)) or isinstance(rid, bool) or str(rid) == "" \
                        or res.get("status") != "pending":
                    return "refused", None, "the approval API gave an unexpected answer"
                req_id = str(rid)
            else:
                res = _request("GET", f"{base}/guard/approvals/{urllib.parse.quote(req_id, safe='')}",
                               timeout=min(5.0, left))
                if not isinstance(res, dict) or str(res.get("id")) != req_id:
                    return "refused", req_id, "the approval API answered for another request"
                if res.get("command_hash") != command_hash:
                    # the request behind this id is not the action this hook is checking
                    return "refused", req_id, "the approval API answered for another action"
                status = res.get("status")
                if status in ("approved", "rejected", "expired"):
                    return status, req_id, ""
                if status != "pending":
                    return "refused", req_id, "the approval API gave an unknown status"
        except urllib.error.HTTPError as e:
            refused = 0
            if 400 <= e.code < 500:
                if req_id is None:
                    return "refused", None, f"{UNREACHABLE} (HTTP {e.code})"
                return "refused", req_id, f"the approval API refused the request (HTTP {e.code})"
        except Exception as e:  # noqa: BLE001 - API down / 5xx / bad JSON: keep trying until the deadline
            if isinstance(e, ConnectionRefusedError) or isinstance(getattr(e, "reason", None),
                                                                    ConnectionRefusedError):
                refused += 1
                if refused >= 3:
                    return "refused", req_id, UNREACHABLE
            else:
                refused = 0
        else:
            refused = 0
        time.sleep(max(0.0, min(poll, deadline - time.monotonic())))
    return "timeout", req_id, ""


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
        rec = dict(base, decision="deny", reason=redact(reason))
        _spool(rec)
        deny("CO-GUARD: blocked — " + redact(reason, 1000))
        return post_report(policy, rec)
    hard_deadline = started + HOOK_TIMEOUT - EXIT_MARGIN
    deadline = min(time.monotonic() + wait_seconds(policy), hard_deadline - 2)
    status, req_id, detail = request_approval(policy, payload, verdict, summary, deadline)
    rec = dict(base, decision="approved" if status == "approved" else "deny", status=status, request_id=req_id,
               reason=redact(reason))
    if detail:
        rec["detail"] = detail
    _spool(rec)
    if status == "approved":
        context_only(f"co-guard: a person approved this action (request {req_id}).")
    else:
        if status == "refused":
            deny(f"CO-GUARD: {detail} (request {req_id or 'not filed'}). It needed a person's approval: "
                 + redact(reason, 1000) + ". Do not retry the same action; explain what you needed instead.")
        else:
            why = {"rejected": "a person rejected it", "expired": "the request expired",
                   "timeout": "no decision in time"}[status]
            deny(f"CO-GUARD: blocked — needs approval and {why} (request {req_id}): "
                 + redact(reason, 1000) + ". Do not retry the same action; explain what you needed instead.")
    return post_report(policy, rec)


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


def _truthy(value):
    """Claude Code's check: anything but undefined / null / false / "false" counts as set."""
    return value is not None and value is not False and value != "false"


def _disables_guard(data):
    if not isinstance(data, dict):
        return False
    if _truthy(data.get("disableAllHooks")) or _truthy(data.get("allowManagedHooksOnly")):
        return True
    plugins = data.get("enabledPlugins")
    return isinstance(plugins, dict) and any(
        str(k).split("@", 1)[0] == "co-guard" and v is False for k, v in plugins.items())


def config_change(payload):
    """ConfigChange (UNVERIFIED with a real CLI, Ruling R19b): exit 2 blocks a
    settings change that would switch co-guard's hooks off."""
    path = payload.get("file_path")
    if not isinstance(path, str) or not path:
        raise ValueError("no file_path")
    # any error - including a DELETED settings file (FileNotFoundError) - blocks
    # the change in main(): fail closed
    with open(path, encoding="utf-8") as f:
        data = json.loads(f.read())
    if not isinstance(data, dict):
        raise ValueError("settings are not an object")
    if _disables_guard(data):
        return "co-guard: settings that switch off co-guard's hooks are not allowed during a task"
    return None


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
            # Ruling R21: proof for the worker's watchdog that this call reached
            # co-guard - written FIRST; if it cannot be written, deny.
            _spool_strict({"event": "PreToolUse", "tool_use_id": payload.get("tool_use_id"), "seen": True})
            pre_tool_use(payload, load_policy(), started)
        elif event == "ConfigChange":
            why = config_change(payload)
            _spool({"event": event, "source": redact(payload.get("source"), 50),
                    "decision": "blocked" if why else "allowed"})
            if why:
                sys.stderr.write(why + "\n")
                return 2
            _emit(None)
        else:
            other_event(event, payload)
            _emit(None)
    except BaseException:  # noqa: BLE001 - fail closed on anything
        if is_pre or event not in EVENTS:
            deny(ERROR_REASON)
            _spool({"event": event or "?", "decision": "deny", "rule_ids": [], "reason": "co-guard error"})
        elif event == "ConfigChange":
            _spool({"event": event, "decision": "blocked", "reason": "co-guard error"})
            sys.stderr.write("co-guard: settings change blocked (it could not be checked)\n")
            return 2
        else:
            _emit(None)
    finally:
        if watchdog is not None:
            watchdog.cancel()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
