"""Stage kinds and save-time validation for gate and approval stages."""

from ..models import Project
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
        out.append(st)
    return out
