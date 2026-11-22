"""Questions inbox (phase 3b, forge F22 + F99): filing, the shared limits and cleaning, and the capture of a
question from a step's final text.

Every text is redacted (the run's secrets plus token shapes), cleaned of hidden characters and made one line
before it is stored, and its length is checked after that (422). At most ``OPEN_MAX`` questions are open per thread (409);
the count and the insert run under the global follow-up lock, so two filings cannot both take the last slot."""

from __future__ import annotations

import logging
import re

from fastapi import HTTPException
from sqlalchemy import func, select

from .. import notifications
from ..access import effective_project_id
from ..constants import Status
from ..database import SessionLocal
from ..models import Task, TaskEvent, TaskHandoff, TaskQuestion
from ..redaction import clean_text, redact
from ..resume import handoff as resume_handoff
from ..schemas import QuestionCreate, QuestionOut

log = logging.getLogger(__name__)

QUESTION_MAX = 600
OPTIONS_MAX = 4
OPTION_MAX = 200
RECOMMENDATION_MAX = 200
BLOCKS_MAX = 300
ANSWER_MAX = 2000
OPEN_MAX = 5  # open questions per thread
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


def clean(text, secrets=None) -> str:
    """Hidden characters stripped, the secrets and token shapes masked, outer space trimmed (line breaks
    kept: a person's free answer)."""
    return redact(clean_text(text), list(secrets or [])).strip()


def one_line(text, secrets=None) -> str:
    """``clean`` and every run of white space (line breaks too) made one space: how a question's texts are
    stored, so a later prompt that quotes them cannot gain a line (e.g. a fake "The person's answer:")."""
    return " ".join(clean(text, secrets).split())


def _limited(text, secrets, limit: int, what: str) -> str | None:
    if text is None:
        return None
    out = one_line(text, secrets)
    if len(out) > limit:
        raise HTTPException(422, f"{what} is at most {limit} characters")
    return out or None


def clean_fields(body: QuestionCreate, secrets=None) -> dict:
    """A filing's fields as stored (422 for an empty question, too many options or a text over its limit)."""
    question = _limited(body.question, secrets, QUESTION_MAX, "question")
    if not question:
        raise HTTPException(422, "question is empty")
    if len(body.options) > OPTIONS_MAX:
        raise HTTPException(422, f"at most {OPTIONS_MAX} options")
    options = []
    for o in body.options:
        o = _limited(o, secrets, OPTION_MAX, "an option")
        if not o:
            raise HTTPException(422, "an option is empty")
        options.append(o)
    return {
        "question": question,
        "options": options,
        "recommendation": _limited(body.recommendation, secrets, RECOMMENDATION_MAX, "recommendation"),
        "blocks": _limited(body.blocks, secrets, BLOCKS_MAX, "blocks"),
        "reversible": body.reversible,
    }


def out(q: TaskQuestion, *, task_title: str | None = None, can_answer: bool = False) -> QuestionOut:
    o = QuestionOut.model_validate(q)
    o.options = list(q.options or [])
    o.task_title = task_title
    o.can_answer = bool(can_answer and q.status == "open")
    return o


async def file(s, task: Task, fields: dict, *, source: str) -> TaskQuestion:
    """Store a question on ``task`` (committed). 409 when its thread already has ``OPEN_MAX`` open."""
    from ..guard import grants as guard_grants

    root = task.root_id or task.id
    pid = await effective_project_id(s, task)
    async with guard_grants.followup_lock():
        n = (await s.execute(select(func.count()).select_from(TaskQuestion).where(
            TaskQuestion.root_id == root, TaskQuestion.status == "open"))).scalar_one()
        if n >= OPEN_MAX:
            raise HTTPException(409, f"This thread already has {OPEN_MAX} open questions")
        q = TaskQuestion(task_id=task.id, root_id=root, project_id=pid, source=source, status="open", **fields)
        s.add(q)
        await s.commit()
        await s.refresh(q)
    return q


async def notify(question_id: str) -> None:
    """The configured channel's webhook (never raises, never delays the caller)."""
    try:
        await notifications.notify_question(question_id)
    except Exception:  # noqa: BLE001
        log.warning("questions: notify failed for %s", question_id, exc_info=True)


# ---- the final text (after a run ends) -----------------------------------------------------------


def last_paragraph(text) -> str | None:
    """The text after the last blank line, stripped; None for an empty text."""
    if not isinstance(text, str):
        return None
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return None
    return _BLANK_LINE.split(text)[-1].strip() or None


async def _touched(s, task: Task) -> bool:
    """The step wrote a file (a Write/Edit/NotebookEdit tool call) or made a commit (git's summary line in a
    tool result, or the commit the thread's handoff recorded for this step)."""
    rows = await s.execute(select(TaskEvent.type, TaskEvent.payload).where(
        TaskEvent.task_id == task.id, TaskEvent.type.in_(("tool_use", "tool_result"))))
    for etype, payload in rows:
        p = payload if isinstance(payload, dict) else {}
        if etype == "tool_use" and p.get("name") in WRITE_TOOLS:
            return True
        if etype == "tool_result" and not p.get("is_error") and resume_handoff._COMMIT.search(
                str(p.get("content") or "")):
            return True
    row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == (task.root_id or task.id)))
           ).scalar_one_or_none()
    content = row.content if row is not None and isinstance(row.content, dict) else {}
    if content.get("step_task_id") == task.id:
        return any(str(d).startswith("commit ") for d in content.get("done") or [])
    return False


async def capture_final(task_id: str, ending: dict | None = None) -> None:
    """When a run ends (any terminal status with a final text): when the step touched files or made a commit
    and its last paragraph ends with ``?`` (at most ``QUESTION_MAX`` characters; a longer one is skipped, not
    cut), file that paragraph as a ``final_text`` question - unless a question was already filed on this step
    (by the agent, or an earlier capture) or the thread is full. Never raises.

    ``ending`` (the worker's ``_finish``, like the handoff snapshot's): the run is ending and its terminal
    status is not committed yet, so the question is in place when the status flips; its ``result_text``
    (redacted already) is the final text, else the stored one. Without it the stored status must be terminal."""
    try:
        async with SessionLocal() as s:
            task = await s.get(Task, task_id)
            if task is None or (ending is None and task.status not in Status.TERMINAL):
                return
            text = ending["result_text"] if ending is not None and "result_text" in ending else task.result_text
            para = last_paragraph(text)
            # the stored result_text is already redacted with the run's secrets (_finish); this masks
            # token shapes again and strips hidden characters
            para = one_line(para) if para else None
            if not para or not para.endswith("?") or len(para) > QUESTION_MAX:
                return
            filed = (await s.execute(select(TaskQuestion.id).where(TaskQuestion.task_id == task_id).limit(1))
                     ).first()
            if filed is not None or not await _touched(s, task):
                return
            try:
                q = await file(s, task, {"question": para, "options": []}, source="final_text")
            except HTTPException:
                return  # the thread already has OPEN_MAX open questions
            qid = q.id
        await notify(qid)
    except Exception:  # noqa: BLE001 - never breaks a finish
        log.warning("questions: capture failed for %s", task_id, exc_info=True)
