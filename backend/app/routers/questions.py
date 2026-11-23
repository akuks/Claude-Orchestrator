"""The Questions inbox for people (phase 3b): list, answer, dismiss. Never on the agent token's allowlist.

Answer and dismiss need the same permission as Continue (``_manage_allowed(need_contributor=True)`` on the
question's task); a question on a task the caller cannot see is 404. An answer continues the thread: a
follow-up step of the thread's newest main-line step (not a dispatched child), built by the follow-up path
(``_step_from`` under the global follow-up lock). It resumes that step's session when the answerer owns it,
else starts fresh with the thread's handoff."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.exc import InvalidRequestError

from ..access import require_task, task_visibility_clause
from ..constants import Status
from ..database import SessionLocal
from ..decisions import proposals, validate as decision_validate
from ..models import Task, TaskQuestion
from ..questions import service as questions
from ..resume.texts import ACCEPT_ANSWER, ANSWER_PROMPT
from ..schemas import QuestionAnswer, QuestionOut, TaskOut
from .tasks import _manage_allowed, _require_manage, _step_from

log = logging.getLogger(__name__)

router = APIRouter(prefix="/questions", tags=["questions"])

STATUSES = ("open", "answered", "dismissed", "all")
LIST_MAX = 200


@router.get("", response_model=list[QuestionOut])
async def list_questions(request: Request, status: str = Query("open")):
    """Questions on the tasks the caller can see, newest first, each with ``can_answer``."""
    if status not in STATUSES:
        raise HTTPException(422, "status must be open, answered, dismissed or all")
    p = request.state.principal
    async with SessionLocal() as s:
        q = (select(TaskQuestion, Task).join(Task, Task.id == TaskQuestion.task_id)
             .where(await task_visibility_clause(s, p)))
        if status != "all":
            q = q.where(TaskQuestion.status == status)
        rows = (await s.execute(q.order_by(TaskQuestion.created_at.desc()).limit(LIST_MAX))).all()
        allowed: dict[str, bool] = {}
        out = []
        for row, task in rows:
            if row.status == "open" and task.id not in allowed:
                allowed[task.id] = (await _manage_allowed(s, p, task, need_contributor=True))[0]
            out.append(questions.out(row, task_title=task.title, can_answer=allowed.get(task.id, False)))
        return out


async def _open_question(s, p, question_id: str) -> tuple[TaskQuestion, Task]:
    """The question and its task: 404 when either is gone or the task is hidden, 403 without the permission,
    409 when the question is no longer open."""
    q = await s.get(TaskQuestion, question_id)
    if q is None:
        raise HTTPException(404, "Question not found")
    try:
        task = await require_task(s, p, q.task_id)
    except HTTPException:
        raise HTTPException(404, "Question not found") from None
    await _require_manage(s, p, task, need_contributor=True)
    if q.status != "open":
        raise HTTPException(409, f"The question is {q.status}")
    return q, task


@router.post("/{question_id}/answer", status_code=201)
async def answer_question(question_id: str, payload: QuestionAnswer, request: Request):
    """``{"answer": str}`` or ``{"accept": true}``: marks the question answered and queues a follow-up step
    whose prompt quotes the question and states the answer. Returns ``{question, task}``."""
    from ..guard import grants as guard_grants

    p = request.state.principal
    async with SessionLocal() as s:
        q, parent = await _open_question(s, p, question_id)  # 404/403 before the body says anything
        if (payload.answer is None) == (payload.accept is None) or payload.accept is False:
            raise HTTPException(422, "Give either an answer or accept: true")
        if payload.accept:
            if not q.recommendation:
                raise HTTPException(409, "The question has no recommendation to accept")
            # one line even for a row stored before the one-line rule
            answer = ACCEPT_ANSWER.format(recommendation=" ".join(q.recommendation.split()))
        else:
            answer = questions.clean(payload.answer)
            if not answer:
                raise HTTPException(422, "answer is empty")
            if len(answer) > questions.ANSWER_MAX:
                raise HTTPException(422, f"answer is at most {questions.ANSWER_MAX} characters")
        async with guard_grants.followup_lock():
            try:
                await s.refresh(q)
                await s.refresh(parent)
            except InvalidRequestError:  # deleted while waiting for the lock
                raise HTTPException(404, "Question not found") from None
            if q.status != "open":
                raise HTTPException(409, f"The question is {q.status}")
            root = parent.root_id or parent.id
            active = (await s.execute(select(Task.id).where(
                or_(Task.root_id == root, Task.id == root), Task.status.in_(Status.ACTIVE)).limit(1))).first()
            if active is not None:
                raise HTTPException(409, "A step of this thread is still active")
            # the answer continues the THREAD: from its newest main-line step (not a dispatched child)
            newest = (await s.execute(select(Task).where(
                or_(Task.root_id == root, Task.id == root), Task.origin != "agent")
                .order_by(Task.created_at.desc()).limit(1))).scalar_one_or_none()
            step_parent = newest or parent
            if step_parent.id != parent.id:  # the step actually continued needs the same permission
                ok, why = await _manage_allowed(s, p, step_parent, need_contributor=True)
                if not ok:
                    raise HTTPException(403, why)
            # the follow-up rule on that step: only its session's owner resumes it; anyone else starts fresh
            sender = p.user.id
            resume = (step_parent.session_id if step_parent.session_id and sender == step_parent.created_by
                      else None)
            # the question is quoted as data on one line (a line break cannot pose as the answer line)
            prompt = ANSWER_PROMPT.format(question=" ".join(q.question.split()), answer=answer)
            child = _step_from(step_parent, prompt=prompt, origin="user", resume=resume, created_by=sender)
            s.add(child)
            await s.flush()
            q.status = "answered"
            q.answer = answer
            q.answered_by = sender
            q.answered_at = datetime.now(timezone.utc)
            q.followup_task_id = child.id
            suggestion = None
            if payload.suggest_decision:
                suggestion = await _suggest(s, q, answer, sender)
            await s.commit()
            await s.refresh(child)
            await s.refresh(q)
        task_out = TaskOut.model_validate(child)
        q_out = questions.out(q, task_title=parent.title)
    await request.app.state.worker.submit(task_out.id, task_out.priority, task_out.created_at)
    return {"question": q_out, "task": task_out, "suggestion": suggestion}


async def _suggest(s, q, answer: str, sender: str) -> str:
    """The answer as a suggested decision; never raises (the answer must not fail because of it)."""
    if not q.project_id:
        return "skipped: the task has no project"
    try:
        async with s.begin_nested():
            fields = proposals.clean_proposal(
                {"title": decision_validate.one_line(q.question)[:200], "why": ("Answer: " + answer)[:2000],
                 "kind": "decision"}, strict_touches=True)
            row = await proposals.add(s, project_id=q.project_id, fields=fields, source="answer",
                                      evidence=f"question {q.id}", dedupe_key=f"answer:{q.id}",
                                      proposed_by=sender, task_id=q.task_id, on_full="raise")
    except ValueError as e:
        return f"skipped: {e}"
    except proposals.QueueFull:
        return "skipped: the suggestion queue is full"
    except Exception as e:  # noqa: BLE001
        log.warning("suggesting the answer to question %s failed: %r", q.id, e)
        return "skipped: could not add the suggestion"
    return "skipped: already suggested" if row is None else "added"


@router.post("/{question_id}/dismiss", response_model=QuestionOut)
async def dismiss_question(question_id: str, request: Request):
    """Close the question without a step."""
    from ..guard import grants as guard_grants

    p = request.state.principal
    async with SessionLocal() as s:
        q, task = await _open_question(s, p, question_id)
        async with guard_grants.followup_lock():  # serialised with an answer of the same question
            await s.refresh(q)
            if q.status != "open":
                raise HTTPException(409, f"The question is {q.status}")
            q.status = "dismissed"
            q.answered_by = p.user.id
            q.answered_at = datetime.now(timezone.utc)
            await s.commit()
            await s.refresh(q)
        return questions.out(q, task_title=task.title)
