"""co-guard server routes.

``router`` (no auth): ``GET /guard/whoami`` (Ruling R22).

``api`` (mounted behind ``current_principal``): action approvals filed by
co-guard's hook and decided by people, and the hook's live decision reports.

* ``POST /guard/approvals`` - agent token only, for its own task (Ruling R2
  body + ``wait_left``; ``Idempotency-Key`` header; body <= BODY_LIMIT).
  201 ``{id, status: "pending", expires_at}``; a retry with the same key while
  that request is still pending answers 200 with the same id - only when its
  stored content (rule_ids, tool, summary, cwd, reason, command_hash) is the
  same, else 409 "conflicting request" (the hook denies). A partial unique
  index keeps one pending row per (task, key). At most 20 pending and 100
  total requests per task (429). Once it is decided or expired the key files a
  NEW request (the hook only re-POSTs a key while retrying before a decision),
  so every approval answers exactly one waiting hook (one-shot).
* ``GET /guard/approvals/{id}`` - the agent of the request's task (``{id,
  status, command_hash}`` only; the hook checks the hash is its own call's), or a person who can see the task (the full card); else 404.
* ``POST /guard/approvals/{id}/approve|reject`` - people only (agent tokens are
  refused by the allowlist in auth.py): an admin, or a maintainer of the task's
  effective project who does not own the task (Ruling R25, two-person rule);
  ad-hoc tasks outside a project thread: admins. Invisible -> 404; past expiry
  -> 409 "expired"; already decided -> 409.
* ``GET /guard/approvals`` - ``?status=pending`` (default): the requests the
  viewer may decide; ``?task_id=X``: every request of a task the viewer can
  see, each with ``can_decide``. Task 5 (UI): the approvals badge / Home
  "Needs you" count = ``len(GET /approvals) + len(GET /guard/approvals)``.
* ``POST /guard/report`` - agent token only, own task: the hook's decision
  record becomes a ``guard`` event of the live run right away (same shape as
  the spool ingestion, which then skips it); a deny that names a request the
  hook gave up on expires that request. Only PreToolUse deny/approved records
  are taken, an "approved" one only for a request a person approved (the
  agent can read the token and call this too) and then with the stored
  request's tool/summary/rule_ids, at most GUARD_REPORT_CAP per run.

Grants and deny-and-resume (Phase 2, ``guard.grants``):

* ``POST /guard/approvals`` also takes ``grant_key`` (64 hex, part of the
  idempotency content). When live RUN grants of the task's thread cover every
  rule id with the right key, it records an ``approved`` row at once
  (``decided_by`` the grant's creator, reason ``covered by run grant <ids>``,
  ``grant_ids``, each grant's ``use_count`` + 1, no notification) and answers
  ``{id, status: "approved", expires_at, command_hash, grant_ids}``. Otherwise
  the pending answer carries ``resume_until`` (ISO) when the project's
  ``on_no_answer`` is ``deny_and_resume`` and the call has a grant key
  (``created_at + RESUME_WINDOW``), else null.
* Phases (``_state``): without ``resume_until`` nothing changes (Phase 1).
  With it, a pending request is in the inline phase while its task runs and
  ``expires_at`` is ahead (and the hook has not reported ``filed``); then in
  the resume phase (``delivery`` "resume", persisted) until ``resume_until``.
  A cancelled task expires it. The agent's GET of a request approved in the
  resume phase still reads ``pending``: that approval is the follow-up's.
* ``POST /guard/approvals/{id}/approve`` takes ``{"scope": "once"|"run"}``
  (default once). ``run`` makes one run grant per grantable rule id for the
  thread (12 h; 409 when nothing is grantable or the request has no grant
  key). In the resume phase an approval also makes the follow-up's once
  grant(s) and joins / defers / spawns the thread's follow-up; a reject only
  records. Inside ``DECIDE_MARGIN`` of the inline wait a request with
  ``resume_until`` answers 409 "the agent is giving up on this request; try
  again in a few seconds" (without it: 409 "expired", as before). R25 holds.
* The card adds ``delivery``, ``resume_until``, ``scope``,
  ``followup_task_id``, ``grant_ids`` and ``run_keys``, and (final wave) the
  server's ``can_approve_run`` / ``run_reason`` (``grants.run_grant_problem``:
  every rule id grantable and a verified key wherever a rule is keyed by the
  command); ``run`` approval refuses with 409 whenever it is false - nothing is
  silently skipped. The pending list (the Home / badge count) includes
  resume-phase requests.
* Final wave: a request names at most ``MAX_RULE_IDS`` (50) rule ids. A
  retried POST (same ``Idempotency-Key``, identical content) within
  ``REPLAY_WINDOW`` seconds of a row a run grant answered replays that row
  (200) instead of recording a second use. A Bash/Monitor summary holding a
  ``HIDDEN_CHARS`` character, and a non-shell call whose ``full_summary`` is
  longer than ``SUMMARY_LIMIT`` or changed by redaction, get no grant key.
* ``POST /guard/report`` also takes ``{"event": "guard_error", "hook_event",
  "error"}`` (a hook other than PreToolUse failed): a run-log line only.
  ``GET /guard/health`` (people): the hook errors of the tasks the viewer can
  see in the last 48 h - count and latest (the Agent guard page's line).
* ``POST /guard/report``: a deny with ``status: "filed"`` moves a request
  with ``resume_until`` to the resume phase instead of expiring it; a
  ``granted`` record is taken only when every grant id is a live run grant
  of the task's thread or a live once grant of this task - once grants are
  spent (``used_at``) and, when the record reaches the run log, every grant's
  ``use_count`` + 1 (the spool ingestion does the same for records not
  reported, and spends once grants of every granted line).
* ``GET /guard/grants?root_id=`` (any step id names its thread; people who
  can see that step, else 404; without it: every grant whose task the viewer
  can see; live grants unless ``include_inactive``), each
  with ``can_revoke`` (``_may_decide`` on the grant's task). ``POST
  /guard/grants/{id}/revoke`` - the same people (403 otherwise; 409 when
  already revoked). Agent tokens: 403 (allowlist).

Policy settings (Phase 2; people only - agent tokens are refused by the
allowlist in auth.py and again here):

* ``GET /guard/rules`` - org rules (any person), or with ``?project_id=`` that
  project's rules (visible project, else 404). ``POST /guard/rules`` -
  ``{scope: org|project, project_id?, name?, kind, match, pattern, reason,
  enabled}``; org rules: admins; project rules: the project's maintainers and
  admins (invisible -> 404, contributor -> 403). ``PATCH|DELETE
  /guard/rules/{rule_id}`` (the row id) under the same rules. Patterns follow
  ``custom.validate_pattern`` (400; a regex that does not compile ->
  ``invalid regex: ...``); reasons are cleaned and cut to 200 chars
  (``custom.clean_reason``); the slug comes from the name, else the reason,
  else the pattern, unique within its scope and project (``-2``, ``-3``...)
  and never changes; at most ``RULES_PER_SCOPE`` rules per scope (org; each
  project) -> 409.
* ``GET /guard/builtins`` - ``[{id, reason, hard_reason, disableable}]``.
* ``GET /guard/org-settings`` (any person) / ``PUT`` (admins):
  ``{disabled_rules, dev_db_patterns, ground_rules, model_tiers}`` (a field left out stays; ``ground_rules`` is replaced
  whole after ``ground_rules.validate``, so a key left out takes its default, 400 on a bad one); only
  ``rules.DISABLEABLE`` ids may be switched off and every dev pattern must pass
  ``dbrules.check_dev_pattern`` (400).

Expiry: ``expires_at = created_at + min(approval_wait_seconds, wait_left)`` -
the wait in the task's own policy file (what its hook waits), else the
project's, clamped 60-900; ``wait_left`` is what the hook has left. A
pending request past ``expires_at``, or whose task is no longer
running, reads as ``expired`` (lazily, persisted when read). Decisions close
``DECIDE_MARGIN`` seconds before ``expires_at``: the hook polls every 2 s and
stops at its own deadline, so a later approval could land on a call that was
already denied.
"""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from .. import notifications, tiers
from ..access import (
    ALL,
    can_see_task,
    effective_project_id,
    maintained_project_ids,
    project_role,
    require_project_role,
    require_task,
    task_visibility_clause,
)
from ..constants import Status
from ..database import SessionLocal
from ..guard import custom as guard_custom
from ..guard import dbrules as guard_dbrules
from ..guard import grants as guard_grants
from ..guard import policy_store
from ..guard import rules as guard_rules
from ..guard import runtime as guard_runtime
from ..guard import co_hook
from ..guard.co_hook import SUMMARY_MAX as HOOK_SUMMARY_MAX
from ..models import ActionApproval, ActionGrant, ActionRule, GuardOrgSettings, Project, Task
from ..redaction import HIDDEN_CHARS, redact
from ..resume import ground_rules

router = APIRouter(prefix="/guard", tags=["guard"])
api = APIRouter(prefix="/guard", tags=["guard"])

_CHALLENGE = re.compile(r"[0-9a-f]{16,128}")

DECIDE_MARGIN = 5  # seconds before expires_at when decisions close
MAX_PENDING_PER_TASK = 20
MAX_TOTAL_PER_TASK = 100  # every status: an agent cannot flood the approvals table
LIST_LIMIT = 200
BODY_LIMIT = 16 * 1024  # bytes, POST /guard/approvals and /guard/report
SUMMARY_LIMIT = 8000  # the full command a person decides on (the hook cuts to fit BODY_LIMIT)
# Stored request fields, compared whole when an idempotency key is reused.
CONTENT_FIELDS = ("rule_ids", "tool", "summary", "cwd", "reason", "command_hash", "grant_key")
GIVING_UP = "the agent is giving up on this request; try again in a few seconds"
MAX_RULE_IDS = co_hook.MAX_RULE_IDS  # rule ids per request (the hook sends the first 50, once-only beyond)
REPLAY_WINDOW = 60  # s: a retried POST answered from a run grant replays that answer
# HIDDEN_CHARS (app.redaction): characters that can make a command read differently from
# what runs - a shell summary holding one gets no grant key, so it can only be approved
# once, inline, after the card revealed them.


@router.get("/whoami")
async def whoami(challenge: str = Query(...)):
    """Ruling R22: proves this is the orchestrator itself (HMAC with a per-boot
    secret held only in server memory). No auth: it reveals nothing - the
    answer is useless without the secret, and the worker checks it before
    every task so an agent's listener on the approval address is caught."""
    if not _CHALLENGE.fullmatch(challenge):
        raise HTTPException(status_code=422, detail="bad challenge")
    return {"proof": guard_runtime.whoami_proof(challenge)}


# ---- helpers -----------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite: naive UTC


def _worker(request: Request):
    return getattr(request.app.state, "worker", None)


def _run_secrets(request: Request, task_id: str) -> list[str]:
    w = _worker(request)
    return list(getattr(w, "_run_secrets", {}).get(task_id) or []) if w is not None else []


def _require_agent_for(p, task_id) -> None:
    """Agent tokens only, and only for their own task (not the thread)."""
    if not p.is_agent:
        raise HTTPException(403, "Only a task's agent token may call this")
    if not task_id or task_id != p.token.task_id:
        raise HTTPException(403, "Token not allowed for this task")


async def _limit_body(request: Request) -> None:
    """413 for a body over BODY_LIMIT (declared length, or the bytes read)."""
    declared = request.headers.get("content-length")
    try:
        too_big = declared is not None and int(declared) > BODY_LIMIT
    except ValueError:
        raise HTTPException(400, "Bad Content-Length")
    if too_big or len(await request.body()) > BODY_LIMIT:
        raise HTTPException(413, "Request body too large")


def _cut(text: str, limit: int) -> str:
    """Bound a stored text; a cut is visible (UI: render as text only)."""
    if len(text) <= limit:
        return text
    mark = f"… (truncated, {len(text)} chars)"
    return text[: limit - len(mark)] + mark


def _wait_seconds(task_id: str, project: Project | None) -> int:
    """What this task's hook waits: its policy file (written at task start),
    else the project's setting; clamped 60-900."""
    try:
        with open(guard_runtime.task_dir(task_id) / guard_runtime.POLICY_NAME, encoding="utf-8") as f:
            policy = json.load(f)
        if isinstance(policy, dict) and "approval_wait_seconds" in policy:
            return guard_runtime.clamp_wait(policy["approval_wait_seconds"])
    except (OSError, ValueError):
        pass
    return guard_runtime.clamp_wait(
        project.approval_wait_seconds if project is not None else guard_runtime.APPROVAL_WAIT_DEFAULT
    )


def _state(a: ActionApproval, task_status: str | None, now: datetime | None = None) -> tuple[str, str]:
    """(effective status, delivery). Without ``resume_until`` (Phase 1, and
    ``on_no_answer`` deny): a pending request past ``expires_at`` or whose task
    stopped running is expired. With it: pending in the inline phase (task
    running, before ``expires_at``, the hook not given up), then pending in the
    resume phase until ``resume_until``; a cancelled (or gone) task expires it."""
    now = now or _now()
    delivery = a.delivery or "inline"
    if a.status != "pending":
        return a.status, delivery
    if a.resume_until is None:
        if now >= _aware(a.expires_at) or task_status != Status.RUNNING:
            return "expired", delivery
        return "pending", delivery
    if task_status is None or task_status == Status.CANCELLED or now >= _aware(a.resume_until):
        return "expired", delivery
    if delivery == "inline" and (now >= _aware(a.expires_at) or task_status != Status.RUNNING):
        delivery = "resume"
    return "pending", delivery


def _effective(a: ActionApproval, task_status: str | None, now: datetime | None = None) -> str:
    return _state(a, task_status, now)[0]


async def _settle(s, a: ActionApproval, task: Task | None) -> str:
    """Persist a lazy expiry or the move to the resume phase; returns the
    effective status."""
    st, delivery = _state(a, task.status if task else None)
    if a.status == "pending" and (st == "expired" or delivery != a.delivery):
        values = {"status": "expired"} if st == "expired" else {"delivery": delivery}
        await s.execute(
            update(ActionApproval)
            .where(ActionApproval.id == a.id, ActionApproval.status == "pending")
            .values(**values)
        )
        await s.commit()
        for k, v in values.items():
            setattr(a, k, v)
    return st


def _live_pending():
    """SQL: a pending row that still reads as pending (needs the Task join)."""
    now = _now()
    inline = and_(ActionApproval.resume_until.is_(None), ActionApproval.expires_at > now,
                  Task.status == Status.RUNNING)
    resume = and_(ActionApproval.resume_until.is_not(None), ActionApproval.resume_until > now,
                  Task.status != Status.CANCELLED)
    return and_(ActionApproval.status == "pending", or_(inline, resume))


async def _may_decide(s, p, task: Task) -> bool:
    """Ruling R25: an admin; or a maintainer of the task's effective project
    who is not the task's owner. Ad-hoc tasks outside a project thread: admins."""
    if p.is_agent or not p.user.active:
        return False
    if p.is_admin:
        return True
    pid = await effective_project_id(s, task)
    if pid is None:
        return False
    if await project_role(s, p, pid) != "maintainer":
        return False
    return task.created_by != p.user.id


class ActionApprovalOut(BaseModel):
    id: str
    task_id: str
    task_title: str = ""
    project_id: Optional[str] = None
    requested_by: Optional[str] = None
    rule_ids: list = []
    tool: str = ""
    summary: str = ""
    cwd: str = ""
    reason: str = ""
    command_hash: str = ""
    status: str
    created_at: datetime
    expires_at: datetime
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_reason: Optional[str] = None
    can_decide: bool = False
    # Phase 2
    delivery: str = "inline"  # inline | resume (| dropped: a deferred follow-up whose thread was cancelled)
    resume_until: Optional[datetime] = None
    scope: Optional[str] = None
    followup_task_id: Optional[str] = None
    grant_ids: list = []
    run_keys: list = []  # what "approve for this run" covers: [{rule_id, key_kind, key}]
    # whether "approve for this run" can work (server-computed), else why not
    can_approve_run: bool = False
    run_reason: Optional[str] = None


async def _card(s, a: ActionApproval, task_title: str, status: str, can_decide: bool,
                delivery: str | None = None, cache: dict | None = None) -> ActionApprovalOut:
    problem = await guard_grants.run_grant_problem(s, a, cache)
    return ActionApprovalOut(
        delivery=delivery or a.delivery or "inline", resume_until=_aware(a.resume_until) if a.resume_until else None, scope=a.scope,
        followup_task_id=a.followup_task_id, grant_ids=list(a.grant_ids or []),
        run_keys=guard_grants.run_keys(a), can_approve_run=problem is None, run_reason=problem,
        id=a.id, task_id=a.task_id, task_title=task_title or "", project_id=a.project_id,
        requested_by=a.requested_by, rule_ids=list(a.rule_ids or []), tool=a.tool or "",
        summary=a.summary or "", cwd=a.cwd or "", reason=a.reason or "", command_hash=a.command_hash or "",
        status=status, created_at=_aware(a.created_at), expires_at=_aware(a.expires_at),
        decided_by=a.decided_by, decided_at=a.decided_at and _aware(a.decided_at),
        decision_reason=a.decision_reason, can_decide=can_decide and status == "pending",
    )


# ---- filing (agent) ----------------------------------------------------------------------


RULE_ID_RE = r"^[a-z0-9-]+(\.[a-z0-9-]+)?$"
MAX_COVERED_PER_TASK = 1000  # Ruling P25: auto-approved (covered) rows, apart from the human-facing cap


class FileBody(BaseModel):
    task_id: str
    rule_ids: list[Annotated[str, Field(pattern=RULE_ID_RE, max_length=100)]] = Field([], max_length=MAX_RULE_IDS)
    tool: str = ""
    summary: str = ""
    cwd: str = ""
    reason: str = ""
    command_hash: str = ""
    # Phase 2: the hook's grant_key of the call (sha256 hex), or none
    grant_key: str = Field("", pattern=r"^([0-9a-f]{64})?$")
    # non-shell tools: the call's input, so the server can check grant_key (never stored)
    tool_input: Any = None
    # seconds left before the hook's own deadline (not part of the content)
    wait_left: Optional[float] = None


def _verified_key(body: FileBody, tool: str, summary: str, secrets: list[str]) -> str:
    """Ruling P23: the grant_key only when it stands for what the card shows.
    Bash/Monitor: the key is the hash of the stored summary (so it was neither
    cut nor changed by redaction) and the summary has no hidden characters
    (``HIDDEN_CHARS``). Other tools: the key is the hash of the sent
    ``tool_input`` and the stored summary is that input's ``full_summary``
    exactly - within ``SUMMARY_LIMIT`` and unchanged by redaction. Otherwise
    "" - the request can be approved once only."""
    key = body.grant_key
    if not key or tool != body.tool:
        return ""
    if tool in co_hook.SHELL_TOOLS:
        ok = co_hook.grant_key(tool, {"command": summary}) == key and not HIDDEN_CHARS.search(summary)
    else:
        if body.tool_input is None:
            return ""
        try:
            full = co_hook.full_summary(tool, body.tool_input)
            ok = (co_hook.grant_key(tool, body.tool_input) == key and len(full) <= SUMMARY_LIMIT
                  and redact(full, secrets) == full and summary == full)
        except Exception:  # noqa: BLE001 - an input we cannot check is not verified
            ok = False
    return key if ok else ""


def _content(body: FileBody, secrets: list[str]) -> dict:
    """The request's stored form: redacted first, then bounded."""
    tool = _cut(redact(body.tool, secrets), 200)
    summary = _cut(redact(body.summary, secrets), SUMMARY_LIMIT)
    return {
        "rule_ids": [redact(str(r), secrets)[:64] for r in body.rule_ids][:MAX_RULE_IDS],
        "tool": tool,
        "summary": summary,
        "cwd": _cut(redact(body.cwd, secrets), 1000),
        "reason": _cut(redact(body.reason, secrets), 2000),
        "command_hash": body.command_hash[:128],
        "grant_key": _verified_key(body, tool, summary, secrets),
    }


def _same_content(a: ActionApproval, content: dict) -> bool:
    return all(
        (list(a.rule_ids or []) if k == "rule_ids" else getattr(a, k)) == content[k] for k in CONTENT_FIELDS
    )


async def _find_covered(s, task_id: str, key: str, now: datetime) -> ActionApproval | None:
    """The latest row this key got answered from a run grant within
    ``REPLAY_WINDOW`` (a retried POST replays it: one call, one use)."""
    return (
        await s.execute(
            select(ActionApproval)
            .where(ActionApproval.task_id == task_id, ActionApproval.idempotency_key == key, _is_covered_row(),
                   ActionApproval.created_at >= now - timedelta(seconds=REPLAY_WINDOW))
            .order_by(ActionApproval.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _find_pending(s, task_id: str, key: str) -> ActionApproval | None:
    return (
        await s.execute(
            select(ActionApproval)
            .where(
                ActionApproval.task_id == task_id,
                ActionApproval.idempotency_key == key,
                ActionApproval.status == "pending",
            )
            .order_by(ActionApproval.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _filed(a: ActionApproval) -> dict:
    if a.status == "approved":  # covered by a run grant of the thread
        return {"id": a.id, "status": "approved", "expires_at": _aware(a.expires_at),
                "command_hash": a.command_hash, "grant_ids": list(a.grant_ids or [])}
    return {"id": a.id, "status": "pending", "expires_at": _aware(a.expires_at),
            "resume_until": _aware(a.resume_until).isoformat() if a.resume_until else None}


def _replay(a: ActionApproval, content: dict, response: Response) -> dict:
    """The pending request filed under this key - only if it is the same
    request. The agent shares the hook's token and can predict its key: a
    planted row with other content must never stand in for the hook's call."""
    if not _same_content(a, content):
        raise HTTPException(409, "conflicting request")
    response.status_code = 200
    return _filed(a)


COVERED_REASON = "covered by run grant "


def _is_covered_row():
    """SQL: an auto-approved row of a call a run grant covered."""
    return and_(ActionApproval.status == "approved", ActionApproval.scope == "run",
                func.coalesce(ActionApproval.decision_reason, "").like(COVERED_REASON + "%"))


async def _covered(s, task: Task, content: dict, key: str | None, project_id, now: datetime):
    """An ``approved`` row for a call the thread's live run grants cover (no
    notification), or None. Not committed."""
    grants = await guard_grants.thread_cover(
        s, root_id=task.root_id or task.id, rule_ids=content["rule_ids"], tool=content["tool"],
        grant_key=content["grant_key"], now=now)
    if not grants:
        return None
    ids = [g.id for g in grants]
    a = ActionApproval(
        task_id=task.id, project_id=project_id, requested_by=task.created_by, idempotency_key=key,
        status="approved", created_at=now, expires_at=now, decided_by=grants[0].created_by, decided_at=now,
        decision_reason=COVERED_REASON + ", ".join(ids), scope="run", grant_ids=ids, **content,
    )
    s.add(a)
    await guard_grants.record_use(s, grants, count=True, now=now)
    return a


@api.post("/approvals", status_code=201, dependencies=[Depends(_limit_body)])
async def file_request(
    body: FileBody,
    request: Request,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    p = request.state.principal
    _require_agent_for(p, body.task_id)
    key = (idempotency_key or "").strip()[:128] or None
    now = _now()
    async with SessionLocal() as s:
        task = await s.get(Task, body.task_id)
        if task is None or task.status != Status.RUNNING:
            raise HTTPException(409, "Task is not running")
        content = _content(body, _run_secrets(request, task.id))
        if key is not None:
            prior = await _find_pending(s, task.id, key)
            if prior is not None and await _settle(s, prior, task) == "pending":
                return _replay(prior, content, response)
            answered = await _find_covered(s, task.id, key, now)
            if answered is not None and _same_content(answered, content):
                response.status_code = 200  # the hook's retry of a call a run grant already answered
                return _filed(answered)
        is_covered = _is_covered_row()
        counts = (
            await s.execute(
                select(
                    func.count(ActionApproval.id).filter(~is_covered),
                    func.count(ActionApproval.id).filter(
                        ActionApproval.status == "pending", ActionApproval.expires_at > now
                    ),
                    func.count(ActionApproval.id).filter(is_covered),
                ).where(ActionApproval.task_id == task.id)
            )
        ).one()
        project_id = await effective_project_id(s, task)
        covered = await _covered(s, task, content, key, project_id, now) \
            if counts[2] < MAX_COVERED_PER_TASK else None
        if covered is not None:
            await s.commit()
            return _filed(covered)
        if counts[0] >= MAX_TOTAL_PER_TASK or counts[1] >= MAX_PENDING_PER_TASK:
            raise HTTPException(429, "Too many approval requests for this task")
        project = await s.get(Project, project_id) if project_id else None
        wait = float(_wait_seconds(task.id, project))
        if body.wait_left is not None and body.wait_left == body.wait_left:  # not NaN
            wait = max(0.0, min(wait, body.wait_left))  # never past the hook's own deadline
        a = ActionApproval(
            task_id=task.id,
            project_id=project_id,
            requested_by=task.created_by,
            idempotency_key=key,
            status="pending",
            created_at=now,
            expires_at=now + timedelta(seconds=wait),
            # deny_and_resume: the request outlives the inline wait (needs the call's grant key)
            resume_until=now + timedelta(seconds=guard_grants.RESUME_WINDOW)
            if project is not None and project.on_no_answer == "deny_and_resume" and content["grant_key"]
            else None,
            **content,
        )
        tid = task.id  # ORM attributes expire on rollback
        s.add(a)
        try:
            await s.commit()
        except IntegrityError:
            # Another POST with this key won the race (unique pending key).
            await s.rollback()
            prior = await _find_pending(s, tid, key) if key is not None else None
            if prior is None:
                raise HTTPException(409, "conflicting request")
            return _replay(prior, content, response)
        try:  # configured channels (redacted first line, no secrets); never blocks filing
            note = await notifications.action_waiting_message(s, a, task)
        except Exception:  # noqa: BLE001
            note = None
        if note is not None:
            notifications.post_in_background(*note)
        return _filed(a)


# ---- reading ------------------------------------------------------------------------------


@api.get("/approvals", response_model=list[ActionApprovalOut])
async def list_requests(
    request: Request,
    status: Literal["pending", "approved", "rejected", "expired", "all"] = "pending",
    task_id: Optional[str] = None,
):
    p = request.state.principal
    if p.is_agent:  # also refused by the allowlist
        raise HTTPException(403, "Token not allowed for this route")
    now = _now()
    async with SessionLocal() as s:
        stmt = select(ActionApproval, Task).join(Task, Task.id == ActionApproval.task_id)
        live = _live_pending()  # inline or resume phase (the Home / badge counts use this list)
        if status == "pending":
            stmt = stmt.where(live)
        elif status == "expired":
            stmt = stmt.where(
                or_(
                    ActionApproval.status == "expired",
                    and_(ActionApproval.status == "pending", ~live),
                )
            )
        elif status != "all":
            stmt = stmt.where(ActionApproval.status == status)
        if task_id is not None:
            task = await require_task(s, p, task_id)  # 404 when invisible
            stmt = stmt.where(ActionApproval.task_id == task.id)
            decidable = await _may_decide(s, p, task)
        else:
            # Only what the viewer may decide (Ruling R25), filtered in SQL.
            maintained = await maintained_project_ids(s, p)
            if maintained is not ALL:
                stmt = stmt.where(
                    ActionApproval.project_id.in_(maintained),
                    func.coalesce(Task.created_by, "") != p.user.id,
                )
            decidable = True
        rows = (
            await s.execute(stmt.order_by(ActionApproval.created_at.desc()).limit(LIST_LIMIT))
        ).all()
        out, cache = [], {}
        for a, t in rows:
            st, delivery = _state(a, t.status, now)
            out.append(await _card(s, a, t.title, st, decidable, delivery, cache))
    return out


@api.get("/approvals/{approval_id}")
async def get_request(approval_id: str, request: Request):
    p = request.state.principal
    async with SessionLocal() as s:
        a = await s.get(ActionApproval, approval_id)
        task = await s.get(Task, a.task_id) if a is not None else None
        if p.is_agent:
            if a is None or a.task_id != p.token.task_id:
                raise HTTPException(404, "Not found")
            st = await _settle(s, a, task)
            if a.delivery == "resume" and st == "approved":
                # approved in the resume phase: that answer is the follow-up's
                # once grant, never a late answer to a hook still polling
                st = "pending"
            # command_hash: the hook checks the id stands for ITS call
            return {"id": a.id, "status": st, "command_hash": a.command_hash}
        if a is None or task is None or not await can_see_task(s, p, task):
            raise HTTPException(404, "Not found")
        st = await _settle(s, a, task)
        return await _card(s, a, task.title, st, await _may_decide(s, p, task))


# ---- deciding (people) --------------------------------------------------------------------


class RejectBody(BaseModel):
    reason: Optional[str] = None


class ApproveBody(BaseModel):
    scope: Literal["once", "run"] = "once"


async def _decide(request: Request, approval_id: str, status: str, reason: str | None, scope: str = "once"):
    """Inline phase: the waiting hook sees the decision (``run`` also makes
    run grants for the thread). Resume phase: an approval makes the
    follow-up's once grant and joins / defers / spawns the thread's follow-up
    (``grants.create_resume_followup``); a reject only records."""
    p = request.state.principal
    spawned: list = []
    async with guard_grants.followup_lock():
        async with SessionLocal() as s:
            a = await s.get(ActionApproval, approval_id)
            task = await s.get(Task, a.task_id) if a is not None else None
            if a is None or task is None or not await can_see_task(s, p, task):
                raise HTTPException(404, "Not found")
            if not await _may_decide(s, p, task):
                raise HTTPException(403, "Not allowed to decide this request")
            st = await _settle(s, a, task)
            if st == "expired":
                raise HTTPException(409, "expired")
            if st != "pending":
                raise HTTPException(409, f"already {st}")
            now = _now()
            resume = a.resume_until is not None and a.delivery == "resume"
            if not resume and now >= _aware(a.expires_at) - timedelta(seconds=DECIDE_MARGIN):
                # the hook stops polling at expires_at: a decision now could land on both phases
                raise HTTPException(409, GIVING_UP if a.resume_until is not None else "expired")
            if resume and status == "approved" and await guard_grants.thread_cancelled(s, task):
                raise HTTPException(409, "the thread was cancelled, so no follow-up can start")
            res = await s.execute(
                update(ActionApproval)
                .where(ActionApproval.id == a.id, ActionApproval.status == "pending",
                       ActionApproval.delivery == a.delivery)
                .values(status=status, decided_by=p.user.id, decided_at=now,
                        decision_reason=(reason or None) and reason[:2000],
                        scope=scope if status == "approved" else None)
            )
            if res.rowcount != 1:
                await s.rollback()
                raise HTTPException(409, "the request just changed; try again")
            await s.refresh(a)
            if status == "approved":
                try:
                    made = await guard_grants.create_run_grants(s, a, task, p.user.id) if scope == "run" else []
                    if resume and not guard_grants.is_hex_key(a.grant_key):
                        raise ValueError("this request has no grant key, so it cannot resume")
                except ValueError as e:
                    await s.rollback()
                    raise HTTPException(409, str(e))
                a.grant_ids = [g.id for g in made]
                if resume:
                    await guard_grants.create_resume_followup(s, a, p.user.id, spawned=spawned)
            await s.commit()
            await s.refresh(a)
            jobs = [(t.id, t.priority, t.created_at) for t in spawned]
            out = await _card(s, a, task.title, a.status, False)
    w = _worker(request)
    for job in jobs:
        if w is not None:
            await w.submit(*job)
    return out


@api.post("/approvals/{approval_id}/approve", response_model=ActionApprovalOut)
async def approve(approval_id: str, request: Request, body: Optional[ApproveBody] = None):
    return await _decide(request, approval_id, "approved", None, (body.scope if body else "once"))


@api.post("/approvals/{approval_id}/reject", response_model=ActionApprovalOut)
async def reject(approval_id: str, request: Request, body: Optional[RejectBody] = None):
    return await _decide(request, approval_id, "rejected", (body.reason if body else None) or "Rejected")


# ---- live report (agent) -------------------------------------------------------------------


# What a "granted" report keeps: the hook's own spool record minus its ts, so the
# worker keys both copies alike (one TaskDetail line, one counted use).
GRANTED_REPORT_KEYS = ("event", "decision", "grant_ids", "tool", "rule_ids", "summary", "tool_use_id", "agent_id")
# What a guard_error report keeps (the hook's spooled record minus its ts).
GUARD_ERROR_KEYS = ("event", "hook_event", "error")


@api.post("/report", status_code=202, dependencies=[Depends(_limit_body)])
async def report(request: Request, rec: dict = Body(...)):
    """The hook's decision record (best effort, after it answered Claude Code)."""
    p = request.state.principal
    task_id = rec.get("task_id")
    _require_agent_for(p, task_id if isinstance(task_id, str) else None)
    rid = rec.get("request_id")
    if rec.get("decision") == "deny" and isinstance(rid, str) and rid:
        # The hook gave up on (or was refused) this request: it can no longer
        # be approved for a call that was already denied - unless the request
        # was filed for deny-and-resume: then it waits for a person (resume
        # phase) and an approval starts a follow-up instead.
        mine = and_(ActionApproval.id == rid, ActionApproval.task_id == task_id, ActionApproval.status == "pending")
        async with SessionLocal() as s:
            if rec.get("status") == "filed":
                await s.execute(update(ActionApproval)
                                .where(mine, ActionApproval.resume_until.is_not(None))
                                .values(delivery="resume"))
            await s.execute(update(ActionApproval)
                            .where(mine, ActionApproval.delivery != "resume")
                            .values(status="expired"))
            await s.commit()
    if rec.get("event") == "guard_error":
        # a hook other than PreToolUse failed (final wave, forge F88): a run-log
        # line only - the agent could send one too, so it changes nothing else
        rec = {k: str(rec[k])[:300] for k in GUARD_ERROR_KEYS if isinstance(rec.get(k), str)}
        w = _worker(request)
        accepted = bool(w is not None and hasattr(w, "report_guard") and await w.report_guard(task_id, rec))
        return {"accepted": accepted}
    # The token is in the agent's env, so the agent can call this too: accept
    # only what the hook reports (PreToolUse deny / approved / granted), an
    # "approved" record only for a request of this task a person approved, and
    # a "granted" one only for live grants of this task's thread (run) or of
    # this task (once).
    if rec.get("event") != "PreToolUse" or rec.get("decision") not in ("deny", "approved", "granted"):
        return {"accepted": False}
    grants = None
    if rec.get("decision") == "approved":
        async with SessionLocal() as s:
            a = await s.get(ActionApproval, rid) if isinstance(rid, str) and rid else None
        if a is None or a.task_id != task_id or a.status != "approved":
            return {"accepted": False}
        # What was approved is what the person saw, not what the caller says
        # (one short line like the hook's own record; the worker dedupes an
        # approved record with the spool copy by its request id).
        summ = " ".join((a.summary or "").split())
        if len(summ) > HOOK_SUMMARY_MAX:
            summ = summ[: HOOK_SUMMARY_MAX - 1] + "…"
        rec = dict(rec, tool=a.tool, rule_ids=list(a.rule_ids or []), summary=summ)
    elif rec.get("decision") == "granted":
        async with SessionLocal() as s:
            task = await s.get(Task, task_id)
            grants = await guard_grants.valid_use(s, task, rec.get("grant_ids")) if task is not None else None
            if not grants:
                return {"accepted": False}
            rec = {k: rec[k] for k in GRANTED_REPORT_KEYS if k in rec}
            # spent now, each on its own: a once grant already spent is a re-use (the
            # spool ingestion spends it again, idempotently, if this report is lost)
            if not await guard_grants.spend_once(s, grants):
                await s.rollback()
                return {"accepted": False}
            await s.commit()
    w = _worker(request)
    accepted = False
    if w is not None and hasattr(w, "report_guard"):
        accepted = await w.report_guard(task_id, rec)  # redacts with the run's secrets
    if accepted and grants:
        async with SessionLocal() as s:  # counted once: the spool ingestion skips a reported record
            await guard_grants.record_use(s, grants, count=True)
            await s.commit()
    return {"accepted": accepted}


# ---- health (people) -------------------------------------------------------------------------

HEALTH_WINDOW_HOURS = 48
HEALTH_SCAN_LIMIT = 5000


@api.get("/health")
async def guard_health(request: Request):
    """co-guard's doctor line (forge F88/F108): hook errors (``guard_error``
    run-log events: a hook other than PreToolUse failed) in the last
    ``HEALTH_WINDOW_HOURS`` hours, over the tasks the viewer can see - the
    count and the latest one. People only."""
    from ..models import TaskEvent

    p = request.state.principal
    _person(p)
    since = _now() - timedelta(hours=HEALTH_WINDOW_HOURS)
    async with SessionLocal() as s:
        visible = select(Task.id).where(await task_visibility_clause(s, p))
        rows = (await s.execute(
            select(TaskEvent.task_id, TaskEvent.payload, TaskEvent.created_at)
            .where(TaskEvent.type == "guard", TaskEvent.created_at >= since, TaskEvent.task_id.in_(visible))
            .order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc())
            .limit(HEALTH_SCAN_LIMIT)
        )).all()
    errors = [(tid, pl, at) for tid, pl, at in rows if isinstance(pl, dict) and pl.get("event") == "guard_error"]
    latest = errors[0] if errors else None
    return {"hook_errors": {
        "window_hours": HEALTH_WINDOW_HOURS,
        "count": len(errors),
        "latest_at": _aware(latest[2]) if latest else None,
        "latest_task_id": latest[0] if latest else None,
        "latest_hook_event": latest[1].get("hook_event") if latest else None,
        "latest_error": latest[1].get("error") if latest else None,
    }}


# ---- grants (people) -------------------------------------------------------------------------


class GrantOut(BaseModel):
    id: str
    root_id: str
    task_id: Optional[str] = None
    approval_id: Optional[str] = None
    scope: str
    rule_id: str
    key_kind: str
    key: str
    created_by: Optional[str] = None
    created_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    used_at: Optional[datetime] = None
    use_count: int = 0
    active: bool = False
    can_revoke: bool = False


def _grant_out(g: ActionGrant, can_revoke: bool, now: datetime) -> GrantOut:
    active = g.revoked_at is None and _aware(g.expires_at) > now and not (g.scope == "once" and g.used_at)
    return GrantOut(
        id=g.id, root_id=g.root_id, task_id=g.task_id, approval_id=g.approval_id, scope=g.scope, rule_id=g.rule_id,
        key_kind=g.key_kind, key=g.key, created_by=g.created_by, created_at=_aware(g.created_at),
        expires_at=_aware(g.expires_at), revoked_at=g.revoked_at and _aware(g.revoked_at), revoked_by=g.revoked_by,
        used_at=g.used_at and _aware(g.used_at), use_count=g.use_count or 0, active=active,
        can_revoke=can_revoke and active,
    )


async def _grant_task(s, g: ActionGrant) -> Task | None:
    """The task whose decide rights govern a grant: its own task, else the root."""
    for tid in (g.task_id, g.root_id):
        t = await s.get(Task, tid) if tid else None
        if t is not None:
            return t
    return None


@api.get("/grants", response_model=list[GrantOut])
async def list_grants(request: Request, root_id: Optional[str] = None, include_inactive: bool = False):
    """A thread's grants (any step id names its thread) for people who can
    see that step (else 404); without ``root_id``, the grants of every thread
    whose granting task the viewer can see. Live ones only unless
    ``include_inactive``."""
    p = request.state.principal
    _person(p)
    now = _now()
    async with SessionLocal() as s:
        stmt = select(ActionGrant)
        if root_id is not None:
            task = await require_task(s, p, root_id)  # 404 when invisible
            stmt = stmt.where(ActionGrant.root_id == (task.root_id or task.id))
        else:
            # visibility in SQL, before the LIMIT: the grant's task, else (gone) its thread root
            visible = select(Task.id).where(await task_visibility_clause(s, p))
            stmt = stmt.where(or_(
                ActionGrant.task_id.in_(visible),
                and_(or_(ActionGrant.task_id.is_(None), ActionGrant.task_id.not_in(select(Task.id))),
                     ActionGrant.root_id.in_(visible)),
            ))
        if not include_inactive:
            stmt = stmt.where(ActionGrant.revoked_at.is_(None), ActionGrant.expires_at > now,
                              or_(ActionGrant.scope == "run", ActionGrant.used_at.is_(None)))
        rows = (await s.execute(stmt.order_by(ActionGrant.created_at.desc()).limit(LIST_LIMIT))).scalars().all()
        ids = {i for g in rows for i in (g.task_id, g.root_id) if i}
        tasks = {t.id: t for t in (await s.execute(select(Task).where(Task.id.in_(ids)))).scalars()} if ids else {}
        out, cache = [], {}
        for g in rows:
            t = tasks.get(g.task_id) or tasks.get(g.root_id)
            if t is None:
                continue
            if t.id not in cache:
                seen = await can_see_task(s, p, t)
                cache[t.id] = (seen, seen and await _may_decide(s, p, t))
            seen, can = cache[t.id]
            if root_id is None and not seen:
                continue
            out.append(_grant_out(g, can, now))
    return out


@api.post("/grants/{grant_id}/revoke", response_model=GrantOut)
async def revoke_grant(grant_id: str, request: Request):
    """Stops server answers and later tasks at once (a running step keeps its
    policy-file copy until it ends). The same people who may decide the
    grant's requests (Ruling R25)."""
    p = request.state.principal
    _person(p)
    async with SessionLocal() as s:
        g = await s.get(ActionGrant, grant_id)
        t = await _grant_task(s, g) if g is not None else None
        if g is None or t is None or not await can_see_task(s, p, t):
            raise HTTPException(404, "Not found")
        if not await _may_decide(s, p, t):
            raise HTTPException(403, "Not allowed to revoke this grant")
        now = _now()
        res = await s.execute(update(ActionGrant).where(ActionGrant.id == g.id, ActionGrant.revoked_at.is_(None))
                              .values(revoked_at=now, revoked_by=p.user.id))
        if res.rowcount != 1:
            await s.rollback()
            raise HTTPException(409, "already revoked")
        await s.commit()
        await s.refresh(g)
        return _grant_out(g, False, now)


# ---- policy settings (people) ----------------------------------------------------------------

RULES_PER_SCOPE = guard_custom.RULES_PER_SCOPE
RULE_SCOPES = ("org", "project")
DEV_PATTERNS_MAX = 200
# Control characters (incl. NUL, tab and newline) are refused, not cleaned: a
# pattern or reason is one line of text (a regex can say \s or \n).
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f]")
# The cap is counted and the row added under this lock (one server process)
# and, on Postgres, a transaction advisory lock per scope (fix round 1, M8).
_RULE_WRITE_LOCK = asyncio.Lock()


def _no_control(field: str, value: str | None) -> None:
    if value is not None and _CONTROL.search(value):
        raise HTTPException(400, f"the {field} contains a control character (tab, newline ...); "
                                 "patterns and reasons are one line of text")


def _person(p) -> None:
    if p.is_agent:  # also refused by the allowlist
        raise HTTPException(403, "Token not allowed for this route")


class RuleIn(BaseModel):
    scope: str = "org"
    project_id: Optional[str] = None
    name: Optional[str] = Field(None, max_length=200)  # only shapes the slug
    kind: str = "confirm"
    match: str = "text"
    pattern: str = Field(max_length=4000)  # the real limit (500) is checked with a clear message
    reason: str = Field("", max_length=4000)
    enabled: bool = True


class RulePatch(BaseModel):
    kind: Optional[str] = None
    match: Optional[str] = None
    pattern: Optional[str] = Field(None, max_length=4000)
    reason: Optional[str] = Field(None, max_length=4000)
    enabled: Optional[bool] = None


class RuleOut(BaseModel):
    id: str
    rule_id: str  # the policy id: org.<slug> | project.<slug>
    scope: str
    project_id: Optional[str] = None
    slug: str
    kind: str
    match: str
    pattern: str
    reason: str = ""
    enabled: bool
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime


def _rule_out(r: ActionRule) -> RuleOut:
    return RuleOut(
        id=r.id, rule_id=guard_custom.rule_id(r.scope, r.slug), scope=r.scope, project_id=r.project_id,
        slug=r.slug, kind=r.kind, match=r.match, pattern=r.pattern, reason=r.reason or "", enabled=r.enabled,
        created_by=r.created_by, created_at=_aware(r.created_at), updated_at=_aware(r.updated_at),
    )


def _check_rule(kind: str, match: str, pattern: str) -> None:
    if kind not in guard_custom.KINDS:
        raise HTTPException(400, f"kind must be one of: {', '.join(guard_custom.KINDS)}")
    err = guard_custom.validate_pattern(match, pattern)
    if err is None:
        return
    bad_regex = "bad regular expression: "
    if match == "regex" and err.startswith(bad_regex):
        err = "invalid regex: " + err[len(bad_regex):]
    raise HTTPException(400, err)


def _same_scope(scope: str, project_id: str | None):
    if project_id is None:
        return and_(ActionRule.scope == scope, ActionRule.project_id.is_(None))
    return and_(ActionRule.scope == scope, ActionRule.project_id == project_id)


async def _may_write_rule(s, p, scope: str, project_id: str | None) -> None:
    """Org rules: admins (403). Project rules: maintainers/admins of a
    project the caller can see (invisible or missing -> 404, lower -> 403)."""
    if scope == "org":
        if not p.is_admin or not p.user.active:
            raise HTTPException(403, "Admin only")
        return
    await require_project_role(s, p, project_id, "maintainer")
    if await s.get(Project, project_id) is None:
        raise HTTPException(404, "Not found")


@api.get("/rules", response_model=list[RuleOut])
async def list_rules(request: Request, project_id: Optional[str] = None):
    p = request.state.principal
    _person(p)
    async with SessionLocal() as s:
        if project_id is not None:
            await require_project_role(s, p, project_id)
            if await s.get(Project, project_id) is None:
                raise HTTPException(404, "Not found")
            where = _same_scope("project", project_id)
        else:
            if not p.user.active:
                raise HTTPException(404, "Not found")
            where = _same_scope("org", None)
        rows = (await s.execute(select(ActionRule).where(where).order_by(ActionRule.created_at, ActionRule.slug))
                ).scalars().all()
    return [_rule_out(r) for r in rows]


@api.post("/rules", response_model=RuleOut, status_code=201)
async def create_rule(body: RuleIn, request: Request):
    p = request.state.principal
    _person(p)
    if body.scope not in RULE_SCOPES:
        raise HTTPException(400, f"scope must be one of: {', '.join(RULE_SCOPES)}")
    if body.scope == "org" and body.project_id is not None:
        raise HTTPException(400, "an org rule has no project_id")
    if body.scope == "project" and not body.project_id:
        raise HTTPException(400, "a project rule needs a project_id")
    project_id = body.project_id if body.scope == "project" else None
    async with SessionLocal() as s:
        await _may_write_rule(s, p, body.scope, project_id)
        for field, value in (("pattern", body.pattern), ("reason", body.reason), ("name", body.name)):
            _no_control(field, value)
        _check_rule(body.kind, body.match, body.pattern)
        await s.rollback()  # the counted read below starts a fresh transaction, under the locks
        async with _RULE_WRITE_LOCK:
            return await _insert_rule(s, p, body, project_id)


async def _insert_rule(s, p, body: RuleIn, project_id: str | None) -> RuleOut:
    if s.get_bind().dialect.name == "postgresql":
        await s.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"co-rules:{body.scope}:{project_id}"))))
    reason = guard_custom.clean_reason(body.reason)
    same = _same_scope(body.scope, project_id)
    count = (await s.execute(select(func.count(ActionRule.id)).where(same))).scalar_one()
    if count >= RULES_PER_SCOPE:
        raise HTTPException(409, f"at most {RULES_PER_SCOPE} rules per scope")
    taken = set((await s.execute(select(ActionRule.slug).where(same))).scalars())
    name = guard_custom.clean_reason(body.name) if body.name else ""
    now = _now()
    rule = ActionRule(
        scope=body.scope, project_id=project_id,
        slug=guard_custom.unique_slug(name or reason or body.pattern, taken),
        kind=body.kind, match=body.match, pattern=body.pattern, reason=reason, enabled=body.enabled,
        created_by=p.user.id, created_at=now, updated_at=now,
    )
    s.add(rule)
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        raise HTTPException(409, "a rule with this name was just added; try again")
    await s.refresh(rule)
    return _rule_out(rule)


async def _rule_for_write(s, p, rule_id: str) -> ActionRule:
    rule = await s.get(ActionRule, rule_id)
    if rule is None:
        raise HTTPException(404, "Not found")
    await _may_write_rule(s, p, rule.scope, rule.project_id)
    return rule


@api.patch("/rules/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: str, body: RulePatch, request: Request):
    p = request.state.principal
    _person(p)
    async with SessionLocal() as s:
        rule = await _rule_for_write(s, p, rule_id)
        data = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
        for field in ("pattern", "reason"):
            _no_control(field, data.get(field))
        kind, match, pattern = data.get("kind", rule.kind), data.get("match", rule.match), data.get(
            "pattern", rule.pattern)
        _check_rule(kind, match, pattern)
        rule.kind, rule.match, rule.pattern = kind, match, pattern
        if "reason" in data:
            rule.reason = guard_custom.clean_reason(data["reason"])
        if "enabled" in data:
            rule.enabled = data["enabled"]
        rule.updated_at = _now()
        await s.commit()
        await s.refresh(rule)
        return _rule_out(rule)


@api.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: str, request: Request):
    p = request.state.principal
    _person(p)
    async with SessionLocal() as s:
        rule = await _rule_for_write(s, p, rule_id)
        await s.delete(rule)
        await s.commit()


@api.get("/builtins")
async def list_builtins(request: Request):
    _person(request.state.principal)
    return [
        {"id": rid, "reason": reason, "hard_reason": hard, "disableable": rid in guard_rules.DISABLEABLE}
        for rid, (reason, hard) in guard_rules.RULES.items()
    ]


class OrgSettingsIn(BaseModel):
    disabled_rules: Optional[list[str]] = None
    dev_db_patterns: Optional[list[str]] = None
    ground_rules: Optional[Any] = None  # replaces the whole object; not an object: 400 (validate) (a key left out takes its default)
    model_tiers: Optional[Any] = None  # replaces the whole map; a key left out takes its default (400 on a bad one)


async def _org_settings_out(s) -> dict:
    row = await s.get(GuardOrgSettings, 1)
    out = await policy_store.org_settings(s)
    out["updated_by"] = row.updated_by if row else None
    out["updated_at"] = _aware(row.updated_at) if row and row.updated_at else None
    return out


@api.get("/org-settings")
async def get_org_settings(request: Request):
    _person(request.state.principal)
    async with SessionLocal() as s:
        return await _org_settings_out(s)


@api.put("/org-settings")
async def put_org_settings(body: OrgSettingsIn, request: Request):
    p = request.state.principal
    _person(p)
    if not p.is_admin or not p.user.active:
        raise HTTPException(403, "Admin only")
    disabled = patterns = ground = tier_map = None
    if body.model_tiers is not None:
        try:
            tier_map = tiers.validate(body.model_tiers)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    if body.ground_rules is not None:
        try:
            ground = ground_rules.validate(body.ground_rules)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    if body.disabled_rules is not None:
        disabled = list(dict.fromkeys(body.disabled_rules))
        locked = [r for r in disabled if r not in guard_rules.DISABLEABLE]
        if locked:
            raise HTTPException(400, f"cannot switch off: {', '.join(locked)} (only "
                                     f"{', '.join(sorted(guard_rules.DISABLEABLE))} can be)")
    if body.dev_db_patterns is not None:
        if len(body.dev_db_patterns) > DEV_PATTERNS_MAX:
            raise HTTPException(400, f"at most {DEV_PATTERNS_MAX} dev database patterns")
        for raw in body.dev_db_patterns:
            err = guard_dbrules.check_dev_pattern(raw)
            if err:
                raise HTTPException(400, err)
        patterns = list(dict.fromkeys(x.strip() for x in body.dev_db_patterns))
    for attempt in range(2):
        async with SessionLocal() as s:
            row = await s.get(GuardOrgSettings, 1)
            if row is None:
                row = GuardOrgSettings(id=1, disabled_rules=[], dev_db_patterns=[])
                s.add(row)
            if disabled is not None:
                row.disabled_rules = disabled
            if patterns is not None:
                row.dev_db_patterns = patterns
            if ground is not None:
                row.ground_rules = ground
            if tier_map is not None:
                row.model_tiers = tier_map
            row.updated_by = p.user.id
            row.updated_at = _now()
            try:
                await s.commit()
            except IntegrityError:  # another first write won the race: update that row
                await s.rollback()
                if attempt:
                    raise HTTPException(409, "the settings changed; try again")
                continue
            return await _org_settings_out(s)
