"""Task output is redacted before it is stored or streamed.

An agent can print its env; the task owner's credentials, the minted
CO_API_TOKEN and secret-looking project env must never reach TaskEvent rows,
the broker / GET /tasks/{id}/events, task.result_text or task.error. The mock
claude echoes selected env vars into every output channel when the prompt
carries [env-echo] (conftest). Every credential here is an obviously fake
test string."""

import asyncio
import json
import os
import re
import secrets as pysecrets
import time
import uuid

from sqlalchemy import select

from app import redaction
from app.database import SessionLocal
from app.events import broker
from app.models import TaskEvent
from conftest import login_as, wait_task

CO_TOKEN_RE = re.compile(r"co_[A-Za-z0-9_-]{43}")


def _uid():
    return uuid.uuid4().hex[:8]


def _fake(kind: str) -> str:
    return f"fake-{kind}-{uuid.uuid4().hex}"


def _events(task_id):
    async def go():
        async with SessionLocal() as s:
            return (
                await s.execute(
                    select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.seq)
                )
            ).scalars().all()

    return asyncio.run(go())


def _project(admin, **extra):
    r = admin.post("/projects", json={"name": f"rd-{_uid()}", **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _grant(admin, pid, user_id, role):
    keep = [
        {k: m[k] for k in ("subject_type", "subject_id", "role")}
        for m in admin.get(f"/projects/{pid}/members").json()
        if m["subject_id"] != user_id
    ]
    keep.append({"subject_type": "user", "subject_id": user_id, "role": role})
    r = admin.put(f"/projects/{pid}/members", json=keep)
    assert r.status_code == 200, r.text


# ---- unit ---------------------------------------------------------------


def test_secrets_from_env_picks_sensitive_names_only():
    env = {
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth-value-123456",
        "ANTHROPIC_API_KEY": "apikey-value-123456",
        "GH_TOKEN": "gh-value-12345678",
        "GITHUB_TOKEN": "github-value-1234",
        "CO_API_TOKEN": "co-api-value-1234",
        "DB_PASSWORD": "db-pass-12345678",
        "my_client_secret": "client-secret-123",
        "AWS_SECRET_ACCESS_KEY": "aws-secret-1234567",
        "SSH_PRIVATE_KEY": "private-key-1234567",
        "DB_PASSWD": "passwd-123456789",
        "SHORT_TOKEN": "short1",  # < 8 chars: ignored
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "PROBE_PROJECT_VAR": "plain-visible-value",
        "EMPTY_TOKEN": "",
        "SERVICE_CREDENTIALS": "cred-value-1234",
        "BASIC_AUTH": "user:auth-value-12",
        "SENTRY_DSN": "dsn-value-12345678",
        "DATABASE_URL": "dburl-value-12345",
        "STRIPE_KEY": "stripe-value-1234",
        "GIT_AUTHOR_NAME": "Ada Lovelace Tester",  # AUTH inside a word: not secret
        "GIT_AUTHOR_EMAIL": "ada@example.test",
        "KEYBOARD_LAYOUT": "us-international",  # KEY not as a _KEY suffix
    }
    got = set(redaction.secrets_from_env(env))
    assert got == {
        "oauth-value-123456", "apikey-value-123456", "gh-value-12345678",
        "github-value-1234", "co-api-value-1234", "db-pass-12345678",
        "client-secret-123", "aws-secret-1234567", "private-key-1234567",
        "passwd-123456789", "cred-value-1234", "user:auth-value-12",
        "dsn-value-12345678", "dburl-value-12345", "stripe-value-1234",
    }


def test_redact_obj_recurses_into_string_leaves():
    sec = ["secret-value-abcdef", "secret-value-abcdef-longer"]
    payload = {
        "text": "a secret-value-abcdef-longer b secret-value-abcdef c",
        "input": {"command": ["echo", "secret-value-abcdef"], "n": 3, "ok": True},
        "none": None,
    }
    out = redaction.redact_obj(payload, sec)
    assert out == {
        "text": "a *** b *** c",
        "input": {"command": ["echo", "***"], "n": 3, "ok": True},
        "none": None,
    }
    assert payload["text"].startswith("a secret")  # input not mutated


def test_token_shapes_scrubbed_without_env():
    # Same format auth.mint_token produces ("co_" + token_urlsafe(32)).
    co = "co_" + pysecrets.token_urlsafe(32)
    shaped = {
        "sk-ant-oat01-" + "A1b2_C3-d4" * 4: "anthropic",
        "ghp_" + "a1B2c3D4e5" * 4: "github classic",
        "gho_" + "a1B2c3D4e5" * 4: "github oauth",
        "ghu_" + "a1B2c3D4e5" * 4: "github user-to-server",
        "ghs_" + "a1B2c3D4e5" * 4: "github server-to-server",
        "ghr_" + "a1B2c3D4e5" * 4: "github refresh",
        "github_pat_" + "11ABCDEFG_" * 5: "github fine-grained",
        co: "orchestrator api token",
    }
    for tok, what in shaped.items():
        out = redaction.redact(f"x={tok} y", [])
        assert out == "x=*** y", what
    assert CO_TOKEN_RE.fullmatch(co)
    # Not tokens: left alone.
    for plain in (
        "co_short", "co_" + "a" * 20, "sk-ant-short", "ghp_short", "ghx_" + "a" * 40,
        "PATH=/usr/bin:/bin", "https://github.com/org/repo", "see http://host:8080/x",
    ):
        assert redaction.redact(plain, []) == plain


def test_url_userinfo_masked():
    cases = {
        "DATABASE=postgres://app:s3cr3t-pw@db.local:5432/x":
            "DATABASE=postgres://***:***@db.local:5432/x",
        "remote https://x-access-token:abc123def@github.com/o/r.git ok":
            "remote https://***:***@github.com/o/r.git ok",
        "redis://:onlypass@cache:6379": "redis://:onlypass@cache:6379",  # no user: kept
    }
    for raw, want in cases.items():
        assert redaction.redact(raw, []) == want, raw


def test_finish_redacts_and_tolerates_non_str_result(client):
    t = client.post("/tasks", json={"prompt": "plain run"})
    assert t.status_code == 201, t.text
    tid = wait_task(client, t.json()["id"])["id"]
    worker = client.app.state.worker
    sec = _fake("oauth")

    asyncio.run(worker._finish(tid, "completed", result_text=f"tok {sec}", secrets=[sec]))
    assert client.get(f"/tasks/{tid}").json()["result_text"] == "tok ***"

    # A non-string "result" never crashes _finish and is still redacted.
    asyncio.run(
        worker._finish(tid, "completed", result_text={"text": f"tok {sec}"}, secrets=[sec])
    )
    rt = client.get(f"/tasks/{tid}").json()["result_text"]
    assert sec not in rt and "***" in rt


# ---- integration --------------------------------------------------------


def _blob_of(task_id, c):
    db = json.dumps([e.payload for e in _events(task_id)])
    api = c.get(f"/tasks/{task_id}/events")
    assert api.status_code == 200, api.text
    return db, json.dumps(api.json())


SHAPED = "sk-ant-api03-" + "Zz9_Yy8-Xx" * 4  # under a non-sensitive name


def _echo_task(client, prompt, published=None):
    pid = _project(
        client,
        env={
            "PROBE_PROJECT_VAR": "plain-visible-value",
            "PROBE_DB_PASSWORD": _fake("dbpass"),
            "PROBE_SHORT_TOKEN": "short1",
            "PROBE_SHAPED": SHAPED,
        },
    )
    m = login_as(f"rd-{_uid()}@test.local")
    _grant(client, pid, m.user_id, "contributor")
    tok, gh = _fake("oauth"), _fake("gh")
    r = m.put("/me/credentials", json={
        "claude_token": tok, "claude_kind": "oauth_token", "github_token": gh,
    })
    assert r.status_code == 200, r.text
    published = [] if published is None else published
    orig = broker.publish

    async def spy(task_id, event):
        published.append((task_id, json.loads(json.dumps(event, default=str))))
        await orig(task_id, event)

    broker.publish = spy
    try:
        t = m.post("/tasks", json={"prompt": prompt, "project_id": pid})
        assert t.status_code == 201, t.text
        done = wait_task(m, t.json()["id"])
        if "[env-echo-fail]" not in prompt:  # let the memory update land
            deadline = time.time() + 15
            while time.time() < deadline and _memory_of(done["id"])[0] is None:
                time.sleep(0.2)
            time.sleep(0.3)  # its broadcast follows the commit
    finally:
        broker.publish = orig

    async def real_env():
        from app.models import Project

        async with SessionLocal() as s:
            return dict((await s.get(Project, pid)).env)

    penv = asyncio.run(real_env())
    live = json.dumps([e for tid, e in published if tid == done["id"]])
    return m, done, live, [tok, gh, penv["PROBE_DB_PASSWORD"], SHAPED]


def _assert_clean(text, secrets, where):
    for sec in secrets:
        assert sec not in text, (where, sec)
    assert not CO_TOKEN_RE.search(text), where


def _memory_of(task_id):
    from app.models import Project, Task, TaskSummary

    async def go():
        async with SessionLocal() as s:
            row = (
                await s.execute(select(TaskSummary).where(TaskSummary.task_id == task_id))
            ).scalars().first()
            if row is None:
                return None, None
            t = await s.get(Task, task_id)
            return row.summary, (await s.get(Project, t.project_id)).memory

    return asyncio.run(go())


def test_echoed_env_is_redacted_everywhere(client):
    m, done, live, secrets = _echo_task(client, "print env [env-echo]", published := [])
    assert done["status"] == "completed", done.get("error")
    db, api = _blob_of(done["id"], m)
    for where, text in (("db", db), ("api", api), ("live", live)):
        _assert_clean(text, secrets, where)
        assert "CLAUDE_CODE_OAUTH_TOKEN=***" in text, where
        assert "GH_TOKEN=***" in text, where
        assert "CO_API_TOKEN=***" in text, where
        assert "PROBE_DB_PASSWORD=***" in text, where
        assert "PROBE_SHAPED=***" in text, where
        # Non-secrets untouched.
        assert "PROBE_PROJECT_VAR=plain-visible-value" in text, where
        assert "PROBE_SHORT_TOKEN=short1" in text, where  # < 8 chars: kept
        assert f"PATH={os.environ['PATH']}".replace("\\", "\\\\") in text, where
    # Every output channel went through: text, tool_use, tool_result, raw log.
    types = {e.type for e in _events(done["id"])}
    assert {"text_output", "tool_use", "tool_result", "log"} <= types

    rt = done["result_text"]
    assert rt and "CLAUDE_CODE_OAUTH_TOKEN=***" in rt and "CO_API_TOKEN=***" in rt
    _assert_clean(rt, secrets, "result_text")
    assert "PROBE_PROJECT_VAR=plain-visible-value" in rt

    # The post-task memory update (its claude call echoes the env too) stores
    # and broadcasts a redacted summary and memory.
    summary = memory_text = None
    deadline = time.time() + 15
    while time.time() < deadline and summary is None:
        summary, memory_text = _memory_of(done["id"])
        time.sleep(0.2)
    assert summary is not None, "memory update never stored a summary"
    _assert_clean(summary, secrets, "summary")
    _assert_clean(memory_text or "", secrets, "memory")
    assert "CLAUDE_CODE_OAUTH_TOKEN=***" in summary and "GH_TOKEN=***" in summary
    mem_live = json.dumps(
        [e for tid, e in published if tid == done["id"] and e["type"] == "memory_updated"]
    )
    _assert_clean(mem_live, secrets, "memory_updated")


def test_failed_task_error_is_redacted(client):
    m, done, live, secrets = _echo_task(client, "print env then fail [env-echo-fail]")
    assert done["status"] == "failed"
    err = done["error"]
    assert err and "GH_TOKEN=***" in err and "CO_API_TOKEN=***" in err
    _assert_clean(err, secrets, "error")
    _assert_clean(done["result_text"] or "", secrets, "result_text")
    db, api = _blob_of(done["id"], m)
    _assert_clean(db, secrets, "db")
    _assert_clean(api, secrets, "api")
    _assert_clean(live, secrets, "live")
