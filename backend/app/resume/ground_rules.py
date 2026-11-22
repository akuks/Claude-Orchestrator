"""Ground rules: the short rules every agent is given (Phase 3a, forge F21/F93/F98).

Org settings (``GuardOrgSettings.ground_rules``, admin-only to change):
``{"grounding": bool, "short_answers": bool, "usage_care": bool, "notes": bool, "questions": bool, "extra": str}``.

* ``main_text(cfg)`` goes to the main session's ``--append-system-prompt`` (the system prompt survives
  compaction, so it is never re-sent there).
* ``helper_text(cfg, external)`` is precompiled into the policy key ``helper_context`` and handed to every helper
  by the hook's SubagentStart answer.

The rule texts are forge's, adapted: "JSPARK Forge" is "co-orchestrator", and the code-map wording is dropped.
``tests/test_resume_ground_rules.py`` pins each one to the raw upstream file. The notes rule is the hook's
``NOTES_LINE`` (imported, never copied)."""

from __future__ import annotations

import json
import os

from ..guard.co_hook import DATA_LINE, NOTES_LINE
from ..redaction import clean_text

GROUND_EXTRA_MAX = 2000
SWITCHES = ("grounding", "short_answers", "usage_care", "notes", "questions")
DEFAULTS = {"grounding": True, "short_answers": True, "usage_care": True, "notes": True, "questions": True,
            "extra": ""}

GROUNDING = (
    "co-orchestrator grounding rule:\n"
    "1. When you say where something is in the code, cite path:line, from a file you read.\n"
    "2. If you cannot find it, say \"not found\". Never guess a path, name or line.\n"
    "3. Never describe code you have not read in this session. Read it first, or say you have not.\n"
    "4. Handoffs, summaries and memory are pointers, not proof. Re-read the file before relying on them.\n"
    "5. Specs, architecture and schema live in the repo. Read them there, not from memory."
)
SHORT_ANSWERS = (
    "co-orchestrator short-by-default rule: lead with the answer; use bullets or a table for lists; keep an "
    "explanation to one line unless asked for more; always state warnings, risks, decisions and blockers, one "
    "line each. Short never means leaving one of those out."
)
USAGE_CARE_MAIN = (
    "co-orchestrator usage care: give each helper one task with its own tests (several short helpers cost less "
    "than one long one) and ask for a short report; have the checker helper run a long test suite and report the "
    "totals and the failures, or send its output to a file and read only those lines."
)
USAGE_CARE_HELPER = (
    "co-orchestrator usage care (every step re-reads your whole conversation, so keep it small):\n"
    "1. While working, run only the tests of what you changed; run the whole suite once, at the end (and for a "
    "baseline, if your brief asks for one).\n"
    "2. Send long output to a file in the temp folder, not in the project, and read only the totals and the "
    "failing lines.\n"
    "3. Read the part of a file you need (a line range, or search first), not a large file whole.\n"
    "4. Report in short bullets: what changed, the evidence, what is left."
)
# the notes rule for the main session: the hook's nudge, asking for short notes instead of an update now
NOTES_RULE = NOTES_LINE.replace("update your notes now", "keep short notes")
# phase 3b Questions inbox: how the main session files a blocking question (Global Constraints, verbatim).
# Main session only: a helper reports to the main session, which decides what to ask.
QUESTIONS_RULE = (
    "If you need a decision from a person to go on, file it once and end your turn: curl -s -X POST "
    "\"$CO_API_BASE/tasks/$CO_TASK_ID/questions\" -H \"Authorization: Bearer $CO_API_TOKEN\" -H "
    "'Content-Type: application/json' -d '{\"question\": \"...\", \"options\": [\"...\"], "
    "\"recommendation\": \"...\", \"blocks\": \"what waits on it\", \"reversible\": true}'. "
    "Always give a recommendation."
)
# forge v0.7.0's PRECEDENCE_LINE (forge.js l.253-256), "JSPARK Forge" -> "co-orchestrator", nothing else changed.
# The main session gets it when the superpowers plugin is loaded (helpers do not, as in forge).
PRECEDENCE_LINE = (
    "co-orchestrator and superpowers, who decides what: superpowers sets the method (brainstorm, plan, test first, "
    "review, verify). co-orchestrator sets the guard rails (resume, block list, decisions, grounding). At a session "
    "start, do what co-orchestrator's lines say first (show where the work stands, check a cut-off session), then use "
    "superpowers' skills for the work. If the two still disagree: the user's own instruction, then co-orchestrator's "
    "block list and grounding rule, then superpowers."
)
PLUGIN_JSON_MAX = 65536


def _plugin_name(local_path) -> str | None:
    try:
        with open(os.path.join(str(local_path), ".claude-plugin", "plugin.json"), "rb") as fh:
            raw = fh.read(PLUGIN_JSON_MAX + 1)
        if len(raw) > PLUGIN_JSON_MAX:
            return None
        name = json.loads(raw).get("name")
        return name if isinstance(name, str) else None
    except Exception:  # noqa: BLE001 - an unreadable manifest is not superpowers
        return None


def has_superpowers(packs) -> bool:
    """A loaded skill pack is the superpowers plugin: by its name, or its ``.claude-plugin/plugin.json`` name."""
    for p in packs or []:
        if str(getattr(p, "name", None) or "").lower() == "superpowers":
            return True
        if getattr(p, "local_path", None) and _plugin_name(p.local_path) == "superpowers":
            return True
    return False


def normalized(stored) -> dict:
    """``DEFAULTS | stored`` where a stored value of the wrong type (or an unknown key) is ignored."""
    out = dict(DEFAULTS)
    if isinstance(stored, dict):
        for k in SWITCHES:
            if isinstance(stored.get(k), bool):
                out[k] = stored[k]
        if isinstance(stored.get("extra"), str):
            out["extra"] = stored["extra"][:GROUND_EXTRA_MAX]
    return out


def validate(value) -> dict:
    """A PUT body's ``ground_rules``: ValueError (the router's 400) for an unknown key, a non-bool switch or a long ``extra``. The result
    replaces the stored object: a key left out takes its default."""
    if not isinstance(value, dict):
        raise ValueError("ground_rules must be an object")
    unknown = sorted(str(k) for k in value if k not in DEFAULTS)
    if unknown:
        raise ValueError(f"unknown ground rule: {', '.join(unknown)}")
    for k in SWITCHES:
        if k in value and not isinstance(value[k], bool):
            raise ValueError(f"{k} must be true or false")
    out = dict(DEFAULTS) | value
    if not isinstance(out["extra"], str):
        raise ValueError("extra must be text")
    out["extra"] = clean_text(out["extra"]).strip()
    if len(out["extra"]) > GROUND_EXTRA_MAX:
        raise ValueError(f"extra is at most {GROUND_EXTRA_MAX} characters")
    return out


def included(cfg: dict) -> list[str]:
    """The names of the rules that are on (``extra`` when it holds text)."""
    cfg = normalized(cfg)
    return [k for k in SWITCHES if cfg[k]] + (["extra"] if cfg["extra"].strip() else [])


def main_text(cfg: dict) -> str:
    cfg = normalized(cfg)
    parts = [GROUNDING if cfg["grounding"] else None,
             SHORT_ANSWERS if cfg["short_answers"] else None,
             USAGE_CARE_MAIN if cfg["usage_care"] else None,
             NOTES_RULE if cfg["notes"] else None,
             QUESTIONS_RULE if cfg["questions"] else None,
             cfg["extra"].strip()]
    return "\n\n".join(p for p in parts if p)


def helper_text(cfg: dict, external: bool) -> str:
    cfg = normalized(cfg)
    parts = [DATA_LINE if external else None,
             GROUNDING if cfg["grounding"] else None,
             USAGE_CARE_HELPER if cfg["usage_care"] else None,
             cfg["extra"].strip()]
    return "\n\n".join(p for p in parts if p)
