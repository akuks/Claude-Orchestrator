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
from conftest import login_as, time_limit, wait_task

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


# ---- final fix wave (forge F90b): more secret shapes, the same in the hook's copy ------------------

import pytest  # noqa: E402

from app.guard import co_hook  # noqa: E402

REDACTORS = {
    "server": lambda text: redaction.redact(text, []),
    "hook": lambda text: co_hook.redact(text, None),
}
FAKE_JWT = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")
FAKE_PEM = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEfakefakefakefakefake\nAAAAfakefakefake==\n"
            "-----END RSA PRIVATE KEY-----")
# (input, the secret that must be gone) - forge's redact.test.js cases, then ours.
# Key-shaped fakes are assembled at runtime so secret scanners don't flag the source.
_ANT = "sk-ant-" + "api03-AbCdEf1234567890xyz"
_GHP = "ghp" + "_ABCDEFabcdef1234567890ABCDEFabcdef12"
_AKIA = "AKIA" + "IOSFODNN7" + "EXAMPLE"
_ASIA = "ASIA" + "IOSFODNN7" + "EXAMPLE"
_XOXB = "xox" + "b-1234-5678-abcdefghij"
_XOXP = "xox" + "p-9999-8888-zyxwvu"
SHAPES = [
    (f'curl -H "x-api-key: {_ANT}" https://x', _ANT),
    (f"git clone https://{_GHP}@github.com/o/r", _GHP),
    (f"aws configure set aws_access_key_id {_AKIA}", _AKIA),
    (f"AWS_ACCESS_KEY_ID={_ASIA} aws s3 ls", _ASIA),
    (f"SLACK={_XOXB}", _XOXB),
    (_XOXP, _XOXP),
    ("xoxa-2-fakefake1234 and xoxr-fakefake1234", "fakefake1234"),
    ('curl -H "Authorization: Bearer eyJhbGciOi.abc.def" x', "eyJhbGciOi.abc.def"),
    ("curl -H 'Authorization: bearer abcdef123456' x", "abcdef123456"),
    ('psql "host=db password=hunter2-fake user=me"', "hunter2-fake"),
    ("connect passwd=s3cr3t-fake;", "s3cr3t-fake"),
    ("https://api.example.com/?token=abc123def&x=1", "abc123def"),
    ("https://api.example.com/v1?access_token=fakeAccess99&x=1", "fakeAccess99"),
    ("curl 'https://x/?api_key=fakekey123'", "fakekey123"),
    ("mysql -u root -pS3cretPass mydb", "S3cretPass"),
    ("mysqldump --user=root --password=FakePw-77 db", "FakePw-77"),
    ("export DB_PASSWORD=fake-db-pass", "fake-db-pass"),
    ("GITHUB_TOKEN=fakeghvalue123 gh pr list", "fakeghvalue123"),
    ("client_secret=fake-client-secret", "fake-client-secret"),
    ("aws_secret_access_key: fakeAwsSecretValue", "fakeAwsSecretValue"),
    ("PGPASSWORD='s3 cr3t' psql -h db", "s3 cr3t"),
    ('DB_PASSWORD="short" make', "short"),
    ("mysql -u root -p'S3cretFake' db", "S3cretFake"),
    ('mysql -u root -p"S3cret" db', "S3cret"),
    ("tool --password FakePassword9 --user me", "FakePassword9"),
    ("tool --api-key fake-api-key-1 run", "fake-api-key-1"),
    ("tool --api_key 'fake key 2' run", "fake key 2"),
    ("secret: 'a.b(c)'", "a.b(c)"),  # a quoted literal stays masked whatever it holds
    ("  api_key: fake-yaml-key", "fake-yaml-key"),
    ("accessToken: fakeCamelValue", "fakeCamelValue"),
    ("the cookie is " + FAKE_JWT + " ok", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"),
    ("The database password is: Hunter2-Hunter2 and it works", "Hunter2-Hunter2"),
    ("password: correct-horse-battery", "correct-horse-battery"),
    ("token: abc123XYZ789", "abc123XYZ789"),
    ('{"user":"me","password":"p4ssw0rd-json"}', "p4ssw0rd-json"),
    ("key file:\n" + FAKE_PEM + "\ndone", "MIIEfakefakefakefakefake"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAfake", "b3BlbnNzaC1rZXktdjEAAAAfake"),  # cut
    ("OPENAI_KEY=sk-proj-FAKEfake1234567890abcd", "sk-proj-FAKEfake1234567890abcd"),
]


@pytest.mark.parametrize("which", sorted(REDACTORS))
@pytest.mark.parametrize("text,secret", SHAPES)
def test_secret_shapes_are_redacted(which, text, secret):
    out = REDACTORS[which](text)
    assert secret not in out and "***" in out, out


@pytest.mark.parametrize("which", sorted(REDACTORS))
def test_labels_are_kept(which):
    r = REDACTORS[which]
    assert r("psql password=hunter2-x -h db") == "psql password=*** -h db"
    assert r("PGPASSWORD='s3 cr3t' psql") == "PGPASSWORD='***' psql"
    assert r("mysql -u root -p'S3cretFake' db") == "mysql -u root -p'***' db"
    assert r("tool --password FakePassword9 x") == "tool --password *** x"
    assert r("Authorization: Bearer abcdef123456") == "Authorization: Bearer ***"
    assert r("mysql -u root -pS3cretPass mydb") == "mysql -u root -p*** mydb"
    assert r('{"password":"p4ssw0rd-json"}') == '{"password":"***"}'
    assert r("a\n" + FAKE_PEM + "\nb") == "a\n***\nb"


ORDINARY = [
    "npm test -- save_chart && git commit -m \"add chart model\"",
    "mysql -u root -p mydb",
    "node server.js --port 3000",
    "see https://example.com/docs and the keyboard token economy",
    "ssh://git@host/repo.git",
    "git checkout skip-ahead && git rebase task-sk-runner",
    "git show 3f9c2a7d1e4b5a6c8d9e0f1a2b3c4d5e6f7a8b9c",
    "id 123e4567-e89b-12d3-a456-426614174000",
    "max_tokens: 4096 and tokenizer=fast, passwords are hashed",
    "cd $PWD && echo PWD=/home/me OLDPWD=/tmp",
    "PGPASSWORD=$PGPASS psql -h db; DB_PASSWORD=${DB_PASSWORD} make; TOKEN=$(cat t) x",
    "rule secret-files blocked it (secrets.yml)",
    "eyJ alone is not a token, nor eyJabc.def",
    "AKIA is a prefix; AKIAshort",
    # coordinator's re-review: code and prose with secret words survive
    "token_count: 5",
    "token: refresh logic fixed",
    "if token == expected:",
    "csrf_token = request.form['x']",
    "secret: none",
    "token=None",
    "no_password: true",
    "pwd=/home/x",
    "password: NULL, secret: False, token: true, api_key: ''",
    "token = get_token(user)",
    "api_key = settings.API_KEY",
    "password=12345678901",
    "the --password flag and --api-key - are documented",
    "skeleton sk-short",
]


@pytest.mark.parametrize("which", sorted(REDACTORS))
@pytest.mark.parametrize("text", ORDINARY)
def test_ordinary_text_is_not_redacted(which, text):
    assert REDACTORS[which](text) == text


def test_hook_and_server_use_the_same_patterns():
    assert co_hook._TOKEN_SHAPES.pattern == redaction.TOKEN_SHAPES.pattern
    assert co_hook._TOKEN_SHAPES.flags == redaction.TOKEN_SHAPES.flags
    assert [(r.pattern, r.flags) for r in co_hook._LABELLED] == [(r.pattern, r.flags) for r in redaction.LABELLED]
    assert co_hook._URL_USERINFO.pattern == redaction.URL_USERINFO.pattern


def test_redaction_stays_linear_on_long_input():
    blob = "a" * 200_000 + " password" * 2000 + " mysql " + "x" * 50_000
    with time_limit(3.0):
        redaction.redact(blob, [])
        co_hook.redact(blob, None)


# ---- phase 7e (F90b): strip_query, redact_head, the workspace's .env values ----------------

QUERY_CASES = [
    ("see https://h.example/a/b?token=abc&x=1#frag now", "see https://h.example/a/b now"),
    ("https://h.example/p#only-frag", "https://h.example/p"),
    ("(https://h.example/x?a=1).", "(https://h.example/x)."),
    ("git clone ssh://git@h.example/r.git?ref=main", "git clone ssh://git@h.example/r.git"),
    ('"http://127.0.0.1:8000/api?q=1"', '"http://127.0.0.1:8000/api"'),
    ("no url here? really#not", "no url here? really#not"),
    ("https://h.example/plain", "https://h.example/plain"),
    ("two https://a.example/?a=1 and https://b.example/c?d=2", "two https://a.example/ and https://b.example/c"),
    ("", ""),
    # review M1: glued to a word, many letters before the scheme, JSON-escaped
    ("foo_https://h/x?token=a", "foo_https://h/x"),
    ("2https://h/x?token=a", "2https://h/x"),
    ("a" * 40 + "https://h/x?token=a", "a" * 40 + "https://h/x"),
    (r'{"u":"https:\/\/h\/x?token=abc"}', r'{"u":"https:\/\/h\/x"}'),
    # review C1: a ? or # inside user:password@ is not a query
    ("https://u:p?w#d@h.example/x?t=1", "https://u:p?w#d@h.example/x"),
    ("https://h.example?x=1", "https://h.example"),
]


PASSWORDS = ["Pa55w0rd#Zq81x", "Pa55?w0rd", "pa#ss?wd#9"]


@pytest.mark.parametrize("pw", PASSWORDS)
@pytest.mark.parametrize("known", [True, False])
def test_url_password_with_query_chars_never_leaks(pw, known, monkeypatch):
    """Review C1: redact first, then strip - no part of a #/? password survives, known secret or not."""
    from app.resume import handoff
    from app.worker import WorkerManager, _short

    text = f"git clone https://deploy:{pw}@git.example.com/x?t=1 done"
    secrets = [pw] if known else []
    parts = [p for p in re.split(r"[#?]", pw) if p]
    outs = [_short(text, secrets), redaction.strip_query(redaction.redact_head(text, secrets)),
            WorkerManager.guard_payload({"summary": text}, secrets)["summary"],
            handoff.build(_handoff_input(text, secrets))["task"]]
    for k in list(os.environ):
        if co_hook._SECRET_NAME.search(k):
            monkeypatch.delenv(k)
    if known:
        monkeypatch.setenv("GIT_PASSWORD", pw)
    outs.append(co_hook.strip_query(co_hook.redact(text)))
    for out in outs:
        assert not any(p in out for p in parts), out
        assert "git.example.com/x" in out and "t=1" not in out


def _handoff_input(prompt, secrets):
    from datetime import datetime, timezone

    from app.resume import handoff

    now = datetime(2026, 11, 25, tzinfo=timezone.utc)
    step = {"id": "s1", "title": "t", "prompt": prompt, "origin": "user", "status": "completed", "created_at": now,
            "started_at": now, "completed_at": now, "result_text": None, "notes": None, "notes_updated_at": None}
    return handoff.HandoffInput(
        steps=[step], step_events=[], facts=None, outside={"commits": [], "rewritten": False}, sources=[], waiting=[],
        children={}, others=[], trust="own", workspace="/w", project_dir="/w", policy={"project_dir": "/w"},
        now=now, reason="run ended", secrets=secrets)


@pytest.mark.parametrize("text,want", QUERY_CASES)
def test_strip_query_server_and_hook_agree(text, want):
    assert redaction.strip_query(text) == want
    assert co_hook.strip_query(text) == want


def test_redact_head_server_and_hook_agree(monkeypatch):
    """Review Focus 2: the hook's copies behave exactly like the server's (no env secrets on either side)."""
    for k in list(os.environ):
        if co_hook._SECRET_NAME.search(k):
            monkeypatch.delenv(k)
    texts = [f"token: {'q' * 30} " + "x" * 5000, "ghp_" + "a" * 36 + " tail", "short", "a" * 4000, "a" * 4001,
             "https://u:pw@h.example/a?k=v"]
    for t in texts:
        for n in (10, 100, 4000):
            assert redaction.redact_head(t, [], n) == co_hook.redact_head(t, n)
        assert redaction.redact_head(t, []) == co_hook.redact_head(t)
        assert len(redaction.redact_head(t, [])) <= 4000
    # redact first, then cut: no secret prefix survives the cut
    sec = "S" * 20
    assert redaction.redact_head("ab" + sec, [sec], 10) == "ab***"
    assert co_hook._STRIP_URL.pattern == redaction.STRIP_URL.pattern
    assert co_hook._URL_TAIL.pattern == redaction._URL_TAIL.pattern


def test_strip_query_stays_linear():
    blob = "x://" * 50_000 + "?" + "a" * 50_000
    with time_limit(3.0):
        redaction.strip_query(blob)
        co_hook.strip_query(blob)


def test_dotenv_values(tmp_path):
    (tmp_path / ".env").write_text(
        "# a comment\nAPI_KEY=abc123xyz789\nexport DB_PASSWORD='hunter2hunter2'\nDEBUG=true\nPORT=8000\n"
        "PIN=123456\nSHORT_TOKEN=abc\nAPP_SECRET=\"dq-value-1\"  # trailing\nURL=postgres://u:pw99@h/db # note\n"
        "EMPTY=\nNAME=frontend-app-2\nPLAIN=ghp_" + "g" * 36 + "\n")
    (tmp_path / ".env.local").write_text("LOCAL_TOKEN=local-token-9\n")
    for skipped in (".envrc", ".env.example", ".env.sample", ".env.template", ".env.dist"):
        (tmp_path / skipped).write_text("RC_SECRET=rc-secret-77\n")
    (tmp_path / "other.env").write_text("NOT_TOP_TOKEN=not-read-001\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / ".env").write_text("DEEP_TOKEN=deep-value-002\n")
    outside = tmp_path.parent / f"outside-{uuid.uuid4().hex[:6]}.env"
    outside.write_text("LINKED_TOKEN=linked-value-003\n")
    (tmp_path / ".env.link").symlink_to(outside)
    (tmp_path / ".env.big").write_text("BIG_TOKEN=big-value-0004\n" + "#" * (64 * 1024))
    (tmp_path / ".env.dir").mkdir()
    vals = redaction.dotenv_values(tmp_path)
    assert set(vals) == {"abc123xyz789", "hunter2hunter2", "dq-value-1", "postgres://u:pw99@h/db", "local-token-9",
                         "ghp_" + "g" * 36}
    assert vals == sorted(vals, key=len, reverse=True)
    assert redaction.dotenv_values(tmp_path / "missing") == []
    assert redaction.dotenv_values(None) == []


def test_dotenv_ordinary_values_are_not_secrets(tmp_path):
    """Review I1: words people and agents need verbatim never become run secrets."""
    (tmp_path / ".env").write_text(
        "DB_USER=postgres\nDB_PASSWORD=password\nDB_HOST=postgres\nAPP_DIR=frontend\nHOST=db.internal.example\n"
        "BIND=0.0.0.0\nAPI_URL=http://localhost:8000\nSECRET_PATH=/opt/Projects_UI/canonizer\nAPI_TOKEN=changeme\n"
        "SECRET_KEY=your-secret-here\nAUTH_PORT=127.0.0.1:5432\nTOKEN_TTL=3600000\nDEBUG_TOKEN=false\n")
    assert redaction.dotenv_values(tmp_path) == []


def test_dotenv_caps(tmp_path):
    """Review I2: at most 200 values and 16 KB in all."""
    (tmp_path / ".env").write_text("".join(f"K{i}_TOKEN=val{i:05d}x\n" for i in range(1000)))
    assert len(redaction.dotenv_values(tmp_path)) == redaction.DOTENV_MAX_VALUES
    (tmp_path / ".env").write_text("".join(f"K{i}_TOKEN={i:03d}{'z' * 1000}\n" for i in range(40)))
    vals = redaction.dotenv_values(tmp_path)
    assert sum(map(len, vals)) <= redaction.DOTENV_MAX_CHARS and len(vals) == 16


def test_run_masks_workspace_dotenv_values(client, tmp_path):
    """A value from the project folder's .env never reaches a stored tool result."""
    d = tmp_path / f"dotenv-{_uid()}"
    d.mkdir()
    (d / ".env").write_text("SERVICE_PASSWORD=dotenv-secret-7e7e\n")
    r = client.post("/projects", json={"name": f"rd-{_uid()}", "directory": str(d)})
    assert r.status_code == 201, r.text
    r = client.post("/tasks", json={"prompt": "go [stream-fixture:dotenv_echo]", "project_id": r.json()["id"]})
    assert r.status_code == 201, r.text
    done = wait_task(client, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    evs = _events(done["id"])
    res = [e.payload for e in evs if e.type == "tool_result"][0]
    assert "dotenv-secret-7e7e" not in json.dumps([e.payload for e in evs])
    assert "***" in res["content"]
    # stored tool results lose URL queries
    assert "https://h.example/download" in res["content"] and "sig=" not in res["content"]
