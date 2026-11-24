"""Phase 6c Task 3 (F105): co-guard's PostToolUse records what a run published outside the project - an
``Output`` spool line for a publishing tool (an MCP tool whose name's last part says create / publish / upload /
post / share / deploy / send, or a ``gh … create`` command) whose answer holds an http(s) link. Spool only:
no network, no text for the agent."""

import json
import os
import statistics
import time

import pytest

from app.guard import co_hook
from test_co_hook import Run, plugin, stub  # noqa: F401

WORDS = ("create", "publish", "upload", "post", "share", "deploy", "send")
GH = ("gh pr create", "gh gist create", "gh release create", "gh issue create")


@pytest.fixture
def orun(plugin, tmp_path, stub):  # noqa: F811
    return Run(plugin, tmp_path, stub.base)


def post(tool, text, tool_input=None, tid="tu1", key="tool_response"):
    p = {"hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": tool_input or {}, "tool_use_id": tid,
         "session_id": "s1"}
    p[key] = [{"type": "text", "text": text}] if isinstance(text, str) else text
    return p


def outputs(run):
    return [r for r in run.spool() if r.get("event") == "Output"]


def one(run, payload):
    run(payload, event="PostToolUse")
    recs = outputs(run)
    assert len(recs) <= 1
    return recs[0] if recs else None


# ---- which calls ------------------------------------------------------------------------------


@pytest.mark.parametrize("word", WORDS)
def test_each_tool_name_word(orun, stub, word):
    out = orun(post(f"mcp__pages__{word}_page", f"Done: https://pages.example.com/p/{word}"), event="PostToolUse")
    assert out is None  # never any text for the agent
    rec = outputs(orun)[0]
    assert {k: v for k, v in rec.items() if k != "ts"} == {
        "event": "Output", "link": f"https://pages.example.com/p/{word}", "tool": f"mcp__pages__{word}_page",
        "title": None, "local_file": None}
    assert stub.requests == []  # no network


@pytest.mark.parametrize("tool", ["mcp__pages__list_pages", "mcp__create__read", "mcp__publish", "Write",
                                  "mcp__x__", "WebFetch", "mcp__db__query"])
def test_other_tools_record_nothing(orun, tool):
    assert one(orun, post(tool, "see https://example.com/a")) is None


def test_word_in_capitals_counts(orun):
    assert one(orun, post("mcp__docs__CreateDocument", "https://docs.example.com/d/1"))["link"] == \
        "https://docs.example.com/d/1"


@pytest.mark.parametrize("form", GH)
def test_each_gh_create_form(orun, form):
    cmd = f"cd repo &&  {form.replace(' ', '  ')} --title 'x' --body 'y'"
    rec = one(orun, post("Bash", {"stdout": "Creating...\nhttps://github.com/o/r/pull/7\n", "stderr": ""},
                         {"command": cmd}))
    assert rec["link"] == "https://github.com/o/r/pull/7" and rec["tool"] == "Bash"


@pytest.mark.parametrize("cmd", ["gh pr view 7", "gh pr list", "echo https://x.example.com", "gh repo create x",
                                 "git push origin main"])
def test_other_commands_record_nothing(orun, cmd):
    assert one(orun, post("Bash", {"stdout": "https://github.com/o/r/pull/7"}, {"command": cmd})) is None


def test_tool_result_key_is_read_too(orun):
    rec = one(orun, post("mcp__p__publish", "https://p.example.com/x", key="tool_result"))
    assert rec["link"] == "https://p.example.com/x"


# ---- the link ---------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["no link here", "ftp://files.example.com/a", "mailto:a@b.c", "https:///nohost",
                                  "http://", ""])
def test_no_link_nothing(orun, text):
    assert one(orun, post("mcp__p__publish", text)) is None


def test_query_fragment_and_userinfo_removed(orun):
    tok = "abcdef0123456789secret"
    rec = one(orun, post("mcp__p__publish", f"Live at HTTPS://user:pa55word@Pages.Example.com:8443/a/b?token={tok}"
                                            f"&x=1#frag then https://second.example.com"))
    assert rec["link"] == "https://pages.example.com:8443/a/b"
    raw = (orun.tdir / "spool.jsonl").read_text()
    assert tok not in raw and "pa55word" not in raw and "user" not in raw and "frag" not in raw


@pytest.mark.parametrize("text,link", [
    ("(see https://ex.example.com/p/1).", "https://ex.example.com/p/1"),
    ("Link: https://ex.example.com/p/2, done", "https://ex.example.com/p/2"),
    ("[page](https://ex.example.com/p/3)", "https://ex.example.com/p/3"),
    ('{"url": "https://ex.example.com/p/4"}', "https://ex.example.com/p/4"),
    ("**https://ex.example.com/p/5**!", "https://ex.example.com/p/5"),
    ("https://ex.example.com", "https://ex.example.com"),
    ("http://[::1]:8080/a", "http://[::1]:8080/a"),
])
def test_prose_punctuation_trimmed(orun, text, link):
    assert one(orun, post("mcp__p__publish", text))["link"] == link


def test_link_from_a_dict_response(orun):
    resp = {"ok": True, "result": {"url": "https://d.example.com/doc/9?auth=zzz"}}
    assert one(orun, post("mcp__docs__create_doc", resp))["link"] == "https://d.example.com/doc/9"


def test_long_link_dropped(orun):
    assert one(orun, post("mcp__p__publish", "https://ex.example.com/" + "a" * 480)) is None
    assert one(orun, post("mcp__p__publish", "https://ex.example.com/" + "b" * 470))["link"].endswith("b" * 470)


@pytest.mark.parametrize("bad", ["https://ex.example.com/a‮b", "https://ex.example.com/a​b",
                                 "https://ex.exa\u0007mple.com/a", "https://ex.example.com:99999/a",
                                 "https://ex_ample\\.com/a"])
def test_control_characters_or_bad_host_drop_the_record(orun, bad):
    assert one(orun, post("mcp__p__publish", bad)) is None


def test_link_holding_a_token_is_never_stored(orun):
    gh = "ghp_" + "Z" * 36
    assert one(orun, post("mcp__p__publish", f"https://hooks.example.com/{gh}/x")) is None
    assert gh not in (orun.tdir / "spool.jsonl").read_text()


# ---- title, tool ------------------------------------------------------------------------------


@pytest.mark.parametrize("ti,title", [
    ({"title": "  Weekly\n report\t now "}, "Weekly report now"),
    ({"name": "Launch notes"}, "Launch notes"),
    ({"subject": "Hello"}, "Hello"),
    ({"title": "T", "name": "N"}, "T"),
    ({"title": 5, "name": "N"}, "N"),
    ({"title": "x" * 300}, "x" * 120),
    ({"title": "a‮b\u0000c"}, "abc"),
    ({"title": "   "}, None),
    ({"other": "x"}, None),
])
def test_title_picked_and_capped(orun, ti, title):
    rec = one(orun, post("mcp__p__publish", "https://ex.example.com/t", ti))
    assert rec["title"] == title
    assert rec["title"] is None or len(rec["title"]) <= 120


def test_tool_is_one_line_and_capped(orun):
    tool = "mcp__p__publish_" + "x" * 300
    rec = one(orun, post(tool, "https://ex.example.com/t"))
    assert rec["tool"] == tool[:200]


# ---- local_file -------------------------------------------------------------------------------


def test_local_file_in_the_project_is_stored_relative(orun):
    (orun.project / "site").mkdir()
    (orun.project / "site" / "page.html").write_text("<p>x</p>")
    rec = one(orun, post("mcp__p__publish", "https://ex.example.com/a",
                         {"title": "Page", "content": "<p>x</p>\n", "path": str(orun.project / "site" / "page.html")}))
    assert rec["local_file"] == "site/page.html"


def test_relative_local_file(orun):
    (orun.project / "notes.md").write_text("x")
    rec = one(orun, post("mcp__p__upload", "https://ex.example.com/a", {"file": "notes.md"}))
    assert rec["local_file"] == "notes.md"


def test_nested_input_and_first_match(orun):
    (orun.project / "a.md").write_text("x")
    (orun.project / "b.md").write_text("x")
    rec = one(orun, post("mcp__p__upload", "https://ex.example.com/a",
                         {"files": [{"path": "missing.md"}, {"path": "b.md"}], "z": "a.md"}))
    assert rec["local_file"] == "b.md"


def test_gh_command_names_a_project_file(orun):
    (orun.project / "report.md").write_text("x")
    rec = one(orun, post("Bash", {"stdout": "https://gist.github.com/u/abc123"},
                         {"command": "gh gist create report.md --public"}))
    assert rec["local_file"] == "report.md"


def test_local_file_in_the_task_workspace(plugin, tmp_path, stub):  # noqa: F811
    ws = tmp_path / "ws"
    ws.mkdir()
    r = Run(plugin, tmp_path, stub.base, cwd=str(ws))
    (ws / "out.pdf").write_text("x")
    rec = one(r, post("mcp__p__upload", "https://ex.example.com/a", {"path": str(ws / "out.pdf")}))
    assert rec["local_file"] == "out.pdf"


def test_local_file_null_for_a_link_outside_path_dotdot_or_folder(orun, tmp_path):
    outside = tmp_path / "outside.html"
    outside.write_text("x")
    (orun.project / "link.html").symlink_to(outside)
    (orun.project / "inner.html").write_text("x")
    (orun.project / "link2.html").symlink_to(orun.project / "inner.html")  # a link, even to an inside file
    (orun.project / "dir").mkdir()
    (orun.project / "outdir").symlink_to(tmp_path)  # a linked folder out of the project
    for value in (str(orun.project / "link.html"), "link.html", "link2.html", str(outside), "../outside.html",
                  str(orun.project / ".." / "outside.html"), "dir", str(orun.project / "dir"),
                  "outdir/outside.html", str(orun.project / "outdir" / "outside.html"), "/etc/hosts", "nope.md",
                  "a\x00b", "x" * 5000):
        orun.tdir.joinpath("spool.jsonl").unlink(missing_ok=True)
        orun.tdir.joinpath(co_hook.OUTPUTS_FILE).unlink(missing_ok=True)
        rec = one(orun, post("mcp__p__publish", "https://ex.example.com/a", {"path": value}))
        assert rec["local_file"] is None, value


# ---- caps -------------------------------------------------------------------------------------


def test_repeated_link_and_the_21st_are_not_spooled(orun):
    for i in range(3):
        orun(post("mcp__p__publish", "https://ex.example.com/same?v=%d" % i, tid=f"r{i}"), event="PostToolUse")
    assert len(outputs(orun)) == 1  # the same link (query removed) once
    for i in range(25):
        orun(post("mcp__p__publish", f"https://ex.example.com/n/{i}", tid=f"n{i}"), event="PostToolUse")
    recs = outputs(orun)
    assert len(recs) == co_hook.OUTPUTS_MAX == 20
    assert len({r["link"] for r in recs}) == 20


@pytest.mark.parametrize("content", ["{not json", '{"links": 5}', "[1]",
                                     '{"links": ["a"], "pad": "' + "x" * 70000 + '"}'])
def test_broken_or_big_state_file_counts_as_the_cap_reached(orun, content):
    orun.tdir.joinpath(co_hook.OUTPUTS_FILE).write_text(content)
    assert orun(post("mcp__p__publish", "https://ex.example.com/ok"), event="PostToolUse") is None
    assert outputs(orun) == []
    assert not [r for r in orun.spool() if r.get("event") == "guard_error"]


def test_fifo_state_file_never_blocks(orun):
    os.mkfifo(orun.tdir / co_hook.OUTPUTS_FILE)
    t0 = time.monotonic()
    orun(post("mcp__p__publish", "https://ex.example.com/ok"), event="PostToolUse", timeout=10)
    assert time.monotonic() - t0 < 5
    assert outputs(orun) == []


def test_linked_state_file_is_not_read(orun, tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text('{"links": []}')
    (orun.tdir / co_hook.OUTPUTS_FILE).symlink_to(target)
    orun(post("mcp__p__publish", "https://ex.example.com/ok"), event="PostToolUse")
    assert outputs(orun) == [] and target.read_text() == '{"links": []}'


def test_tmp_link_is_never_written_through(orun, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    (orun.tdir / (co_hook.OUTPUTS_FILE + ".tmp")).symlink_to(victim)
    rec = one(orun, post("mcp__p__publish", "https://ex.example.com/ok"))
    assert rec["link"] == "https://ex.example.com/ok"
    assert victim.read_text() == "keep me"
    state = orun.tdir / co_hook.OUTPUTS_FILE
    assert not state.is_symlink() and json.loads(state.read_text()) == {"links": ["https://ex.example.com/ok"]}


@pytest.mark.parametrize("link", [
    "https://hooks.slack.com/services/T000/B000/XXXXsecretXXXX",
    "https://hooks.slack.com/anything",
    "https://discord.com/api/webhooks/1234/abcdtoken",
    "https://canary.discord.com/api/webhooks/1234/abcdtoken",
    "https://discordapp.com/api/webhooks/1234/abcdtoken",
    "https://ex.example.com/hooks/webhooks/abc123",
    "https://ex.example.com/api/Webhook/abc/def",
    "https://ex.example.com/%77ebhooks/abc",
])
def test_webhook_links_dropped(orun, link):
    assert one(orun, post("mcp__p__create_webhook", f"Created: {link}")) is None


@pytest.mark.parametrize("link", ["https://docs.example.com/guides/webhooks", "https://docs.example.com/webhooks/",
                                  "https://discord.com/channels/1/2"])
def test_links_merely_about_webhooks_kept(orun, link):
    assert one(orun, post("mcp__p__publish", link))["link"] == link


def test_backslash_path_dropped_and_non_ascii_encoded(orun):
    assert one(orun, post("mcp__p__publish", "https://good.example.com/a\\b")) is None
    assert one(orun, post("mcp__p__publish", "https://good.example.com/caf\u00e9/\u2215evil"))["link"] == \
        "https://good.example.com/caf%C3%A9/%E2%88%95evil"


@pytest.mark.parametrize("link,ok", [
    ("https://.../a", False), ("https://intranet/a", False), ("https://a..b.com/x", False),
    ("http://[fe80::1%25eth0]/a", False), ("https://-bad.example.com/", False),
    ("http://localhost:3000/a", True), ("http://10.0.0.7/a", True), ("http://[2001:db8::1]/a", True),
    ("https://xn--bcher-kva.example/a", True),
])
def test_host_must_be_a_name_or_an_ip(orun, link, ok):
    rec = one(orun, post("mcp__p__publish", link))
    assert (rec is not None) is ok, link


def test_relative_local_file_uses_the_calls_cwd_inside_a_root(orun, tmp_path):
    (orun.project / "sub").mkdir()
    (orun.project / "sub" / "r.md").write_text("x")
    (orun.project / "r.md").write_text("x")
    p = post("Bash", {"stdout": "https://gist.github.com/u/abc"}, {"command": "cd sub && gh gist create r.md"})
    p["cwd"] = str(orun.project / "sub")
    assert one(orun, p)["local_file"] == "sub/r.md"
    # a cwd outside the roots is ignored: the workspace is used
    (orun.tdir / "spool.jsonl").unlink()
    (orun.tdir / co_hook.OUTPUTS_FILE).unlink()
    p["cwd"] = str(tmp_path)
    assert one(orun, p)["local_file"] == "r.md"


@pytest.mark.parametrize("resp", [None, 5, {"a": [1, 2, None]}, [{"type": "image"}]])
def test_odd_responses_never_fail(orun, resp):
    p = post("mcp__p__publish", "x")
    p["tool_response"] = resp
    assert orun(p, event="PostToolUse") is None
    assert outputs(orun) == []
    assert not [r for r in orun.spool() if r.get("event") == "guard_error"]


def test_done_line_still_written_first(orun):
    orun(post("mcp__p__publish", "https://ex.example.com/a"), event="PostToolUse")
    assert [r.get("done") or r.get("event") for r in orun.spool()] == [True, "Output"]


def test_hook_time_budget(orun):
    """PostToolUse of a publishing call stays well inside the hook's budget (no network, small files)."""
    (orun.project / "page.html").write_text("x")
    times = []
    for i in range(5):
        t0 = time.monotonic()
        orun(post("mcp__p__publish", f"https://ex.example.com/t/{i}", {"path": "page.html"}, tid=f"t{i}"),
             event="PostToolUse")
        times.append(time.monotonic() - t0)
    assert statistics.median(times) < 0.5, times
    assert os.path.exists(orun.tdir / co_hook.OUTPUTS_FILE)
    state = json.loads((orun.tdir / co_hook.OUTPUTS_FILE).read_text())
    assert len(state["links"]) == 5
