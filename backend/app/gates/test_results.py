"""Read the text a test command printed and say how many tests passed, failed
or were skipped, and which ones failed. Also report how far a run still being
written has got. Pure functions: no files, no processes.

Ported from the forge ``scripts/lib/gate-results.js`` (v0.7.0, MIT).

One pass over the lines collects what every runner would print (its summary
lines, one line per test, failed names). The format is then chosen once: a
runner whose final summary is present wins (the last one in the text); with no
summary at all, the runner with the most per-test lines wins. Counts come from
the summary when there is one and from per-test lines only when the run was cut
off. Only the last MAX_INPUT characters are read, every line is cut to
MAX_LINE characters, and every pattern is anchored and compiled once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_INPUT = 20 * 1024 * 1024
MAX_LINE = 4000
MAX_NAMES = 50
MAX_NAME = 160

_A = re.ASCII
FORMATS = ("playwright", "pytest", "go", "tap", "node-test", "jest", "vitest", "mocha")
_KEY = {"playwright": "pw", "pytest": "py", "go": "go", "tap": "tap",
        "node-test": "node", "jest": "jest", "vitest": "vt", "mocha": "mocha"}

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_NODE_TAIL = re.compile(r" \(\d+(?:\.\d+)?ms\)(?: #.*)?", _A)
_NODE_DUR = re.compile(r"\(\d+(?:\.\d+)?ms\)", _A)
_JEST_TAIL = re.compile(r" \(\d+ ms\)", _A)
_VITEST_TAIL = re.compile(r" \d+(?:\.\d+)?ms", _A)
_PW_TAIL = re.compile(r" \(\d+(?:\.\d+)?(?:ms|s|m)\)", _A)
_PW_COUNT = re.compile(r"(\d+) (failed|flaky|skipped|did not run|passed|interrupted)(?: \(.*\))?", _A)
_MOCHA_COUNT = re.compile(r"(\d+) (passing|failing|pending)(?: \(.*\))?", _A)
_PY_SUM = re.compile(r"(?:=+ )?(\d+ \w+(?:, \d+ \w+)*) in [\d.:]+s?(?: \([\d:]+\))?(?: =+)?", _A)
_GO_PKG = re.compile(r"(ok|FAIL)\s+\S+\s+(?:\d+(?:\.\d+)?s|\(cached\)|\[.*\])", _A)
_TAP_PLAN = re.compile(r"1\.\.(\d+)(?:\s|$)", _A)
_NUMBERED = re.compile(r"\d+\) (.*)", _A)
_NODE_SUM = re.compile(r"ℹ (tests|pass|fail|skipped|cancelled|todo) (\d+)", _A)
_TAP_SUM = re.compile(r"# (tests|pass|fail|skipped|skip|cancelled|todo) (\d+)", _A)
_GO_RES = re.compile(r"--- (PASS|FAIL|SKIP): (\S+)", _A)
_TAP_OK = re.compile(r"ok (\d+)(.*)", _A)
_TAP_NOT = re.compile(r"not ok (\d+)(.*)", _A)
_VT_SUM = re.compile(r"Tests +(\d.*)", _A)
_PW_RUN = re.compile(r"Running (\d+) tests? using", _A)
_PY_COLL = re.compile(r"collected (\d+) items?", _A)
_FAIL_FILE = re.compile(r"FAIL +(\S.*)", _A)
_VT_FAIL = re.compile(r"FAIL {2}(.+)", _A)
_PY_PROG = re.compile(r"(\S+\.py) +([.FEsxX]+)(?: +\[ *\d+%\])?", _A)
_PY_DOTS = re.compile(r"([.FEsxX]+) +\[ *\d+%\]", _A)  # pytest -q: no file name
_PY_V_STATUS = {"PASSED": 0, "XPASS": 0, "FAILED": 1, "ERROR": 1, "SKIPPED": 2, "XFAIL": 2}
_PCT_TAIL = re.compile(r"\[ *\d+%\]", _A)
_PW_LINE = re.compile(r"(✓|✘|ok|x|-)\s+\d+\s+(.*)", _A)
_COUNT_PART = re.compile(r"(\d+) ([a-z ]+)", _A)
_TOTAL_TAIL = re.compile(r"\(\d+\)", _A)
_VITEST_FILE = re.compile(r"\(\d+ tests?\b", _A)
_WS = re.compile(r"\s+", _A)


@dataclass(frozen=True)
class TestResult:
    __test__ = False  # not a pytest class

    runner: str | None
    passed: int | None
    failed: int | None
    skipped: int | None
    total: int | None
    failing: tuple[str, ...]


_UNKNOWN = TestResult(None, None, None, None, None, ())


class _Bag:
    __slots__ = ("list", "seen")

    def __init__(self) -> None:
        self.list: list[str] = []
        self.seen: set[str] = set()


def _add(b: _Bag, raw: str) -> None:
    if len(b.list) >= MAX_NAMES:
        return
    n = _WS.sub(" ", str(raw)).strip()
    if not n:
        return
    if len(n) > MAX_NAME:
        n = n[: MAX_NAME - 1] + "…"
    if n in b.seen:
        return
    b.seen.add(n)
    b.list.append(n)


def _cut_tail(s: str, rx: re.Pattern, frm: str) -> str:
    i = s.rfind(frm)
    return s[:i] if i >= 0 and rx.fullmatch(s[i:]) else s


def _drop_total(s: str) -> str:
    t = s.rstrip()
    i = t.rfind("(")
    return t[:i].rstrip() if i >= 0 and _TOTAL_TAIL.fullmatch(t[i:]) else t


def _counts(s: str, sep: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in s.split(sep):
        m = _COUNT_PART.fullmatch(part.strip())
        if m:
            out[m.group(2)] = out.get(m.group(2), 0) + int(m.group(1))
    return out


def _before_dash(s: str) -> str:
    i = s.find(" - ")
    return s if i < 0 else s[:i]


class _State:
    def __init__(self) -> None:
        self.sum: dict[str, dict] = {}
        self.per = {_KEY[f]: [0, 0, 0] for f in FORMATS}  # passed, failed, skipped
        self.names = {_KEY[f]: (_Bag(), _Bag()) for f in FORMATS}
        self.head: dict[str, int] = {}
        self.node_block = False
        self.mocha_failing = False
        self.mocha_hold: str | None = None
        self.pw_running = False
        self.pw_in_failed = False
        self.jest_file = ""
        self.go_pkg = 0
        self.go_lone = False
        self.go_at = -1


def _flush_mocha(S: _State, nxt: str | None) -> None:
    h = S.mocha_hold
    if h is None:
        return
    S.mocha_hold = None
    if nxt is not None and nxt.endswith(":"):
        _add(S.names["mocha"][0], h + " " + nxt[:-1])
    else:
        _add(S.names["mocha"][0], h)


def _pw_line(S: _State, t: str, kind: str) -> bool:
    m = _PW_LINE.fullmatch(t)
    if not m or "›" not in t:
        return False
    sym = m.group(1)
    if sym in ("ok", "x") and not S.pw_running:
        return False
    if "(retry #" in t:
        return True
    per = S.per["pw"]
    if sym in ("✓", "ok"):
        per[0] += 1
    elif sym in ("✘", "x"):
        per[1] += 1
        _add(S.names["pw"][1], _cut_tail(m.group(2), _PW_TAIL, " ("))
    else:
        per[2] += 1
    return True


def _tap_line(S: _State, ok: bool, rest: str) -> None:
    r = rest[3:] if rest.startswith(" - ") else rest.strip()
    d = ""
    h = r.rfind(" # ")
    if h >= 0:
        d = r[h + 3:].lower()
        r = r[:h]
    per = S.per["tap"]
    if d.startswith("skip") or d.startswith("todo"):
        per[2] += 1
    elif ok:
        per[0] += 1
    else:
        per[1] += 1
        _add(S.names["tap"][0], r)


def _scan(raw: str) -> _State | None:
    if not isinstance(raw, str) or raw == "":
        return None
    text = raw[-MAX_INPUT:] if len(raw) > MAX_INPUT else raw
    if "\x1b" in text:
        text = _ANSI.sub("", text).replace("\x1b", "")
    if "\r" in text:
        text = text.replace("\r", "")
    S = _State()
    # drop short lines up front (cheap): the shortest line any runner prints is 4 characters
    for i, line in enumerate([ln for ln in text.split("\n") if len(ln) > 3]):
        if len(line) > MAX_LINE:
            line = line[:MAX_LINE]
        lead = line.lstrip(" \t")
        k = len(line) - len(lead)
        t = lead.rstrip()
        if len(t) < 4:  # the shortest line any runner prints is 4 characters ("PASS", "ok 1")
            continue
        c = ord(t[0])
        if S.mocha_hold is not None:
            _flush_mocha(S, t)
        digit = 48 <= c <= 57

        if digit and k >= 1:
            m = _PW_COUNT.fullmatch(t)
            if m:
                w = m.group(2)
                p = S.sum.setdefault("pw", {})
                p["at"] = i
                p[w] = p.get(w, 0) + int(m.group(1))
                S.pw_in_failed = w in ("failed", "interrupted")
                continue
        if S.pw_in_failed:
            if k >= 3:
                _add(S.names["pw"][0], t)
                continue
            S.pw_in_failed = False

        if digit:
            if k == 0 and t.startswith("1.."):
                m = _TAP_PLAN.match(t)
                if m:
                    S.head["tap"] = int(m.group(1))
                    continue
            if k >= 1:
                m = _MOCHA_COUNT.fullmatch(t)
                if m:
                    s = S.sum.setdefault("mocha", {})
                    s["at"] = i
                    s[m.group(2)] = s.get(m.group(2), 0) + int(m.group(1))
                    if m.group(2) == "failing":
                        S.mocha_failing = True
                    continue
            if k == 2 and S.mocha_failing:
                m = _NUMBERED.fullmatch(t)
                if m:
                    S.mocha_hold = m.group(1)
                    continue
            if k >= 4:
                m = _NUMBERED.fullmatch(t)
                if m:
                    S.per["mocha"][1] += 1
                    _add(S.names["mocha"][1], m.group(1))
                    continue
        if k == 0 and (c == 61 or digit) and " in " in t:
            m = _PY_SUM.fullmatch(t)
            if m:
                S.sum["py"] = {"at": i, "c": _counts(m.group(1), ", ")}
                continue

        if digit and k == 0 and "]" not in t and ".py " not in t and "::" not in t:
            continue  # nothing below can match a plain numeric line (fast path)

        if c == 0x2139:  # ℹ node-test summary
            m = _NODE_SUM.fullmatch(t)
            if m:
                s = S.sum.setdefault("node", {})
                s["at"] = i
                s[m.group(1)] = int(m.group(2))
        elif c in (0x2714, 0x2716, 0xFE63):  # ✔ ✖ ﹣
            if len(t) > 1 and t[1] == " ":
                rest = t[2:]
                if c == 0x2716 and rest.startswith("failing tests:"):
                    S.node_block = True
                else:
                    dur = _NODE_DUR.search(rest) is not None
                    if c == 0x2716:
                        if S.node_block:
                            _add(S.names["node"][0], _cut_tail(rest, _NODE_TAIL, " ("))
                        elif dur:
                            S.per["node"][1] += 1
                            _add(S.names["node"][1], _cut_tail(rest, _NODE_TAIL, " ("))
                    elif dur:
                        if not S.node_block:
                            S.per["node"][0 if c == 0x2714 else 2] += 1
                    elif c == 0x2714 and k >= 2:
                        S.per["mocha"][0] += 1
        elif c in (0x2713, 0x2718):  # ✓ ✘
            if k >= 1 and _pw_line(S, t, "p" if c == 0x2713 else "f"):
                pass
            elif c == 0x2718:
                pass
            elif len(t) > 1 and t[1] == " ":
                rest = t[2:]
                if _cut_tail(rest, _JEST_TAIL, " (") != rest:
                    S.per["jest"][0] += 1
                elif _VITEST_FILE.search(rest):
                    pass  # a vitest file line, not a test
                elif _cut_tail(rest, _VITEST_TAIL, " ") != rest:
                    S.per["vt"][0] += 1
                elif k >= 2:
                    S.per["mocha"][0] += 1
        elif c == 0x2715:  # ✕ jest fail
            if len(t) > 1 and t[1] == " ":
                S.per["jest"][1] += 1
                _add(S.names["jest"][1], _cut_tail(t[2:], _JEST_TAIL, " ("))
        elif c == 0x25CB:  # ○ jest skipped
            if len(t) > 1 and t[1] == " ":
                S.per["jest"][2] += 1
        elif c == 0xD7:  # × vitest fail
            if k >= 1 and len(t) > 1 and t[1] == " ":
                S.per["vt"][1] += 1
                _add(S.names["vt"][1], _cut_tail(t[2:], _VITEST_TAIL, " "))
        elif c == 0x2193:  # ↓ vitest skipped
            if k >= 1 and len(t) > 1 and t[1] == " ":
                S.per["vt"][2] += 1
        elif c == 0x25CF:  # ● jest failed name
            if k == 2 and len(t) > 1 and t[1] == " ":
                name = t[2:]
                if not name.startswith("Console"):
                    if name.endswith("Test suite failed to run"):
                        _add(S.names["jest"][0],
                             S.jest_file + " (test suite failed to run)" if S.jest_file
                             else "Test suite failed to run")
                    else:
                        _add(S.names["jest"][0], name)
        elif c == 45:  # -
            m = _GO_RES.match(t) if t.startswith("--- ") else None
            if m:
                if m.group(1) == "PASS":
                    S.per["go"][0] += 1
                elif m.group(1) == "SKIP":
                    S.per["go"][2] += 1
                else:
                    S.per["go"][1] += 1
                    _add(S.names["go"][0], m.group(2))
            elif k >= 1 and _pw_line(S, t, "s"):
                pass
            elif k >= 4 and len(t) > 1 and t[1] == " ":
                S.per["mocha"][2] += 1
        elif c == 111:  # o
            if k == 0:
                m = _TAP_OK.match(t)
                if m:
                    _tap_line(S, True, m.group(2))
                elif _GO_PKG.fullmatch(t):
                    S.go_pkg += 1
                    S.go_at = i
            else:
                _pw_line(S, t, "p")
        elif c == 120:  # x playwright
            if k >= 1:
                _pw_line(S, t, "f")
        elif c == 110:  # n
            if k == 0:
                m = _TAP_NOT.match(t)
                if m:
                    _tap_line(S, False, m.group(2))
        elif c == 35:  # #
            if k == 0:
                m = _TAP_SUM.fullmatch(t)
                if m:
                    s = S.sum.setdefault("tap", {})
                    s["at"] = i
                    s["skipped" if m.group(1) == "skip" else m.group(1)] = int(m.group(2))
        elif c == 84:  # T
            if k == 0 and t.startswith("Tests:"):
                S.sum["jest"] = {"at": i, "c": _counts(t[6:], ",")}
            else:
                m = _VT_SUM.fullmatch(t)
                if m:
                    S.sum["vt"] = {"at": i, "c": _counts(_drop_total(m.group(1)), "|")}
        elif c == 82:  # R
            if k == 0:
                m = _PW_RUN.match(t)
                if m:
                    S.head["pw"] = int(m.group(1))
                    S.pw_running = True
        elif c == 99:  # c
            if k == 0:
                m = _PY_COLL.match(t)
                if m:
                    S.head["py"] = int(m.group(1))
        elif c == 80:  # P
            if k == 0 and t == "PASS":
                S.go_lone = True
                S.go_at = i
        elif c == 70:  # F
            if k == 0 and t.startswith("FAILED "):
                _add(S.names["py"][0], _before_dash(t[7:]))
            elif k == 0 and t == "FAIL":
                S.go_lone = True
                S.go_at = i
            elif k == 0 and _GO_PKG.fullmatch(t):
                S.go_pkg += 1
                S.go_at = i
            elif k == 0 and (m := _FAIL_FILE.fullmatch(t)):
                S.jest_file = m.group(1).strip()
            elif k >= 1 and (m := _VT_FAIL.fullmatch(t)):
                _add(S.names["vt"][0], m.group(1))
        elif c == 69:  # E
            if k == 0 and t.startswith("ERROR "):
                _add(S.names["py"][0], _before_dash(t[6:]))

        # pytest progress lines: "tests/test_a.py ..F.s    [ 45%]"
        if k == 0 and (".py " in t or t[-1] == "]"):
            m = _PY_PROG.fullmatch(t) or _PY_DOTS.fullmatch(t)
            if m:
                for ch in m.group(m.lastindex):
                    if ch in ".X":
                        S.per["py"][0] += 1
                    elif ch in "FE":
                        S.per["py"][1] += 1
                    else:
                        S.per["py"][2] += 1
        if k == 0 and "::" in t:  # pytest -v: "tests/a.py::test_x PASSED  [ 10%]"
            u = t
            if u.endswith("%]"):
                j = u.rfind("[")
                if j > 0 and _PCT_TAIL.fullmatch(u[j:]):
                    u = u[:j].rstrip()
            for w in (" SKIPPED (", " XFAIL ("):  # pytest -v prints the reason after the word
                j = u.find(w)
                if j > 0:
                    u = u[:j] + w[:-2]
                    break
            head, sep, word = u.rpartition(" ")
            if sep and word in _PY_V_STATUS and "::" in head:
                idx = _PY_V_STATUS[word]
                S.per["py"][idx] += 1
                if idx == 1:
                    _add(S.names["py"][1], head)
    if S.mocha_hold is not None:
        _flush_mocha(S, None)
    return S


def _n(d: dict, key: str) -> int:
    return d.get(key, 0)


def _summary_of(S: _State, f: str) -> dict | None:
    s = S.sum
    if f in ("node-test", "tap"):
        x = s.get("tap" if f == "tap" else "node")
        if not x or "tests" not in x:
            return None
        return {"at": x["at"], "passed": _n(x, "pass"),
                "failed": _n(x, "fail") + _n(x, "cancelled"),
                "skipped": _n(x, "skipped") + _n(x, "todo"), "notRun": 0}
    if f in ("jest", "vitest"):
        x = s.get("jest" if f == "jest" else "vt")
        if not x:
            return None
        c = x["c"]
        return {"at": x["at"], "passed": _n(c, "passed"), "failed": _n(c, "failed"),
                "skipped": _n(c, "skipped") + _n(c, "todo"), "notRun": 0}
    if f == "pytest":
        x = s.get("py")
        if not x:
            return None
        c = x["c"]
        return {"at": x["at"], "passed": _n(c, "passed") + _n(c, "xpassed"),
                "failed": _n(c, "failed") + _n(c, "error") + _n(c, "errors"),
                "skipped": _n(c, "skipped") + _n(c, "xfailed"), "notRun": 0}
    if f == "playwright":
        x = s.get("pw")
        if not x:
            return None
        return {"at": x["at"], "passed": _n(x, "passed"),
                "failed": _n(x, "failed") + _n(x, "interrupted"),
                "skipped": _n(x, "skipped"), "notRun": _n(x, "did not run")}
    if f == "mocha":
        x = s.get("mocha")
        if not x:
            return None
        return {"at": x["at"], "passed": _n(x, "passing"), "failed": _n(x, "failing"),
                "skipped": _n(x, "pending"), "notRun": 0}
    if f == "go":
        per = S.per["go"]
        if not (S.go_pkg > 0 or (S.go_lone and sum(per) > 0)):
            return None
        return {"at": S.go_at, "passed": per[0], "failed": per[1], "skipped": per[2], "notRun": 0}
    return None


def _has_head(S: _State, f: str) -> bool:
    return ((f == "playwright" and "pw" in S.head) or (f == "pytest" and "py" in S.head)
            or (f == "tap" and "tap" in S.head))


def _choose(S: _State) -> str | None:
    best = None
    best_at = -1
    for f in FORMATS:
        sm = _summary_of(S, f)
        if sm and sm["at"] >= best_at:
            best, best_at = f, sm["at"]
    if best:
        return best
    score = 0
    for f in FORMATS:
        sc = sum(S.per[_KEY[f]]) + (1 if _has_head(S, f) else 0)
        if sc > score:
            best, score = f, sc
    return best


def parse(text: str) -> TestResult:
    """Final counts and failing names, or an all-None result. Never raises."""
    try:
        S = _scan(text)
        if S is None:
            return _UNKNOWN
        f = _choose(S)
        if f is None:
            return _UNKNOWN
        sm = _summary_of(S, f)
        per = S.per[_KEY[f]]
        if f == "go" and sum(per) == 0:
            # only package lines ("ok  pkg 0.5s"): no per-test information
            return TestResult("go", None, None, None, None, ())
        if sm:
            p, fl, sk, nr = sm["passed"], sm["failed"], sm["skipped"], sm["notRun"]
        else:
            p, fl, sk, nr = per[0], per[1], per[2], 0
        a, b = S.names[_KEY[f]]
        names = (a.list if a.list else b.list)[:MAX_NAMES]
        return TestResult(f, p, fl, sk, p + fl + sk + nr, tuple(names))
    except Exception:  # noqa: BLE001 - the parser must never raise
        return _UNKNOWN


def progress(text: str) -> tuple[int, int | None, int] | None:
    """(done, total_or_None, failed) for a run still being written, or None."""
    try:
        S = _scan(text)
        if S is None:
            return None
        f = _choose(S)
        if f is None:
            return None
        per = S.per[_KEY[f]]
        sm = _summary_of(S, f)
        if f == "go" and sum(per) == 0:
            return None
        done = sum(per)
        failed = per[1]
        total: int | None = None
        if f == "playwright" and "pw" in S.head:
            total = S.head["pw"]
        elif f == "pytest" and "py" in S.head:
            total = S.head["py"]
        elif f == "tap" and "tap" in S.head:
            total = S.head["tap"]
        if sm:  # a finished run: the summary is the truth
            done = sm["passed"] + sm["failed"] + sm["skipped"]
            failed = sm["failed"]
        if total is None and sm:
            total = sm["passed"] + sm["failed"] + sm["skipped"] + sm["notRun"]
        return (done, total, failed)
    except Exception:  # noqa: BLE001
        return None


def progress_line(p: tuple[int, int | None, int]) -> str:
    done, total, failed = p
    head = f"{done} tests done" if total is None else f"{done} of {total} tests done"
    return f"{head}, {failed} failed"
