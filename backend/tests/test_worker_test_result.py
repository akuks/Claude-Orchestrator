import asyncio

from app.worker import WorkerManager

PYTEST_OUT = ("============ FAILURES ============\n"
              "FAILED tests/test_a.py::test_one - AssertionError\n"
              "FAILED tests/test_b.py::test_two - token sk-secret123\n"
              "==== 2 failed, 61 passed, 1 skipped in 3.2s ====\n")


def _run(events, secrets=None):
    w = WorkerManager()
    got = []

    async def emit(t, p):
        got.append((t, p))

    ctx = {"task_id": "T", "mcp_ids": {}, "secrets": secrets or [], "model_stored": True}
    for ev in events:
        asyncio.run(w._handle_stream_event(ev, emit, {}, ctx))
    return got, ctx


def _use(tid, cmd, name="Bash"):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": {"command": cmd}}]}}


def _res(tid, text):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text}]}}


def test_pytest_pair_emits_event_with_redaction():
    got, _ = _run([_use("a", "pytest -q TOKEN=sk-secret123"), _res("a", PYTEST_OUT)], ["sk-secret123"])
    ev = [p for t, p in got if t == "test_result"]
    assert len(ev) == 1
    p = ev[0]
    assert (p["runner"], p["passed"], p["failed"], p["skipped"]) == ("pytest", 61, 2, 1)
    assert len(p["failing"]) == 2 and "sk-secret123" not in str(p)
    assert p["command"].startswith("pytest -q")


def test_non_test_command_and_unparseable_output_emit_nothing():
    got, _ = _run([_use("a", "ls"), _res("a", PYTEST_OUT), _use("b", "npm test"), _res("b", "hello")])
    assert not [1 for t, _p in got if t == "test_result"]


def test_cap_and_parser_error(monkeypatch):
    got, ctx = _run([_use(f"i{i}", "pytest") for i in range(250)])
    assert len(ctx["test_calls"]) == 200
    from app.gates import test_results

    def boom(_t):
        raise RuntimeError("x")

    monkeypatch.setattr(test_results, "parse", boom)
    got, _ = _run([_use("a", "pytest"), _res("a", PYTEST_OUT)])
    assert [t for t, _p in got] == ["tool_use", "tool_result"]
