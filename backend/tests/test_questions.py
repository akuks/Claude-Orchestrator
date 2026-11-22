"""Phase 3b task 5: the Questions inbox - filing (agent token, own task only; a manager), limits and
redaction, the list with ``can_answer``, answer (a follow-up step with ANSWER_PROMPT) and dismiss, the
webhook, and the ground-rules paragraph."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth, notifications
from app.constants import Status
from app.database import SessionLocal
from app.main import app
from app.models import Schedule, Task, TaskQuestion
from app.resume import ground_rules as gr
from app.resume.texts import ACCEPT_ANSWER, ANSWER_PROMPT
from conftest import login_as


def _run(coro):
    return asyncio.run(coro)


def _task(created_by, status="completed", root_id=None, session_id="sess-q", **kw):
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title=f"q-{tid[:6]}", prompt="p", root_id=root_id or tid, status=status,
                       created_by=created_by, session_id=session_id, **kw))
            await s.commit()

    _run(go())
    return tid


def _agent(user_id, task_id):
    async def go():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, user_id, "agent-task", task_id, 3600)
            await s.commit()
            return raw

    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {_run(go())}"
    return c


def _get(model, key):
    async def go():
        async with SessionLocal() as s:
            return await s.get(model, key)

    return _run(go())


def _set_status(tid, status):
    async def go():
        async with SessionLocal() as s:
            (await s.get(Task, tid)).status = status
            await s.commit()

    _run(go())


@pytest.fixture
def admin_id(client):
    return client.get("/auth/me").json()["id"]


@pytest.fixture
def submitted(monkeypatch):
    got = []

    async def submit(task_id, priority, created_at):
        got.append(task_id)

    monkeypatch.setattr(app.state.worker, "submit", submit)
    yield got
    for tid in got:  # never left queued for other tests
        _set_status(tid, Status.CANCELLED)


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(notifications, "post_in_background", lambda *a: out.append(a))
    return out


Q = {"question": "Which database should I use?", "options": ["Postgres", "SQLite"],
     "recommendation": "Postgres", "blocks": "the schema work", "reversible": True}


# ---- filing ------------------------------------------------------------------------------------


def test_agent_files_and_reads_own_task_questions(client, admin_id, sent):
    tid = _task(admin_id, status="running")
    agent = _agent(admin_id, tid)
    r = agent.post(f"/tasks/{tid}/questions", json=Q)
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["question"] == Q["question"] and q["options"] == Q["options"]
    assert q["recommendation"] == "Postgres" and q["blocks"] == "the schema work" and q["reversible"] is True
    assert q["source"] == "agent" and q["status"] == "open" and q["task_id"] == tid and q["root_id"] == tid
    r = agent.get(f"/tasks/{tid}/questions")
    assert r.status_code == 200 and [x["id"] for x in r.json()] == [q["id"]]
    # people read it too
    assert [x["id"] for x in client.get(f"/tasks/{tid}/questions").json()] == [q["id"]]


def test_agent_token_other_task_and_never_answers(client, admin_id, submitted):
    mine, other = _task(admin_id, status="running"), _task(admin_id, status="running")
    agent = _agent(admin_id, mine)
    assert agent.post(f"/tasks/{other}/questions", json=Q).status_code == 403
    assert agent.get(f"/tasks/{other}/questions").status_code == 403
    assert agent.post(f"/tasks/{uuid.uuid4().hex}/questions", json=Q).status_code == 403
    qid = agent.post(f"/tasks/{mine}/questions", json=Q).json()["id"]
    assert agent.post(f"/questions/{qid}/answer", json={"answer": "x"}).status_code == 403
    assert agent.post(f"/questions/{qid}/dismiss").status_code == 403
    assert agent.get("/questions").status_code == 403
    assert _get(TaskQuestion, qid).status == "open" and submitted == []


def test_sixth_open_question_is_409(client, admin_id):
    root = _task(admin_id, status="running")
    step = _task(admin_id, status="running", root_id=root, parent_task_id=root)
    for i in range(3):
        assert _agent(admin_id, root).post(f"/tasks/{root}/questions", json={"question": f"a{i}?"}).status_code == 201
    for i in range(2):
        assert _agent(admin_id, step).post(f"/tasks/{step}/questions", json={"question": f"b{i}?"}).status_code == 201
    r = _agent(admin_id, step).post(f"/tasks/{step}/questions", json={"question": "sixth?"})
    assert r.status_code == 409, r.text
    # a closed one frees a slot
    qid = client.get(f"/tasks/{root}/questions").json()[0]["id"]
    assert client.post(f"/questions/{qid}/dismiss").status_code == 200
    assert _agent(admin_id, step).post(f"/tasks/{step}/questions", json={"question": "sixth?"}).status_code == 201


@pytest.mark.parametrize("body", [
    {},
    {"question": ""},
    {"question": "   "},
    {"question": "x" * 601},
    {"question": "q?", "options": ["a", "b", "c", "d", "e"]},
    {"question": "q?", "options": ["x" * 201]},
    {"question": "q?", "options": [""]},
    {"question": "q?", "options": "a"},
    {"question": "q?", "recommendation": "x" * 201},
    {"question": "q?", "blocks": "x" * 301},
    {"question": 5},
    {"question": "q?", "reversible": "maybe"},
])
def test_limits_are_422(client, admin_id, body):
    tid = _task(admin_id, status="running")
    assert _agent(admin_id, tid).post(f"/tasks/{tid}/questions", json=body).status_code == 422, body


def test_limits_at_the_edge_pass(client, admin_id):
    tid = _task(admin_id, status="running")
    body = {"question": "x" * 599 + "?", "options": ["o" * 200] * 4, "recommendation": "r" * 200,
            "blocks": "b" * 300, "reversible": None}
    r = _agent(admin_id, tid).post(f"/tasks/{tid}/questions", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["reversible"] is None


def test_texts_redacted_and_cleaned(client, admin_id):
    tid = _task(admin_id, status="running")
    secret = "run-secret-value-123"
    app.state.worker._run_secrets[tid] = [secret]
    tok = "ghp_" + "a" * 36
    try:
        r = _agent(admin_id, tid).post(f"/tasks/{tid}/questions", json={
            "question": f"Use {secret} or {tok}? zero​width rtl‮word",
            "options": [f"keep {tok}", f"drop {secret}"],
            "recommendation": f"rotate {secret}", "blocks": f"deploy with {tok}"})
    finally:
        app.state.worker._run_secrets.pop(tid, None)
    assert r.status_code == 201, r.text
    row = _get(TaskQuestion, r.json()["id"])
    stored = [row.question, *row.options, row.recommendation, row.blocks]
    for text in stored:
        assert secret not in text and tok not in text and "​" not in text and "‮" not in text
    assert "***" in row.question and all("***" in o for o in row.options)
    assert "zerowidth" in row.question and "rtlword" in row.question


def test_human_manager_files_others_cannot(client, admin_id):
    tid = _task(admin_id)
    r = client.post(f"/tasks/{tid}/questions", json=Q)
    assert r.status_code == 201 and r.json()["source"] == "agent"
    stranger = login_as(f"q-{uuid.uuid4().hex[:8]}@test.local")
    assert stranger.post(f"/tasks/{tid}/questions", json=Q).status_code == 404
    assert stranger.get(f"/tasks/{tid}/questions").status_code == 404


# ---- listing -----------------------------------------------------------------------------------


def test_list_shows_visible_questions_with_can_answer(client, admin_id):
    member = login_as(f"q-{uuid.uuid4().hex[:8]}@test.local")
    theirs = _task(member.user_id)
    mine = _task(admin_id)
    q_theirs = member.post(f"/tasks/{theirs}/questions", json={"question": "theirs?"}).json()["id"]
    q_mine = client.post(f"/tasks/{mine}/questions", json={"question": "mine?"}).json()["id"]
    got = {q["id"]: q for q in member.get("/questions?status=open").json()}
    assert q_theirs in got and q_mine not in got  # only tasks the caller can see
    assert got[q_theirs]["can_answer"] is True
    admin_view = {q["id"]: q for q in client.get("/questions").json()}  # default: open
    assert admin_view[q_theirs]["can_answer"] is True and admin_view[q_mine]["can_answer"] is True
    assert client.post(f"/questions/{q_mine}/dismiss").status_code == 200
    assert q_mine not in {q["id"] for q in client.get("/questions?status=open").json()}
    dismissed = {q["id"]: q for q in client.get("/questions?status=dismissed").json()}
    assert dismissed[q_mine]["can_answer"] is False
    assert client.get("/questions?status=bogus").status_code == 422


def test_viewer_without_manage_cannot_answer(client, admin_id, submitted):
    # a project contributor who is neither the creator nor a maintainer sees it but may not answer
    proj = client.post("/projects", json={"name": f"qp-{uuid.uuid4().hex[:6]}"}).json()
    member = login_as(f"q-{uuid.uuid4().hex[:8]}@test.local")
    keep = [{k: m[k] for k in ("subject_type", "subject_id", "role")}
            for m in client.get(f"/projects/{proj['id']}/members").json()]
    keep.append({"subject_type": "user", "subject_id": member.user_id, "role": "contributor"})
    r = client.put(f"/projects/{proj['id']}/members", json=keep)
    assert r.status_code == 200, r.text
    tid = _task(admin_id, project_id=proj["id"])
    qid = client.post(f"/tasks/{tid}/questions", json=Q).json()["id"]
    got = {q["id"]: q for q in member.get("/questions").json()}
    assert got[qid]["can_answer"] is False
    assert member.post(f"/questions/{qid}/answer", json={"answer": "x"}).status_code == 403
    assert member.post(f"/questions/{qid}/dismiss").status_code == 403
    assert submitted == []


# ---- answer / dismiss --------------------------------------------------------------------------


def test_answer_queues_followup_with_answer_prompt(client, admin_id, submitted):
    tid = _task(admin_id, session_id="sess-owner")
    qid = client.post(f"/tasks/{tid}/questions", json=Q).json()["id"]
    r = client.post(f"/questions/{qid}/answer", json={"answer": "SQLite, it is a prototype"})
    assert r.status_code == 201, r.text
    out = r.json()
    step = out["task"]
    assert out["question"]["status"] == "answered" and out["question"]["followup_task_id"] == step["id"]
    assert out["question"]["answer"] == "SQLite, it is a prototype"
    assert step["prompt"] == ANSWER_PROMPT.format(question=Q["question"], answer="SQLite, it is a prototype")
    assert step["parent_task_id"] == tid and step["root_id"] == tid and step["origin"] == "user"
    assert submitted == [step["id"]]
    row = _get(Task, step["id"])
    assert row.resume_session_id == "sess-owner"  # the answerer owns the session
    q = _get(TaskQuestion, qid)
    assert q.answered_by == admin_id and q.answered_at is not None
    # not open any more
    assert client.post(f"/questions/{qid}/answer", json={"answer": "again"}).status_code == 409
    assert client.post(f"/questions/{qid}/dismiss").status_code == 409


def test_answer_continues_from_the_threads_newest_main_line_step(client, admin_id, submitted):
    from datetime import datetime, timedelta, timezone

    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    step1 = _task(admin_id, session_id="sess-1", created_at=t0)
    qid = client.post(f"/tasks/{step1}/questions", json=Q).json()["id"]
    step2 = _task(admin_id, root_id=step1, parent_task_id=step1, session_id="sess-2",
                  created_at=t0 + timedelta(minutes=1))
    # a newer dispatched child is not the main line
    _task(admin_id, root_id=step1, parent_task_id=step2, origin="agent", session_id="sess-child",
          created_at=t0 + timedelta(minutes=2))
    r = client.post(f"/questions/{qid}/answer", json={"answer": "go"})
    assert r.status_code == 201, r.text
    step = r.json()["task"]
    assert step["parent_task_id"] == step2 and step["root_id"] == step1
    assert _get(Task, step["id"]).resume_session_id == "sess-2"  # the answerer owns step 2's session


def test_answer_by_another_person_starts_fresh(client, submitted):
    member = login_as(f"q-{uuid.uuid4().hex[:8]}@test.local")
    tid = _task(member.user_id, session_id="sess-member")
    qid = member.post(f"/tasks/{tid}/questions", json=Q).json()["id"]
    r = client.post(f"/questions/{qid}/answer", json={"answer": "yes"})  # the admin, not the owner
    assert r.status_code == 201, r.text
    assert _get(Task, r.json()["task"]["id"]).resume_session_id is None


def test_accept_uses_accept_answer(client, admin_id, submitted):
    tid = _task(admin_id)
    qid = client.post(f"/tasks/{tid}/questions", json=Q).json()["id"]
    r = client.post(f"/questions/{qid}/answer", json={"accept": True})
    assert r.status_code == 201, r.text
    ans = ACCEPT_ANSWER.format(recommendation="Postgres")
    assert r.json()["task"]["prompt"] == ANSWER_PROMPT.format(question=Q["question"], answer=ans)
    assert r.json()["question"]["answer"] == ans


def test_accept_without_recommendation_and_bad_bodies(client, admin_id, submitted):
    tid = _task(admin_id)
    qid = client.post(f"/tasks/{tid}/questions", json={"question": "go?"}).json()["id"]
    assert client.post(f"/questions/{qid}/answer", json={"accept": True}).status_code == 409
    for bad in ({}, {"answer": ""}, {"answer": "  "}, {"accept": False}, {"answer": "x", "accept": True},
                {"answer": 5}):
        assert client.post(f"/questions/{qid}/answer", json=bad).status_code == 422, bad
    assert client.post(f"/questions/{uuid.uuid4().hex}/answer", json={"answer": "x"}).status_code == 404
    assert submitted == [] and _get(TaskQuestion, qid).status == "open"


def test_answer_409_while_thread_active(client, admin_id, submitted):
    root = _task(admin_id)
    qid = client.post(f"/tasks/{root}/questions", json=Q).json()["id"]
    live = _task(admin_id, status="running", root_id=root, parent_task_id=root)
    assert client.post(f"/questions/{qid}/answer", json={"answer": "x"}).status_code == 409
    assert _get(TaskQuestion, qid).status == "open" and submitted == []
    _set_status(live, Status.COMPLETED)
    assert client.post(f"/questions/{qid}/answer", json={"answer": "x"}).status_code == 201


def test_answer_hidden_from_stranger_is_404(client, admin_id):
    tid = _task(admin_id)
    qid = client.post(f"/tasks/{tid}/questions", json=Q).json()["id"]
    stranger = login_as(f"q-{uuid.uuid4().hex[:8]}@test.local")
    assert stranger.post(f"/questions/{qid}/answer", json={"answer": "x"}).status_code == 404
    assert stranger.post(f"/questions/{qid}/dismiss").status_code == 404


def test_dismiss_closes_without_a_step(client, admin_id, submitted):
    tid = _task(admin_id)
    qid = client.post(f"/tasks/{tid}/questions", json=Q).json()["id"]
    r = client.post(f"/questions/{qid}/dismiss")
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    assert submitted == [] and _get(TaskQuestion, qid).followup_task_id is None

    async def steps():
        async with SessionLocal() as s:
            return (await s.execute(select(Task.id).where(Task.root_id == tid))).scalars().all()

    assert _run(steps()) == [tid]


# ---- webhook -----------------------------------------------------------------------------------


def test_filing_sends_the_schedule_webhook(client, admin_id, sent):
    sid = uuid.uuid4().hex

    async def mk():
        async with SessionLocal() as s:
            s.add(Schedule(id=sid, name="nightly", cron="0 0 * * *", prompt="p", notify="on_failure",
                           notify_webhook="https://hooks.example.invalid/x", created_by=admin_id))
            await s.commit()

    _run(mk())
    tid = _task(admin_id, status="running", schedule_id=sid)
    r = _agent(admin_id, tid).post(f"/tasks/{tid}/questions", json=Q)
    assert r.status_code == 201
    assert len(sent) == 1
    url, text = sent[0]
    assert url == "https://hooks.example.invalid/x"
    assert "question" in text.lower() and Q["question"] in text
    # a task without a configured channel sends nothing
    other = _task(admin_id, status="running")
    _agent(admin_id, other).post(f"/tasks/{other}/questions", json=Q)
    assert len(sent) == 1


def test_notify_question_never_raises_for_a_missing_row(client):
    _run(notifications.notify_question(uuid.uuid4().hex))


# ---- ground rules ------------------------------------------------------------------------------


def test_questions_rule_in_main_text_only_when_on():
    assert "questions" in gr.SWITCHES and gr.DEFAULTS["questions"] is True
    assert gr.QUESTIONS_RULE in gr.main_text(gr.DEFAULTS)
    assert gr.QUESTIONS_RULE not in gr.main_text(gr.DEFAULTS | {"questions": False})
    for external in (True, False):
        assert gr.QUESTIONS_RULE not in gr.helper_text(gr.DEFAULTS, external)
    assert "questions" in gr.included(gr.DEFAULTS)
    assert "/tasks/$CO_TASK_ID/questions" in gr.QUESTIONS_RULE
