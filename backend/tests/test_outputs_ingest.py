"""Phase 6c Task 3 (F105): the worker turns co-guard's ``Output`` spool lines into ``output`` task events
(``{link, tool, title, local_file, kept}``). The spool sits in the task's folder, so every line is checked again:
the same link rules and caps, at most 20 ``output`` events per task counted in the database, the same link once,
a malformed line ignored, and never a ``guard`` event."""

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select

from app import worker as worker_mod
from app.database import SessionLocal
from app.guard import runtime as guard_runtime
from app.models import Project, Task, TaskEvent


def _task_row(workspace=None, project_dir=None):
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            pid = None
            if project_dir is not None:
                pid = uuid.uuid4().hex
                s.add(Project(id=pid, name="p-" + pid[:6], slug="p-" + pid, directory=str(project_dir)))
                await s.flush()
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="running", project_id=pid,
                       workspace_dir=str(workspace) if workspace is not None else None))
            await s.commit()

    asyncio.run(go())
    return tid


def _events(tid):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(TaskEvent).where(TaskEvent.task_id == tid)
                                    .order_by(TaskEvent.seq))).scalars().all()

    return asyncio.run(go())


def _ingest(tid, *recs, raw=""):
    w = worker_mod.WorkerManager()
    d = guard_runtime.make_task_dir(tid)
    (d / guard_runtime.SPOOL_NAME).write_text("".join(json.dumps(r) + "\n" for r in recs) + raw)
    asyncio.run(w._ingest_guard_spool(tid, w._emit_offline_for(tid)))
    assert not d.exists()
    return [e.payload for e in _events(tid) if e.type == "output"]


def out(link, **kw):
    return {"event": "Output", "link": link, "tool": "mcp__p__publish", "title": None, "local_file": None,
            "ts": 1.0, **kw}


def test_record_becomes_an_output_event_with_kept(client, tmp_path):
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "page.html").write_text("x")
    tid = _task_row(workspace=tmp_path)
    got = _ingest(tid, out("https://ex.example.com/a", title="Page", local_file="site/page.html"),
                  out("https://ex.example.com/b", tool="Bash"))
    assert got == [
        {"link": "https://ex.example.com/a", "tool": "mcp__p__publish", "title": "Page",
         "local_file": "site/page.html", "kept": True},
        {"link": "https://ex.example.com/b", "tool": "Bash", "title": None, "local_file": None, "kept": False}]
    evs = _events(tid)
    assert not [e for e in evs if e.type == "guard"]  # never a guard event
    assert all(len(e.type) <= 32 for e in evs)


@pytest.mark.parametrize("rec", [
    out("ftp://ex.example.com/a"),
    out("javascript:alert(1)"),
    out("file:///etc/passwd"),
    out("https:///nohost"),
    out("https://ex.example.com/" + "a" * 600),
    out("https://ex.example.com/a‮b"),
    out("https://ex.example.com/a\nb"),
    out("https://ex.example.com:99999/a"),
    out(5),
    out(None),
    out(["https://ex.example.com/a"]),
    out("https://ex.example.com/a", tool=None),
    out("https://ex.example.com/a", tool=7),
    out("https://ex.example.com/a", title=["x"]),
    out("https://ex.example.com/a", local_file={"x": 1}),
    out("https://hooks.slack.com/services/T0/B0/abcdefgh"),
    out("https://discord.com/api/webhooks/123/tok"),
    out("https://ex.example.com/hooks/webhook/abc"),
    out("https://.../a"),
    out("https://intranet/a"),
    out("https://ex.example.com/a\\b"),
    {"event": "Output"},
    {"event": "Output", "link": "https://ex.example.com/a"},
])
def test_malformed_or_non_http_record_ignored(client, rec):
    tid = _task_row()
    assert _ingest(tid, rec) == []
    assert not [e for e in _events(tid) if e.type == "guard"]


def test_link_cleaned_again_on_the_server(client):
    tid = _task_row()
    got = _ingest(tid, out("HTTPS://u:p@Ex.Example.com/a?token=zzz#f"))
    assert [g["link"] for g in got] == ["https://ex.example.com/a"]
    assert "zzz" not in json.dumps([e.payload for e in _events(tid)])


def test_title_and_tool_capped_and_one_line(client):
    tid = _task_row()
    got = _ingest(tid, out("https://ex.example.com/a", title="a\nb‮" + "x" * 300, tool="mcp__p__" + "y" * 300))
    assert got[0]["title"] == ("a b" + "x" * 300)[:120]
    assert got[0]["tool"] == ("mcp__p__" + "y" * 300)[:200]


def test_malformed_lines_around_a_good_one(client):
    tid = _task_row()
    got = _ingest(tid, out("https://ex.example.com/ok"), raw='{"event": "Output", "link": \n[1,2]\n"str"\n')
    assert [g["link"] for g in got] == ["https://ex.example.com/ok"]


def test_cap_and_duplicates_are_counted_in_the_database(client):
    tid = _task_row()
    first = _ingest(tid, *[out(f"https://ex.example.com/{i}") for i in range(15)], out("https://ex.example.com/0"))
    assert len(first) == 15  # the repeated link once
    # a second step of the same task (a new spool): the database already holds 15
    got = _ingest(tid, out("https://ex.example.com/3"), *[out(f"https://ex.example.com/n{i}") for i in range(10)])
    assert len(got) == 20
    assert len({g["link"] for g in got}) == 20
    assert "https://ex.example.com/n4" in {g["link"] for g in got}
    assert "https://ex.example.com/n5" not in {g["link"] for g in got}


def test_kept_is_checked_on_disk(client, tmp_path):
    ws, proj, outside = tmp_path / "ws", tmp_path / "proj", tmp_path / "outside"
    for d in (ws, proj, outside):
        d.mkdir()
    (ws / "in_ws.md").write_text("x")
    (proj / "docs").mkdir()
    (proj / "docs" / "in_proj.md").write_text("x")
    (outside / "secret.md").write_text("x")
    (ws / "link.md").symlink_to(outside / "secret.md")
    (ws / "outdir").symlink_to(outside)
    (ws / "folder").mkdir()
    tid = _task_row(workspace=ws, project_dir=proj)
    cases = {"in_ws.md": True, "docs/in_proj.md": True, "missing.md": False, "link.md": False,
             "outdir/secret.md": False, "folder": False, "../outside/secret.md": False,
             str(outside / "secret.md"): False, "/etc/hosts": False, "": False}
    got = _ingest(tid, *[out(f"https://ex.example.com/k{i}", local_file=lf) for i, lf in enumerate(cases)])
    assert len(got) == len(cases)
    for (lf, kept), g in zip(cases.items(), got):
        assert g["kept"] is kept, lf
        assert g["local_file"] == (lf if kept else None), lf


def test_kept_false_without_a_task_row(client, tmp_path):
    assert guard_runtime.output_payload(out("https://ex.example.com/a", local_file="a.md"))["kept"] is False
    (tmp_path / "a.md").write_text("x")
    p = guard_runtime.output_payload(out("https://ex.example.com/a", local_file="a.md"), [str(tmp_path)])
    assert p["kept"] is True and p["local_file"] == "a.md"
    assert guard_runtime.output_payload(out("https://ex.example.com/a", local_file="a.md"), ["rel/dir"])[
        "kept"] is False


def test_stored_events_without_a_text_link_use_no_slot(client):
    tid = _task_row()

    async def seed():
        async with SessionLocal() as s:
            for i, pl in enumerate([{"link": None}, {}, {"link": 5}, {"link": ["x"]}]):
                s.add(TaskEvent(task_id=tid, seq=i + 1, type="output", payload=pl))
            await s.commit()

    asyncio.run(seed())
    got = _ingest(tid, *[out(f"https://ex.example.com/s{i}") for i in range(20)])
    assert len([g for g in got if isinstance(g.get("link"), str)]) == 20
