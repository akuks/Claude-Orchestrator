"""The rules agents work under, as one plain-text file (phase 6a, forge F41).

``render(s, project, now)`` gives markdown for use outside the orchestrator: the ground rules that are on, the block
list in words, the org's and the project's custom rules, and (own-trust projects only) the active decisions.
Everything passes through ``clean_text`` and the project's env values are masked; no env value is ever written."""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import func, select

from ..models import ProjectDecision
from ..redaction import clean_text, env_values, mask_values
from ..resume import ground_rules
from . import policy_store
from . import rules as guard_rules

DECISIONS_MAX = 100
CUSTOM_MAX = 500
GUARD_OFF = "The guard is switched off for this project, so the built-in rules are not enforced here."
HEADER = ("Rules for agents working on {project} — generated {date}. For use outside the orchestrator; "
          "inside it these are enforced, not just written.")
ORG_NAME = "this organisation"


def _line(text) -> str:
    """One clean line (no hidden characters, no line breaks)."""
    return " ".join(clean_text(text).split())


def _safe_block(text: str) -> str:
    """Multi-line text, cleaned line by line; a leading # is neutralised so it cannot fake a heading."""
    lines = []
    for ln in clean_text(text).splitlines():
        ln = ln.rstrip()
        lines.append("\\" + ln if ln.lstrip().startswith("#") else ln)
    return "\n".join(lines).strip()


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _custom_line(rule: dict) -> str:
    never = rule.get("kind") == "never"
    how = "commands matching" if rule.get("match") == "regex" else "commands containing"
    pat = _line(rule.get("pattern")).replace("`", "'")
    reason = _line(rule.get("reason"))
    return f"- {'Never' if never else 'Needs a person\'s OK'}: {how} `{pat}`" + (f" — {reason}" if reason else "")


async def render(s, project, now: datetime) -> str:
    org = await policy_store.org_settings(s)
    disabled = set(org["disabled_rules"]) & guard_rules.DISABLEABLE
    name = _line(project.name) if project is not None else ORG_NAME
    out = [HEADER.format(project=name, date=now.date().isoformat()), ""]

    out += ["## Ground rules", ""]
    text = _safe_block(ground_rules.main_text(org["ground_rules"]))
    out += [text, ""] if text else ["None are switched on.", ""]

    if project is not None and not project.guard_enabled:
        out += ["## Built-in rules", "", GUARD_OFF, ""]
    else:
        always = [_sentence(hard) for rid, (_, hard) in guard_rules.RULES.items() if hard]
        confirm = [_sentence(reason) for rid, (reason, _) in guard_rules.RULES.items()
                   if reason and rid not in disabled]
        out += ["## Always blocked", ""] + [f"- {t}" for t in always] + [""]
        out += ["## Needs a person's OK", ""] + [f"- {t}" for t in confirm] + [""]

    pid = project.id if project is not None else None
    custom = await policy_store.rules_for(s, pid)
    out += ["## " + ("This project's own rules" if project is not None else "The organisation's own rules"), ""]
    out += [_custom_line(r) for r in custom[:CUSTOM_MAX]] if custom else ["None."]
    if len(custom) > CUSTOM_MAX:
        out.append(f"(and {len(custom) - CUSTOM_MAX} more not shown)")
    out += [""]

    if project is not None and project.trust == "own" and getattr(project, "directory", None):
        repo = await asyncio.to_thread(policy_store.read_repo_rules, project.directory)
        found = list(repo.get("never") or []) + list(repo.get("confirm") or [])
        if found:
            out += ["## From the project's rules file", ""]
            out += [_custom_line(r) for r in found[:CUSTOM_MAX]]
            if len(found) > CUSTOM_MAX:
                out.append(f"(and {len(found) - CUSTOM_MAX} more not shown)")
            out += [""]

    if project is not None and project.trust == "own":
        rows = (await s.execute(
            select(ProjectDecision).where(ProjectDecision.project_id == project.id,
                                          ProjectDecision.status == "active")
            .order_by(ProjectDecision.number).limit(DECISIONS_MAX + 1))).scalars().all()
        more = len(rows) > DECISIONS_MAX
        rows = rows[:DECISIONS_MAX]
        out += ["## Decisions", ""]
        for d in rows:
            why = _line(d.why)
            out.append(f"- D-{d.number:03d} {_line(d.title)}" + (f" — {why}" if why else ""))
        if more:
            total = (await s.execute(select(func.count()).select_from(ProjectDecision).where(
                ProjectDecision.project_id == project.id, ProjectDecision.status == "active"))).scalar_one()
            out.append(f"(and {total - DECISIONS_MAX} more not shown)")
        if not rows:
            out.append("None.")
        out += [""]

    body = "\n".join(out).rstrip() + "\n"
    if project is not None:
        body = mask_values(body, env_values([project.env]))
    return body
