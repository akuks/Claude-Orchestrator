"""Licence hints for projects (phase 6a, F59). A port of jspark-forge's classifier (MIT, same owner).

`classify` is pure and linear. `for_directory` reads one licence file from a folder's root without
trusting it: no symlinks, regular files only, bounded read. A hint, never legal advice.
"""
import asyncio
import hashlib
import logging
import os
import re
import stat
from datetime import datetime, timezone

log = logging.getLogger(__name__)

MAX_READ = 64 * 1024
TITLE_LEN = 400
WINDOW = 300
_FILE_PREFIXES = ("license", "licence", "copying")


def _lic(kind: str, id_: str) -> dict:
    return {"id": id_, "name": id_, "kind": kind}


def _P(n):
    return _lic("permissive", n)


def _C(n):
    return _lic("copyleft", n)


def _N(n):  # non-commercial: not free to use commercially, so "proprietary" here
    return _lic("proprietary", n)


_NONE = {"id": "none", "name": "No licence found", "kind": "none"}
_UNKNOWN = {"id": "unknown", "name": "Unrecognised licence", "kind": "unknown"}
_RESERVED = {"id": "All rights reserved", "name": "All rights reserved", "kind": "proprietary"}


def _ver(w: str, pattern: str, default: str) -> str:
    m = re.search(pattern, w, re.I)
    return m.group(1) if m else default


def _dotted(v: str) -> str:
    return v if "." in v else f"{v}.0"


# (regex, make(window), is_title)
_RULES = [
    (r"polyform\s+noncommercial", lambda w: _N("PolyForm-Noncommercial"), True),
    (r"business\s+source\s+license", lambda w: _N("BSL-1.1"), True),
    (r"attribution-noncommercial|cc\s*by-nc",
     lambda w: _N("CC-BY-NC-SA-4.0" if re.search("sharealike", w, re.I) else "CC-BY-NC-4.0"), True),
    (r"gnu\s+affero\s+general\s+public\s+license",
     lambda w: _C("AGPL-" + _dotted(_ver(w, r"version\s+(\d)", "3"))), True),
    (r"gnu\s+lesser\s+general\s+public\s+license|gnu\s+library\s+general\s+public\s+license",
     lambda w: _C("LGPL-" + _dotted(_ver(w, r"version\s+(\d(?:\.\d)?)", "3"))), True),
    (r"gnu\s+general\s+public\s+license",
     lambda w: _C("GPL-" + _dotted(_ver(w, r"version\s+(\d)", "3"))), True),
    (r"mozilla\s+public\s+license",
     lambda w: _C("MPL-" + _dotted(_ver(w, r"version\s+(\d(?:\.\d)?)", "2"))), True),
    (r"eclipse\s+public\s+license",
     lambda w: _C("EPL-2.0" if re.search(r"v(?:ersion)?\s*2\.0", w, re.I) else "EPL-1.0"), True),
    (r"apache\s+licen[cs]e",
     lambda w: _P("Apache-" + _dotted(_ver(w, r"version\s+(\d(?:\.\d)?)", "2"))), True),
    (r"\bmit\s+licen[cs]e", lambda w: _P("MIT"), True),
    (r"\bisc\s+licen[cs]e", lambda w: _P("ISC"), True),
    (r"\bbsd\s+([23])-clause", lambda w: _P("BSD-" + _ver(w, r"bsd\s+([23])-clause", "2") + "-Clause"), True),
    (r"permission\s+is\s+hereby\s+granted,?\s+free\s+of\s+charge", lambda w: _P("MIT"), False),
    (r"permission\s+to\s+use,\s+copy,\s+modify,?\s+(and/or\s+)?distribute\s+this\s+software\s+for\s+any\s+purpose"
     r"\s+with\s+or\s+without\s+fee", lambda w: _P("ISC"), False),
    (r"redistribution\s+and\s+use\s+in\s+source\s+and\s+binary\s+forms",
     lambda w: _P("BSD-3-Clause" if re.search(r"neither\s+the\s+name", w, re.I) else "BSD-2-Clause"), False),
    (r"free\s+and\s+unencumbered\s+software\s+released\s+into\s+the\s+public\s+domain",
     lambda w: _P("Unlicense"), False),
    (r"altered\s+source\s+versions\s+must\s+be\s+plainly\s+marked", lambda w: _P("zlib"), False),
]
_COMPILED = [(re.compile(p, re.I), mk, title) for p, mk, title in _RULES]
_DUAL = re.compile(r"dual[- ]licen[cs]|at your (option|choice)|either of|\bor\b[^.]{0,40}\bat your", re.I)
_RESERVED_RE = re.compile(r"all\s+rights\s+reserved", re.I)
_BEST = {"permissive": 0, "copyleft": 1, "proprietary": 2}


def _matches(t: str, only_titles: bool, limit: int | None = None) -> list:
    hay = t[:limit] if limit else t
    out = []
    for order, (rx, make, title) in enumerate(_COMPILED):
        if only_titles and not title:
            continue
        m = rx.search(hay)
        if m:
            size = 1500 if "redistribution" in rx.pattern else WINDOW
            out.append((m.start(), order, make(t[m.start():m.start() + size])))
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def classify(text: str) -> dict:
    """-> {id, name, kind}; kind is permissive|copyleft|proprietary|none|unknown. Input is capped at 64 KB."""
    t = re.sub(r"\s+", " ", str(text or "")[:MAX_READ]).strip()
    if not t:
        return dict(_UNKNOWN)
    if len(t) < 2500 and _DUAL.search(t):  # a choice is offered: the most permissive option counts
        seen: dict = {}
        for _, _, c in _matches(t, False):
            seen.setdefault(c["id"], c)
        allc = list(seen.values())
        if len(allc) > 1:
            best = min(allc, key=lambda c: _BEST[c["kind"]])
            name = " OR ".join(c["id"] for c in allc)
            return {"id": name, "name": name, "kind": best["kind"]}
    title = _matches(t, True, TITLE_LEN)
    if title:
        return dict(title[0][2])
    anyc = _matches(t, False)
    if anyc:
        return dict(anyc[0][2])
    if _RESERVED_RE.search(t):
        return dict(_RESERVED)
    return dict(_UNKNOWN)


def _read_sync(directory: str) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        with os.scandir(directory) as it:
            names = sorted(e.name for e in it if e.name.lower().startswith(_FILE_PREFIXES))
    except OSError:
        return None
    if not names:
        return {**_NONE, "file": None, "hash": None, "checked_at": now}
    name = names[0]
    fd = None
    try:
        fd = os.open(os.path.join(directory, name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        data = b""
        while len(data) < MAX_READ + 1:
            chunk = os.read(fd, MAX_READ + 1 - len(data))
            if not chunk:
                break
            data += chunk
        data = data[:MAX_READ]
    except OSError:
        return {**_UNKNOWN, "file": name, "hash": None, "checked_at": now}
    finally:
        if fd is not None:
            os.close(fd)
    text = data.decode("utf-8", "replace")
    return {**classify(text), "file": name, "hash": hashlib.sha256(data).hexdigest()[:16], "checked_at": now}


async def for_directory(directory: str) -> dict | None:
    """The licence of a folder's root file, or None when the folder cannot be read. Never raises."""
    try:
        return await asyncio.to_thread(_read_sync, directory)
    except Exception:  # noqa: BLE001 - a hint must never break the request
        log.debug("licence check failed for %s", directory, exc_info=True)
        return None


def badge(project) -> dict | None:
    """{label, tone} for the project card, or None. Own projects with a permissive/unknown licence show nothing."""
    lic = getattr(project, "licence", None)
    if not isinstance(lic, dict) or not lic.get("kind"):
        return None
    kind = lic["kind"]
    if getattr(project, "trust", "own") == "own" and kind in ("permissive", "unknown"):
        return None
    ident = str(lic.get("id") or "")[:60]
    if kind == "permissive":
        return {"label": ident or "Permissive licence", "tone": "info"}
    if kind == "copyleft":
        return {"label": f"{ident} — share-alike", "tone": "warning"}
    if kind == "proprietary":
        label = "All rights reserved" if ident == "All rights reserved" else f"{ident} — not free for commercial use"
        return {"label": label, "tone": "danger"}
    if kind == "none":
        return {"label": "No licence found", "tone": "warning"}
    return {"label": "Licence not recognised", "tone": "warning"}
