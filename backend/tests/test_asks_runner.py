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
