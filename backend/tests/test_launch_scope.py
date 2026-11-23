"""Phase 5b Task 2: scope regex (forge v0.7.0 cases), cooldown, scope-check endpoint."""

import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.database import SessionLocal
from app.launch import scope
from app.main import app
from conftest import login_as

POSITIVE = [
    "Let's also add a dark mode toggle",
    "can we add CSV export to the report page?",
    "What if we add Google login for users",
    "please implement a new feature: team invites",
    "please add support for PostgreSQL 17",
    "while we're at it, refactor the whole router",
    "WHILE WE ARE AT IT make the sidebar resizable",
    "lets also create a weekly email digest",
    "could we add a dashboard for admins",
    "how about adding push notifications",
    "one more thing: a public API for partners",
    "let’s also build an iOS app",
    "it would be nice to have offline mode",
    "while we're at it, can we add dark mode",
]
NEGATIVE = [
    "fix the failing test in billing",
    "rename total to totalCents in src/billing/total.ts",
    "why does the login page crash on Safari?",
    "renew feature flags before the release",
    "the newest feature branch is broken, check it",
    "run npm test and tell me what fails",
    "what is the difference between these two functions?",
    "commit this with a short message",
    "can we address the review comments first",
    "add a test for the rounding bug",
    "whatever we did yesterday broke the build",
    "yes",
    "Explain what the new feature flag does",
    "What if we get a 500 here?",
    "Can we add a test for the bug I just fixed?",
    "let's also run the linter",
    "what does the new feature toggle in config.js do",
    "why is the new feature page slow?",
    "what if we are offline, does it crash?",
    "let's also fix the typo in the README",
    "while we're at it, fix the lint errors",
    "explain how the new features in v2 work",
    "can we add a test for the parser",
    "let's also check the logs",
    "what does the add button do?",
    "run the tests",
    "fix the login bug",
    "/scope",
    "/scope can we add dark mode",
]


def test_positive_and_negative_prompts():
    for p in POSITIVE:
        assert scope.looks_like_new_scope(p), p
    for p in NEGATIVE:
        assert not scope.looks_like_new_scope(p), p


def test_long_and_odd_input():
    assert not scope.looks_like_new_scope("a" * 400 + " can we add x")
    assert not scope.looks_like_new_scope("x " * 200 + "can we add a thing")
    assert scope.looks_like_new_scope("x " * 100 + "can we add a thing")
    assert not scope.looks_like_new_scope("")
    assert not scope.looks_like_new_scope(None)


def test_hostile_text_is_fast():
    for text in ("let's also " * 36, "while we " * 44, "add a new " * 39, ("let us also " + " " * 30) * 9, "a" * 399):
        assert len(text) <= 400
        t = time.perf_counter()
        scope.looks_like_new_scope(text[:399])
        assert time.perf_counter() - t < 0.05


def test_cooldown_class():
    c = scope.Cooldown(ttl=0.05)
    assert not c.active("u", "p")
    c.mark("u", "p")
    assert c.active("u", "p") and not c.active("u", "q")
    time.sleep(0.07)
    assert not c.active("u", "p")


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.fixture()
def world(client):
    scope.reset()
    pid = client.post("/projects", json={"name": f"sc-{_uid()}"}).json()["id"]
    contrib = login_as(f"sc-{_uid()}@test.local")
    r = client.put(f"/projects/{pid}/members", json=[
        {"subject_type": "user", "subject_id": contrib.user_id, "role": "contributor"}])
    assert r.status_code == 200, r.text
    yield {"pid": pid, "base": f"/projects/{pid}/launch", "c": contrib, "admin": client}
    scope.reset()


def _add(c, w, title):
    assert c.post(f"{w['base']}/items", json={"title": title}).status_code == 201


def check(c, w, prompt="while we're at it, can we add dark mode"):
    r = c.post(f"{w['base']}/scope-check", json={"prompt": prompt})
    assert r.status_code == 200, r.text
    return r.json()


def test_no_launch_data_is_false(world):
    assert check(world["c"], world) == {"new_scope": False, "open": 0, "would_be": 1, "launch_date": None}


def test_nudge_then_cooldown_then_reset(world):
    w, c = world, world["c"]
    _add(c, w, "one")
    _add(c, w, "two")
    first = check(c, w)
    assert first == {"new_scope": True, "open": 2, "would_be": 3, "launch_date": None}
    assert check(c, w)["new_scope"] is False
    scope.reset()
    assert check(c, w)["new_scope"] is True


def test_non_scope_prompt_does_not_use_cooldown(world):
    w, c = world, world["c"]
    _add(c, w, "one")
    assert check(c, w, "fix the login bug")["new_scope"] is False
    assert check(c, w)["new_scope"] is True


def test_cooldown_is_per_user_and_project(world):
    w, c = world, world["c"]
    _add(c, w, "one")
    assert check(c, w)["new_scope"] is True
    assert check(w["admin"], w)["new_scope"] is True  # another user, same project


def test_launch_row_alone_counts_as_data(world):
    w = world
    r = w["admin"].put(w["base"], json={"launch_date": "2027-01-01", "critical_paths": []})
    assert r.status_code == 200, r.text
    out = check(w["c"], w)
    assert out["new_scope"] is True and out["launch_date"] == "2027-01-01" and out["would_be"] == 1


def test_outsider_404_and_agent_never_nudged(world):
    w = world
    _add(w["c"], w, "one")
    assert login_as(f"sc-o-{_uid()}@test.local").post(
        f"{w['base']}/scope-check", json={"prompt": "can we add x"}).status_code == 404

    async def mint():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, w["c"].user_id, "agent-task", None, 3600)
            await s.commit()
            return raw

    a = TestClient(app)
    a.headers["Authorization"] = f"Bearer {asyncio.run(mint())}"
    r = a.post(f"{w['base']}/scope-check", json={"prompt": "can we add x"})
    assert r.status_code in (401, 403) or r.json()["new_scope"] is False
