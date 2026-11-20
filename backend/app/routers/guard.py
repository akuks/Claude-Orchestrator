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

Expiry: ``expires_at = created_at + min(approval_wait_seconds, wait_left)`` -
the wait in the task's own policy file (what its hook waits), else the
project's, clamped 60-900; ``wait_left`` is what the hook has left. A
pending request past ``expires_at``, or whose task is no longer
running, reads as ``expired`` (lazily, persisted when read). Decisions close
``DECIDE_MARGIN`` seconds before ``expires_at``: the hook polls every 2 s and
stops at its own deadline, so a later approval could land on a call that was
already denied.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from .. import notifications
from ..access import (
    ALL,
    can_see_task,
    effective_project_id,
    maintained_project_ids,
    project_role,
    require_task,
)
from ..constants import Status
from ..database import SessionLocal
from ..guard import runtime as guard_runtime
from ..guard.co_hook import SUMMARY_MAX as HOOK_SUMMARY_MAX
from ..models import ActionApproval, Project, Task
from ..redaction import redact

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
CONTENT_FIELDS = ("rule_ids", "tool", "summary", "cwd", "reason", "command_hash")


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


def _effective(a: ActionApproval, task_status: str | None, now: datetime | None = None) -> str:
    if a.status == "pending" and (
        (now or _now()) >= _aware(a.expires_at) or task_status != Status.RUNNING
    ):
        return "expired"
    return a.status


async def _settle(s, a: ActionApproval, task: Task | None) -> str:
    """Persist a lazy expiry; returns the effective status."""
    st = _effective(a, task.status if task else None)
    if st == "expired" and a.status == "pending":
        await s.execute(
            update(ActionApproval)
            .where(ActionApproval.id == a.id, ActionApproval.status == "pending")
            .values(status="expired")
        )
        await s.commit()
        a.status = "expired"
    return st


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


def _card(a: ActionApproval, task_title: str, status: str, can_decide: bool) -> ActionApprovalOut:
    return ActionApprovalOut(
        id=a.id, task_id=a.task_id, task_title=task_title or "", project_id=a.project_id,
        requested_by=a.requested_by, rule_ids=list(a.rule_ids or []), tool=a.tool or "",
        summary=a.summary or "", cwd=a.cwd or "", reason=a.reason or "", command_hash=a.command_hash or "",
        status=status, created_at=_aware(a.created_at), expires_at=_aware(a.expires_at),
        decided_by=a.decided_by, decided_at=a.decided_at and _aware(a.decided_at),
        decision_reason=a.decision_reason, can_decide=can_decide and status == "pending",
    )


# ---- filing (agent) ----------------------------------------------------------------------


class FileBody(BaseModel):
    task_id: str
    rule_ids: list[str] = []
    tool: str = ""
    summary: str = ""
    cwd: str = ""
    reason: str = ""
    command_hash: str = ""
    # seconds left before the hook's own deadline (not part of the content)
    wait_left: Optional[float] = None


def _content(body: FileBody, secrets: list[str]) -> dict:
    """The request's stored form: redacted first, then bounded."""
    return {
        "rule_ids": [redact(str(r), secrets)[:64] for r in body.rule_ids][:20],
        "tool": _cut(redact(body.tool, secrets), 200),
        "summary": _cut(redact(body.summary, secrets), SUMMARY_LIMIT),
        "cwd": _cut(redact(body.cwd, secrets), 1000),
        "reason": _cut(redact(body.reason, secrets), 2000),
        "command_hash": body.command_hash[:128],
    }


def _same_content(a: ActionApproval, content: dict) -> bool:
    return all(
        (list(a.rule_ids or []) if k == "rule_ids" else getattr(a, k)) == content[k] for k in CONTENT_FIELDS
    )


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
    return {"id": a.id, "status": "pending", "expires_at": _aware(a.expires_at)}


def _replay(a: ActionApproval, content: dict, response: Response) -> dict:
    """The pending request filed under this key - only if it is the same
    request. The agent shares the hook's token and can predict its key: a
    planted row with other content must never stand in for the hook's call."""
    if not _same_content(a, content):
        raise HTTPException(409, "conflicting request")
    response.status_code = 200
    return _filed(a)


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
        counts = (
            await s.execute(
                select(
                    func.count(ActionApproval.id),
                    func.count(ActionApproval.id).filter(
                        ActionApproval.status == "pending", ActionApproval.expires_at > now
                    ),
                ).where(ActionApproval.task_id == task.id)
            )
        ).one()
        if counts[0] >= MAX_TOTAL_PER_TASK or counts[1] >= MAX_PENDING_PER_TASK:
            raise HTTPException(429, "Too many approval requests for this task")
        project_id = await effective_project_id(s, task)
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
        lazily_expired = or_(ActionApproval.expires_at <= now, Task.status != Status.RUNNING)
        if status == "pending":
            stmt = stmt.where(ActionApproval.status == "pending", ~lazily_expired)
        elif status == "expired":
            stmt = stmt.where(
                or_(
                    ActionApproval.status == "expired",
                    and_(ActionApproval.status == "pending", lazily_expired),
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
    return [_card(a, t.title, _effective(a, t.status, now), decidable) for a, t in rows]


@api.get("/approvals/{approval_id}")
async def get_request(approval_id: str, request: Request):
    p = request.state.principal
    async with SessionLocal() as s:
        a = await s.get(ActionApproval, approval_id)
        task = await s.get(Task, a.task_id) if a is not None else None
        if p.is_agent:
            if a is None or a.task_id != p.token.task_id:
                raise HTTPException(404, "Not found")
            # command_hash: the hook checks the id stands for ITS call
            return {"id": a.id, "status": await _settle(s, a, task), "command_hash": a.command_hash}
        if a is None or task is None or not await can_see_task(s, p, task):
            raise HTTPException(404, "Not found")
        st = await _settle(s, a, task)
        return _card(a, task.title, st, await _may_decide(s, p, task))


# ---- deciding (people) --------------------------------------------------------------------


class RejectBody(BaseModel):
    reason: Optional[str] = None


async def _decide(request: Request, approval_id: str, status: str, reason: str | None):
    p = request.state.principal
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
        if now >= _aware(a.expires_at) - timedelta(seconds=DECIDE_MARGIN):
            raise HTTPException(409, "expired")
        res = await s.execute(
            update(ActionApproval)
            .where(ActionApproval.id == a.id, ActionApproval.status == "pending")
            .values(status=status, decided_by=p.user.id, decided_at=now,
                    decision_reason=(reason or None) and reason[:2000])
        )
        if res.rowcount != 1:
            await s.rollback()
            raise HTTPException(409, "already decided")
        await s.commit()
        await s.refresh(a)
        return _card(a, task.title, a.status, False)


@api.post("/approvals/{approval_id}/approve", response_model=ActionApprovalOut)
async def approve(approval_id: str, request: Request):
    return await _decide(request, approval_id, "approved", None)


@api.post("/approvals/{approval_id}/reject", response_model=ActionApprovalOut)
async def reject(approval_id: str, request: Request, body: Optional[RejectBody] = None):
    return await _decide(request, approval_id, "rejected", (body.reason if body else None) or "Rejected")


# ---- live report (agent) -------------------------------------------------------------------


@api.post("/report", status_code=202, dependencies=[Depends(_limit_body)])
async def report(request: Request, rec: dict = Body(...)):
    """The hook's decision record (best effort, after it answered Claude Code)."""
    p = request.state.principal
    task_id = rec.get("task_id")
    _require_agent_for(p, task_id if isinstance(task_id, str) else None)
    rid = rec.get("request_id")
    if rec.get("decision") == "deny" and isinstance(rid, str) and rid:
        # The hook gave up on (or was refused) this request: it can no longer
        # be approved for a call that was already denied.
        async with SessionLocal() as s:
            await s.execute(
                update(ActionApproval)
                .where(ActionApproval.id == rid, ActionApproval.task_id == task_id,
                       ActionApproval.status == "pending")
                .values(status="expired")
            )
            await s.commit()
    # The token is in the agent's env, so the agent can call this too: accept
    # only what the hook reports (PreToolUse deny / approved), and an
    # "approved" record only for a request of this task a person approved.
    if rec.get("event") != "PreToolUse" or rec.get("decision") not in ("deny", "approved"):
        return {"accepted": False}
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
    w = _worker(request)
    accepted = False
    if w is not None and hasattr(w, "report_guard"):
        accepted = await w.report_guard(task_id, rec)  # redacts with the run's secrets
    return {"accepted": accepted}
