"""Personal credentials (/me/credentials) and the org fallback key (Phase 3a, Task 1).

Secrets are write-only: no response ever carries one. Every value below is an
obviously fake test string."""

import asyncio
import uuid

import httpx
import pytest

from app import credentials
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import UserCredential
from conftest import login_as
from test_lockdown import _all_http_routes

FALLBACK = "sk-test-fallback"  # what conftest sets for the whole session


def _uid():
    return uuid.uuid4().hex[:8]


def _fake(kind: str) -> str:
    return f"fake-{kind}-{uuid.uuid4().hex}"


def _run(coro):
    return asyncio.run(coro)


async def _cred_row(user_id):
    async with SessionLocal() as s:
        return await s.get(UserCredential, user_id)


async def _claude_secret(user_id):
    async with SessionLocal() as s:
        return await credentials.get_claude_secret(s, user_id)


async def _github(user_id):
    async with SessionLocal() as s:
        return await credentials.get_github(s, user_id)


def _restore_fallback(admin):
    r = admin.put("/admin/org-fallback", json={"api_key": FALLBACK})
    assert r.status_code == 200, r.status_code


def test_put_get_never_returns_secret(client):
    m = login_as(f"cred-a-{_uid()}@test.local")
    claude, gh = _fake("oauth"), _fake("gh")
    r = m.put("/me/credentials", json={
        "claude_token": claude, "claude_kind": "oauth_token", "github_token": gh,
        "git_name": "Ada Tester", "git_email": "ada@test.local",
    })
    assert r.status_code == 200, r.status_code
    assert claude not in r.text and gh not in r.text
    g = m.get("/me/credentials")
    assert g.status_code == 200
    assert claude not in g.text and gh not in g.text
    body = g.json()
    assert body == r.json()
    assert body["claude"] == "own" and body["claude_kind"] == "oauth_token"
    assert body["github"] is True
    assert body["git_name"] == "Ada Tester" and body["git_email"] == "ada@test.local"
    # Stored encrypted only: no column holds the plaintext.
    row = _run(_cred_row(m.user_id))
    for col in (row.claude_enc, row.github_enc):
        assert claude not in col and gh not in col
    assert _run(_claude_secret(m.user_id)) == ("CLAUDE_CODE_OAUTH_TOKEN", claude)
    assert _run(_github(m.user_id)) == (gh, "Ada Tester", "ada@test.local")


def test_put_validation(client):
    m = login_as(f"cred-v-{_uid()}@test.local")
    bad = [
        {"claude_token": _fake("x")},  # no kind
        {"claude_kind": "api_key"},  # kind without a token
        {"claude_token": "", "claude_kind": "api_key"},
        {"claude_token": "   ", "claude_kind": "api_key"},
        {"claude_token": _fake("x"), "claude_kind": "password"},
        {"github_token": ""},
        {"github_token": None},
        {"github_token": "fake-nbsp\u00a0inside"},  # any whitespace, not just " "
        {"claude_token": "fake-em\u2003space", "claude_kind": "api_key"},
        {"git_name": "evil\n[core]"},
        {"git_email": "not-an-email"},
        {"unknown_field": "x"},
    ]
    for body in bad:
        r = m.put("/me/credentials", json=body)
        assert r.status_code == 422, (list(body), r.status_code)
    assert m.get("/me/credentials").json()["claude"] == "fallback"


def test_422_never_echoes_a_secret(client):
    m = login_as(f"cred-e-{_uid()}@test.local")
    secret = _fake("oauth")
    for body in (
        {"claude_token": secret},  # model-level error
        {"claude_token": secret, "claude_kind": "bogus"},  # field-level error
        {"github_token": f"{secret} spaced"},
        {"claude_token": secret, "claude_kind": "api_key", "extra": 1},
        [secret],  # not even an object
    ):
        r = m.put("/me/credentials", json=body)
        assert r.status_code == 422 and secret not in r.text, r.status_code
    r = client.put("/admin/org-fallback", json={"api_key": secret, "x": 1})
    assert r.status_code == 422 and secret not in r.text
    r = client.put("/admin/org-fallback", json=secret)
    assert r.status_code == 422 and secret not in r.text
    assert client.get("/admin/org-fallback").json()["set"] is True


def test_switch_kind_replaces(client):
    m = login_as(f"cred-s-{_uid()}@test.local")
    oauth, key = _fake("oauth"), _fake("apikey")
    assert m.put("/me/credentials", json={"claude_token": oauth, "claude_kind": "oauth_token"}).status_code == 200
    r = m.put("/me/credentials", json={"claude_token": key, "claude_kind": "api_key"})
    assert r.status_code == 200
    assert r.json()["claude"] == "own" and r.json()["claude_kind"] == "api_key"
    assert _run(_claude_secret(m.user_id)) == ("ANTHROPIC_API_KEY", key)
    row = _run(_cred_row(m.user_id))
    from app.crypto import decrypt_json

    stored = decrypt_json(row.claude_enc)
    assert list(stored.values()) == [key]  # the oauth token is gone, not kept beside it


def test_partial_put_leaves_other_fields(client):
    m = login_as(f"cred-p-{_uid()}@test.local")
    gh = _fake("gh")
    m.put("/me/credentials", json={"github_token": gh, "git_name": "N", "git_email": "n@test.local"})
    r = m.put("/me/credentials", json={"git_email": "n2@test.local"})
    assert r.json()["git_name"] == "N" and r.json()["git_email"] == "n2@test.local"
    assert r.json()["github"] is True
    r = m.put("/me/credentials", json={"git_name": None, "git_email": None})
    assert r.json()["git_name"] is None and r.json()["git_email"] is None
    assert r.json()["github"] is True
    assert _run(_github(m.user_id)) == (gh, None, None)


def test_delete_parts(client):
    m = login_as(f"cred-d-{_uid()}@test.local")
    m.put("/me/credentials", json={
        "claude_token": _fake("k"), "claude_kind": "api_key", "github_token": _fake("gh"),
        "git_name": "D",
    })
    r = m.delete("/me/credentials/claude")
    assert r.status_code == 200
    assert r.json()["claude"] == "fallback" and r.json()["claude_kind"] is None
    assert r.json()["github"] is True and r.json()["git_name"] == "D"
    assert _run(_claude_secret(m.user_id)) is None
    r = m.delete("/me/credentials/github")
    assert r.status_code == 200
    assert r.json()["github"] is False and r.json()["git_name"] == "D"
    assert _run(_github(m.user_id)) == (None, "D", None)
    # Deleting what isn't there is fine (idempotent).
    assert m.delete("/me/credentials/github").status_code == 200
    fresh = login_as(f"cred-d2-{_uid()}@test.local")
    assert fresh.delete("/me/credentials/claude").status_code == 200


def test_other_user_cannot_read(client):
    # No credential route can even name another user.
    cred_routes = [
        (m, p) for m, p in _all_http_routes(app)
        if p.startswith("/me/") or p == "/admin/org-fallback"
    ]
    assert cred_routes
    # The one exception names a key, never a person: the handler acts only on the caller's own editor key (another
    # person's key id is 404, tests/test_editor_mcp.py::test_delete_only_own_key_and_admin_revokes_any).
    named = {(m, p) for m, p in cred_routes if "{" in p}
    assert named == {("DELETE", "/me/editor-keys/{key_id}")}, cred_routes
    m = login_as(f"cred-o-{_uid()}@test.local")
    m.put("/me/credentials", json={"github_token": _fake("gh"), "git_name": "Only Mine"})
    other = login_as(f"cred-o2-{_uid()}@test.local")
    assert other.get("/me/credentials").json()["git_name"] is None
    assert other.get("/me/credentials").json()["github"] is False
    # The admin's /me is the admin's own, not anyone else's.
    assert client.get("/me/credentials").json()["git_name"] != "Only Mine"
    # The user_id query/body is not a way in either.
    assert other.get("/me/credentials", params={"user_id": m.user_id}).json()["git_name"] is None


def test_org_fallback_admin_only(client):
    m = login_as(f"cred-f-{_uid()}@test.local")
    assert m.get("/admin/org-fallback").status_code == 403
    assert m.put("/admin/org-fallback", json={"api_key": _fake("k")}).status_code == 403
    assert m.delete("/admin/org-fallback").status_code == 403

    g = client.get("/admin/org-fallback")
    assert g.status_code == 200 and FALLBACK not in g.text
    assert g.json()["set"] is True and g.json()["updated_at"]
    assert client.put("/admin/org-fallback", json={"api_key": ""}).status_code == 422
    try:
        r = client.delete("/admin/org-fallback")
        assert r.status_code == 200 and r.json()["set"] is False
        assert m.get("/me/credentials").json()["claude"] == "none"
        assert _run(_fallback()) is None
        other = _fake("org")
        r = client.put("/admin/org-fallback", json={"api_key": other})
        assert r.status_code == 200 and other not in r.text and r.json()["set"] is True
        assert m.get("/me/credentials").json()["claude"] == "fallback"
        assert _run(_fallback()) == other
    finally:
        _restore_fallback(client)
    assert _run(_fallback()) == FALLBACK


async def _fallback():
    async with SessionLocal() as s:
        return await credentials.get_fallback_key(s)


# --------------------------------------------------------------------------- test connection


def _probe_script(tmp_path, body: str):
    p = tmp_path / "probe_claude.sh"
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(0o755)
    return str(p)


def test_test_connection_claude_failure_redacted(client, tmp_path, monkeypatch):
    m = login_as(f"cred-t-{_uid()}@test.local")
    secret = _fake("oauth")
    m.put("/me/credentials", json={"claude_token": secret, "claude_kind": "oauth_token"})
    names = tmp_path / "names.txt"
    monkeypatch.setattr(settings, "claude_bin", _probe_script(tmp_path, (
        f'env | cut -d= -f1 > "{names}"\n'
        'echo "auth failed for token $CLAUDE_CODE_OAUTH_TOKEN (again: $CLAUDE_CODE_OAUTH_TOKEN)" >&2\n'
        "exit 1\n"
    )))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-own-key-must-not-leak")
    monkeypatch.setenv("RANDOM_SERVER_VAR", "x")
    r = m.post("/me/credentials/test", json={"which": "claude"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert secret not in r.text and "***" in body["detail"]
    assert "auth failed" in body["detail"] and len(body["detail"]) <= 200
    # The probe env is the allowlisted base plus exactly the one Claude variable.
    got = {n for n in names.read_text().split() if n}
    allowed = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "TMPDIR", "TZ",
               "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "GH_CONFIG_DIR", "GIT_CONFIG_GLOBAL",
               "GIT_CONFIG_NOSYSTEM"}
    # bash itself sets a few (PWD, SHLVL, _, OLDPWD) when started.
    extra = {n for n in got - allowed if not n.startswith("LC_")} - {"PWD", "SHLVL", "_", "OLDPWD"}
    assert not extra, extra
    assert "CLAUDE_CODE_OAUTH_TOKEN" in got and "ANTHROPIC_API_KEY" not in got
    # Per-user CLI config, never the server's ~/.claude.
    assert "CLAUDE_CONFIG_DIR" in got


def test_test_connection_uses_per_user_config_dir(client, tmp_path, monkeypatch):
    m = login_as(f"cred-cd-{_uid()}@test.local")
    m.put("/me/credentials", json={"claude_token": _fake("k"), "claude_kind": "api_key"})
    seen = tmp_path / "dir.txt"
    monkeypatch.setattr(settings, "claude_bin", _probe_script(tmp_path, (
        f'echo "$CLAUDE_CONFIG_DIR" > "{seen}"\nexit 0\n'
    )))
    assert m.post("/me/credentials/test", json={"which": "claude"}).json()["ok"] is True
    want = settings.workspaces_dir / "_users" / m.user_id / "claude"
    assert seen.read_text().strip() == str(want)


def test_test_connection_claude_own_only(client, tmp_path, monkeypatch):
    m = login_as(f"cred-ts-{_uid()}@test.local")
    seen = tmp_path / "seen.txt"
    monkeypatch.setattr(settings, "claude_bin", _probe_script(tmp_path, (
        f'echo "$ANTHROPIC_API_KEY" > "{seen}"\n'
        'echo OK\n'
        "exit 0\n"
    )))
    # No own credential: the org fallback being set doesn't make "my" test pass.
    r = m.post("/me/credentials/test", json={"which": "claude"})
    assert r.status_code == 200
    assert r.json() == {
        "ok": False,
        "detail": "No Claude credential: add one under My credentials (or ask an admin to set an org fallback key).",
    }
    assert not seen.exists()  # nothing was run
    own = _fake("apikey")
    m.put("/me/credentials", json={"claude_token": own, "claude_kind": "api_key"})
    r = m.post("/me/credentials/test", json={"which": "claude"})
    assert r.status_code == 200 and r.json()["ok"] is True, r.json()
    assert seen.read_text().strip() == own
    assert own not in r.text


def test_test_connection_one_at_a_time(client, tmp_path, monkeypatch):
    import threading
    import time

    m = login_as(f"cred-1-{_uid()}@test.local")
    m.put("/me/credentials", json={"claude_token": _fake("k"), "claude_kind": "api_key"})
    started, release = tmp_path / "started", tmp_path / "release"
    monkeypatch.setattr(settings, "claude_bin", _probe_script(tmp_path, (
        f'touch "{started}"\n'
        f'for i in $(seq 200); do [ -f "{release}" ] && exit 0; sleep 0.05; done\nexit 1\n'
    )))
    out = {}
    th = threading.Thread(
        target=lambda: out.update(r=m.post("/me/credentials/test", json={"which": "claude"}))
    )
    th.start()
    try:
        for _ in range(200):
            if started.exists():
                break
            time.sleep(0.05)
        assert started.exists()
        assert m.post("/me/credentials/test", json={"which": "claude"}).status_code == 429
        assert m.post("/me/credentials/test", json={"which": "github"}).status_code == 429
        # Another user is not blocked.
        other = login_as(f"cred-1b-{_uid()}@test.local")
        assert other.post("/me/credentials/test", json={"which": "github"}).status_code == 200
    finally:
        release.touch()
        th.join(timeout=15)
    assert out["r"].status_code == 200 and out["r"].json()["ok"] is True
    # Released afterwards.
    assert m.post("/me/credentials/test", json={"which": "claude"}).status_code == 200


def test_claude_probe_killed_on_cancel(tmp_path, monkeypatch):
    import os
    import time

    pidf = tmp_path / "pid"
    monkeypatch.setattr(settings, "claude_bin", _probe_script(tmp_path, (
        f'echo $$ > "{pidf}"\nexec sleep 30\n'
    )))

    async def go():
        t = asyncio.ensure_future(credentials.run_claude_probe(dict(os.environ)))
        for _ in range(200):
            if pidf.exists() and pidf.read_text().strip():
                break
            await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    _run(go())
    pid = int(pidf.read_text())
    time.sleep(0.1)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def _mock_github(monkeypatch, status, payload, seen):
    def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(
        credentials, "_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_test_connection_github(client, monkeypatch):
    m = login_as(f"cred-g-{_uid()}@test.local")
    seen = {}
    _mock_github(monkeypatch, 200, {"login": "octo-tester"}, seen)
    r = m.post("/me/credentials/test", json={"which": "github"})
    assert r.json()["ok"] is False and "github" in r.json()["detail"].lower()
    assert "auth" not in seen  # nothing to test: no request made

    gh = _fake("gh")
    m.put("/me/credentials", json={"github_token": gh})
    r = m.post("/me/credentials/test", json={"which": "github"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "detail": "octo-tester"}
    assert seen["auth"] == f"Bearer {gh}" and seen["url"] == "https://api.github.com/user"

    _mock_github(monkeypatch, 401, {"message": f"Bad credentials {gh}"}, seen)
    r = m.post("/me/credentials/test", json={"which": "github"})
    assert r.json()["ok"] is False and gh not in r.text

    assert m.post("/me/credentials/test", json={"which": "gitlab"}).status_code == 422
