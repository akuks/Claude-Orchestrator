"""The brief as text for the run's system prompt (phase 4c)."""

from __future__ import annotations

BRIEF_FIRST_LINE = (
    "co-orchestrator brief for this task (set by whoever started it). co-guard blocks Edit, Write, MultiEdit and "
    "NotebookEdit outside CAN change and inside CANNOT change, and shell commands that write into a CANNOT "
    "change folder.")
REPORT_LINE = ("End your report with Outputs (each name with its value or path) and the evidence "
               "(commands run and their results).")
CAN_ANY = "anything not under CANNOT change"
CANNOT_NONE = "nothing listed"


def _line(text) -> str:
    return " ".join(str(text or "").split())


def _globs(items) -> str:
    return ", ".join(items or [])


def brief_text(brief: dict, bounds: list) -> str:
    """The brief's lines; optional ones only when set. The caller redacts it with the run's secrets."""
    lines = [BRIEF_FIRST_LINE,
             f"Goal: {_line(brief.get('goal'))}",
             f"CAN change: {_globs(brief.get('can_change')) or CAN_ANY}",
             f"CANNOT change: {_globs(brief.get('cannot_change')) or CANNOT_NONE}",
             f"Done when: {_line(brief.get('done'))}",
             f"Stop and report when: {_line(brief.get('stop'))}"]
    for label, key in (("Setup", "setup"), ("Log", "log")):
        if brief.get(key):
            lines.append(f"{label}: {_line(brief[key])}")
    if brief.get("retries") is not None:
        lines.append(f"Retries: {brief['retries']}")
    if brief.get("tie_break"):
        lines.append(f"Tie-break: {_line(brief['tie_break'])}")
    if brief.get("outputs"):
        lines.append(f"Outputs: {', '.join(brief['outputs'])}")
    for b in bounds or []:
        lines.append(f"Also bound by an enclosing brief (task {b.get('task_id')}): "
                     f"CAN change {_globs(b.get('can_change')) or CAN_ANY}; "
                     f"CANNOT change {_globs(b.get('cannot_change')) or CANNOT_NONE}")
    lines.append(REPORT_LINE)
    return "\n".join(lines)
