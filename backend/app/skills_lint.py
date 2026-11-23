"""Skill template and lint, ported from jspark-forge v0.7.0 (MIT) scripts/lib/lint.js lintSkill.

`lint(text)` returns [{line, message}], 1-based lines. HTML comments are blanked
with their line breaks kept, so line numbers stay true.
"""
import re

MAX_BYTES = 256 * 1024

TEMPLATE = """---
name: summarise-document
description: Use when asked to read a document at a URL or path and write a summary into docs/knowledge/.
---
<!--
Co-orchestrator skill template. Copy to skills/<name>/SKILL.md, then replace the example below. One task per skill.
Write in the imperative, second person. Lint the file in Skills before you use it.

- name: the folder name of the skill, lower case with hyphens.
- description: one line that states the trigger, and must start with "Use when".
- Numbered parts: each part names its Input, its Action and its Output (a name or a path).
- A fetched or generated artifact: skip its part if the output already exists.
- Output names never overwrite: check that the name is free, and if it is taken, stop and say so.
- Last part: "Verify:" with "Evidence:", the check that ran and the output that proves it.
-->
1. Input: the URL or path. Action: normalise it to a cache key. Output: the key.
2. Input: the key. Action: if `.cache/<key>/` exists, skip; else fetch and unpack there. Output: `.cache/<key>/`.
3. Input: the cache folder. Action: find the entry file; read it and the files it includes. Output: notes (not written).
4. Input: the notes. Action: write `docs/knowledge/summary-<tag>.md`; if it exists, stop and say so. Output: the path.
5. Verify: the file exists, is under 600 words, and quotes one source line with its location.
   Evidence: the word count and the quoted line with its path.
"""

_FIELD = re.compile(r"^([A-Za-z-]+):\s*(.*)$")
_USE_WHEN = re.compile(r"^Use when\b", re.ASCII)
_PART = re.compile(r"^\d+\.\s", re.ASCII)


def _strip_comments(text: str) -> str:
    """Blank HTML comments but keep their line breaks. One linear pass; an
    unterminated comment runs to the end of the input."""
    out: list[str] = []
    pos = 0
    while True:
        a = text.find("<!--", pos)
        if a < 0:
            out.append(text[pos:])
            break
        out.append(text[pos:a])
        b = text.find("-->", a + 4)
        end = len(text) if b < 0 else b + 3
        out.append("\n" * text.count("\n", a, end))
        pos = end
    return "".join(out)


def _lines(text: str) -> list[str]:
    stripped = _strip_comments(text)
    return stripped.replace("\r", "").split("\n")


def _has(label: str, text: str) -> bool:
    return re.search(r"\b" + re.escape(label), text, re.ASCII) is not None


def lint(text: str) -> list[dict]:
    errs: list[dict] = []

    def err(line: int, message: str) -> None:
        errs.append({"line": line, "message": message})

    lines = _lines(text)
    body_start = 0
    if lines[0].strip() != "---":
        err(1, "frontmatter missing (the file must start with ---, then name and description)")
    else:
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), -1)
        if end < 0:
            err(1, "frontmatter is not closed with ---")
        else:
            body_start = end + 1
            fm: dict[str, tuple[str, int]] = {}
            for i, ln in enumerate(lines[1:end]):
                m = _FIELD.match(ln)
                if m:
                    fm[m.group(1)] = (m.group(2).strip(), i + 2)
            name = fm.get("name")
            if not name or not name[0]:
                err(name[1] if name else 1, "frontmatter field name is missing or empty")
            desc = fm.get("description")
            if not desc or not desc[0]:
                err(desc[1] if desc else 1, "frontmatter field description is missing or empty")
            elif not _USE_WHEN.match(desc[0]):
                err(desc[1], 'description must start with "Use when" (it states the trigger)')

    starts = [i for i in range(body_start, len(lines)) if _PART.match(lines[i])]
    parts = [
        (s + 1, "\n".join(lines[s : starts[k + 1] if k + 1 < len(starts) else len(lines)]))
        for k, s in enumerate(starts)
    ]
    if any(_has("Input:", t) for _, t in parts):
        last = len(parts) - 1
        for k, (line, t) in enumerate(parts):
            missing = [f for f in ("Input:", "Action:", "Output:") if not _has(f, t)]
            if k == last:
                if not _has("Verify:", t):
                    err(line, "the last part must be the Verify: part, with Evidence:")
                elif not _has("Evidence:", t):
                    err(line, "the Verify: part is missing Evidence: (what check ran and what output proves it)")
            elif _has("Verify:", t) and missing:
                err(line, "Verify: must be the last part")
            else:
                for f in missing:
                    err(line, f"part is missing {f}")
    return errs
