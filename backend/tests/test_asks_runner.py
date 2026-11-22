"""Phase 3c Task 2: the ask runner against the mock CLI."""

import asyncio
import os
import tempfile
import uuid

from app.asks.runner import run_ask
from app.asks.texts import ASK_FRAME
from app.config import settings
from conftest import read_probe_args, read_probe_file


def _key():
    return "ask" + uuid.uuid4().hex[:8]


def test_run_ask_argv_exact():
    k = _key()
    asyncio.run(run_ask(f"hi [ask-probe:{k}]", {"PATH": os.environ["PATH"]}))
    assert read_probe_args(k) == [
        "--print", "--output-format", "json", "--model", settings.ask_model, "--max-turns", "1",
        "--permission-mode", "default", "--strict-mcp-config", "--max-budget-usd",
        str(settings.ask_budget_usd), "--append-system-prompt", ASK_FRAME, "--disallowedTools",
        "Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "Read", "Glob", "Grep", "WebFetch",
        "WebSearch", "Task", "Agent", "PowerShell"]
    assert f"[ask-probe:{k}]" in read_probe_file(k, "prompt")


def test_run_ask_json_answer_and_cost():
    k = _key()
    r = asyncio.run(run_ask(f"[ask-probe:{k}]", {"PATH": os.environ["PATH"]}))
    assert (r.answer, r.cost_usd, r.error) == (f"answer for {k}", 0.0123, None)


def test_run_ask_raw_text():
    r = asyncio.run(run_ask("[ask-raw]", {"PATH": os.environ["PATH"]}))
    assert (r.answer, r.cost_usd, r.error) == ("plain answer", None, None)


def test_run_ask_failure():
    r = asyncio.run(run_ask("[ask-fail]", {"PATH": os.environ["PATH"]}))
    assert (r.answer, r.error) == (None, "no answer")


def test_run_ask_missing_binary(monkeypatch):
    monkeypatch.setattr(settings, "claude_bin", "/nonexistent/claude")
    r = asyncio.run(run_ask("x", {"PATH": os.environ["PATH"]}))
    assert r.error == "claude not available"


def test_run_ask_timeout(monkeypatch):
    monkeypatch.setattr(settings, "ask_timeout_seconds", 1)
    r = asyncio.run(run_ask("[ask-sleep]", {"PATH": os.environ["PATH"]}))
    assert (r.answer, r.error) == (None, "timed out")


def test_run_ask_cwd_removed(monkeypatch):
    seen = []
    real = tempfile.mkdtemp

    def spy(*a, **kw):
        d = real(*a, **kw)
        seen.append(d)
        return d

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    asyncio.run(run_ask("[ask-raw]", {"PATH": os.environ["PATH"]}))
    assert seen and not os.path.exists(seen[0])


def test_run_ask_is_error_json_is_failure():
    r = asyncio.run(run_ask("[ask-iserror]", {"PATH": os.environ["PATH"]}))
    assert (r.answer, r.error) == (None, "no answer")


def test_run_ask_failure_keeps_cost():
    env = {"PATH": os.environ["PATH"]}
    assert asyncio.run(run_ask("[ask-budgetstop]", env)).cost_usd == 0.07  # non-zero exit, JSON on stdout
    from app.asks.runner import _parse
    r = _parse('{"is_error": true, "result": "x", "total_cost_usd": 0.02}')
    assert (r.answer, r.error, r.cost_usd) == (None, "no answer", 0.02)


def test_run_ask_cancel_kills_and_cleans(monkeypatch):
    seen, procs = [], []
    real_mk = tempfile.mkdtemp
    real_exec = asyncio.create_subprocess_exec

    def spy(*a, **kw):
        d = real_mk(*a, **kw)
        seen.append(d)
        return d

    async def spy_exec(*a, **kw):
        p = await real_exec(*a, **kw)
        procs.append(p)
        return p

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy_exec)

    async def go():
        t = asyncio.create_task(run_ask("[ask-sleep]", {"PATH": os.environ["PATH"]}))
        await asyncio.sleep(1)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(go())
    assert seen and not os.path.exists(seen[0])
    assert procs and procs[0].returncode is not None
