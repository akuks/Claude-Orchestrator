"""Stage kinds and save-time validation for gate and approval stages."""

import re

from sqlalchemy import select

from ..models import Agent, Project
from ..redaction import clean_text
from . import preflight

KINDS = ("agent", "gate", "approval")
_BAD = {";": ";", "&": "&", "|": "|", ">": ">", "<": "<", "`": "a backtick", "$(": "$("}


def stage_kind(stage) -> str:
    k = stage.get("kind") if isinstance(stage, dict) else None
    return k if k in KINDS else "agent"


def check_command(cmd: str) -> str:
    if not isinstance(cmd, str):
        raise ValueError("a command must be text")
    out = clean_text(cmd).strip()
    if not out:
        raise ValueError("a command cannot be empty")
    if len(out) > 500:
        raise ValueError("a command can be at most 500 characters")
    for ch in out:
        if ch == "\n":
            raise ValueError("a command cannot contain a newline")
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise ValueError(f"a command cannot contain the control character U+{ord(ch):04X}")
    for tok, label in _BAD.items():
        if tok in out:
            raise ValueError(f"a command cannot contain {label}")
    return out


_OUTPUT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _outputs(item: dict, name: str) -> list[str] | None:
    outs = item.get("outputs")
    if outs is None or outs == []:
        return None
    if not isinstance(outs, list) or len(outs) > 10:
        raise ValueError(f"Step '{name}': an item declares at most 10 outputs")
    seen = set()
    for o in outs:
        if not isinstance(o, str) or not _OUTPUT_NAME.match(o):
            raise ValueError(
                f"Step '{name}': output name '{clean_text(str(o))[:40]}' must be 1 to 40 characters: a lowercase letter, "
                "then lowercase letters, digits or _")
        if o in seen:
            raise ValueError(f"Step '{name}': output name '{o}' is repeated")
        seen.add(o)
    return list(outs)


async def _fix(s, st: dict, name: str) -> dict | None:
    fix = st.get("fix")
    if fix is None:
        return None
    if not isinstance(fix, dict):
        raise ValueError(f"Step '{name}': fix must be an agent and a number of rounds")
    ref = fix.get("agent")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError(f"Step '{name}': fix needs an agent")
    rounds = fix.get("rounds", 1)
    if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= 3:
        raise ValueError(f"Step '{name}': fix rounds must be a whole number from 1 to 3")
    # An id, or a name looked up inside the gate's project (names are not unique across
    # projects). The resolved agent ID is stored, so a later same-named agent elsewhere
    # cannot change or break the gate.
    pid = st.get("project")
    agent = await s.get(Agent, ref)
    if agent is None:
        named = (await s.execute(select(Agent).where(Agent.name == ref))).scalars().all()
        if not named:
            raise ValueError(f"Step '{name}': unknown fix agent {ref}")
        inside = [a for a in named if pid and a.project_id == pid]
        if len(inside) > 1:
            raise ValueError(f"Step '{name}': fix agent name {ref} is ambiguous, use its id")
        if not inside:
            if all(not a.project_id for a in named):
                raise ValueError(f"Step '{name}': fix agent {ref} has no project")
            raise ValueError(f"Step '{name}': fix agent {ref} belongs to another project")
        agent = inside[0]
    if not agent.project_id:
        raise ValueError(f"Step '{name}': fix agent {ref} has no project")
    if agent.project_id != pid:
        raise ValueError(f"Step '{name}': fix agent {ref} belongs to another project")
    return {"agent": agent.id, "rounds": rounds}


def _before(st: dict, name: str) -> list[str]:
    before = st.get("before")
    if before is None:
        return []
    if not isinstance(before, list) or len(before) > 5:
        raise ValueError(f"Step '{name}': before holds at most 5 pre-flight lines")
    out = []
    for line in before:
        preflight.parse(line)
        out.append(line.strip())
    return out


async def _project(s, st: dict, name: str, required: bool) -> str | None:
    pid = st.get("project")
    if not pid:
        if required:
            raise ValueError(f"Step '{name}': a project is required")
        return None
    p = await s.get(Project, pid)
    if p is None:
        raise ValueError(f"Step '{name}': unknown project {pid}")
    if not (p.directory or "").strip():
        raise ValueError(f"Step '{name}': project {p.name} has no directory")
    return pid


async def validate_stages(s, stages: list[dict]) -> list[dict]:
    """Return the normalized stages, or raise ValueError with a plain reason."""
    out = []
    for i, st in enumerate(stages):
        name = st.get("name") or f"Stage {i + 1}"
        kind = stage_kind(st)
        st = dict(st)
        if kind == "agent":
            if st.get("kind") not in (None, "agent"):
                st.pop("kind")
            if not st.get("items"):
                raise ValueError(f"Step '{name}': an agent step needs at least one item")
            if st.get("before") is not None:
                st["before"] = _before(st, name)
            ar = st.pop("auto_retry", None)
            if ar is not None and not isinstance(ar, bool):
                raise ValueError(f"Step '{name}': auto_retry must be true or false")
            if ar:
                st["auto_retry"] = True
            items = []
            for item in st["items"]:
                item = dict(item)
                outs = _outputs(item, name)
                item.pop("outputs", None)
                if outs:
                    item["outputs"] = outs
                items.append(item)
            st["items"] = items
            st.pop("fix", None)
            st.pop("ahead", None)
            out.append(st)
            continue
        st["kind"] = kind
        st["before"] = _before(st, name)
        if kind == "gate":
            run = st.get("run")
            if not isinstance(run, list) or not 1 <= len(run) <= 10:
                raise ValueError(f"Step '{name}': a gate runs 1 to 10 commands")
            try:
                cmds = [check_command(c) for c in run]
                retry = st.get("retry")
                retry = check_command(retry) if retry else None
            except ValueError as e:
                raise ValueError(f"Step '{name}': {e}") from None
            if retry is not None:
                if len(cmds) != 1:
                    raise ValueError(f"Step '{name}': retry works only with exactly one command")
                if retry == cmds[0]:
                    raise ValueError(f"Step '{name}': retry must differ from the command")
            t = st.get("timeout_min", 60)
            t = 60 if t is None else t
            if isinstance(t, bool) or not isinstance(t, int) or not 1 <= t <= 720:
                raise ValueError(f"Step '{name}': timeout_min must be a whole number from 1 to 720")
            st.update(
                run=cmds, retry=retry, timeout_min=t,
                project=await _project(s, st, name, True),
            )
            st.pop("message", None)
            fix = await _fix(s, st, name)
            st.pop("fix", None)
            if fix:
                st["fix"] = fix
        else:
            msg = st.get("message")
            msg = "" if msg is None else clean_text(msg)
            if len(msg) > 2000:
                raise ValueError(f"Step '{name}': message can be at most 2000 characters")
            st["message"] = msg
            st["project"] = await _project(s, st, name, False)
            for k in ("run", "retry", "timeout_min"):
                st.pop(k, None)
        st.pop("items", None)
        st.pop("auto_retry", None)
        if kind != "gate":
            st.pop("fix", None)
        ahead = st.pop("ahead", None)
        if kind == "approval" and ahead is not None:
            if not isinstance(ahead, bool):
                raise ValueError(f"Step '{name}': ahead must be true or false")
            if ahead is False:
                st["ahead"] = False  # never approved before the run reaches it
        out.append(st)
    return out
