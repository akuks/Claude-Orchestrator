"""Per-step usage from the stream-json events (F96).

Each ``assistant`` event carries ``message.id``, ``message.model`` and
``message.usage``. One API message can arrive as several events with the same
id: it counts once, with the LAST usage seen (output grows). An event with
``parent_tool_use_id`` belongs to the helper started by that main-thread
``Agent``/``Task`` call; its kind is the call's ``subagent_type`` (else
``description``, else "helper"). Missing ids, usage or numbers are tolerated.
Context size = input + cache write + cache read of the latest main message.
"""

import json
import os

from . import prices

USAGE_FILE = "usage.json"
LARGEST_KEEP = 5
_HELPER_TOOLS = ("Agent", "Task")
_FIELDS = ("input", "cache_write", "cache_read", "output")


def _n(v) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0


def _bucket() -> dict:
    return {"messages": 0, "input": 0, "cache_write": 0, "cache_read": 0, "output": 0}


class UsageTracker:
    def __init__(self) -> None:
        # message key -> {"helper": kind | None, "model": str | None, "u": (input, cw, cr, output)}
        self._msgs: dict[str, dict] = {}
        self._anon = 0  # messages without an id (each its own)
        self._helper_kinds: dict[str, str] = {}  # main-thread Agent/Task tool_use id -> kind
        self._tool_names: dict[str, str] = {}  # tool_use id -> tool name
        self._largest: list[dict] = []
        self._last_model: str | None = None
        self.injected_chars = 0
        self._context: int | None = None
        self._peak: int | None = None

    # ---- feeding -----------------------------------------------------------

    def on_assistant(self, obj: dict) -> None:
        msg = obj.get("message") if isinstance(obj, dict) else None
        if not isinstance(msg, dict):
            return
        model = msg.get("model") if isinstance(msg.get("model"), str) and msg.get("model") else None
        if model:
            self._last_model = model
        content = msg.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                self._tool_names[str(block["id"])] = str(block.get("name") or "")
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            return
        u = (_n(usage.get("input_tokens")), _n(usage.get("cache_creation_input_tokens")),
             _n(usage.get("cache_read_input_tokens")), _n(usage.get("output_tokens")))
        parent = obj.get("parent_tool_use_id")
        helper = (self._helper_kinds.get(str(parent)) or "helper") if parent else None
        mid = msg.get("id")
        if mid:
            key = "id:" + str(mid)
        else:
            self._anon += 1
            key = f"anon:{self._anon}"
        self._msgs[key] = {"helper": helper, "model": model or self._last_model, "u": u}
        if helper is None and (ctx := u[0] + u[1] + u[2]) > 0:
            self._context = ctx
            self._peak = max(self._peak or 0, ctx)

    def on_tool_use_main(self, tool_use_id: str, name: str, input: dict) -> None:
        if tool_use_id:
            self._tool_names[str(tool_use_id)] = str(name or "")
        if name in _HELPER_TOOLS and tool_use_id:
            inp = input if isinstance(input, dict) else {}
            kind = inp.get("subagent_type") or inp.get("description") or "helper"
            self._helper_kinds[str(tool_use_id)] = str(kind)[:80]

    def on_tool_result(self, tool_use_id: str, tool_name: str | None, raw_len: int) -> None:
        tid = str(tool_use_id or "")
        rec = {"tool": tool_name or self._tool_names.get(tid) or "", "chars": _n(raw_len), "id": tid}
        if len(self._largest) >= LARGEST_KEEP and rec["chars"] <= self._largest[-1]["chars"]:
            return
        self._largest.append(rec)
        self._largest.sort(key=lambda r: -r["chars"])
        del self._largest[LARGEST_KEEP:]

    def add_injected(self, chars: int) -> None:
        self.injected_chars += _n(chars)

    # ---- figures -----------------------------------------------------------

    @property
    def context_tokens(self) -> int | None:
        return self._context

    @property
    def peak_context(self) -> int | None:
        return self._peak

    def _per_model(self) -> dict[str, dict]:
        models: dict[str, dict] = {}
        for m in self._msgs.values():
            b = models.setdefault(m["model"] or "unknown", _bucket())
            b["messages"] += 1
            for f, v in zip(_FIELDS, m["u"]):
                b[f] += v
        return models

    @property
    def est_cost_usd(self) -> float | None:
        """The running estimate; None when nothing counted yet or any model is unknown."""
        models = self._per_model()
        if not models:
            return None
        total = 0.0
        for model, b in models.items():
            est = prices.estimate(None if model == "unknown" else model,
                                  b["input"], b["output"], b["cache_write"], b["cache_read"])
            if est is None:
                return None
            total += est
        return total

    def snapshot(self) -> dict:
        main, helpers = _bucket(), {}
        for m in self._msgs.values():
            b = main if m["helper"] is None else helpers.setdefault(m["helper"], _bucket())
            b["messages"] += 1
            for f, v in zip(_FIELDS, m["u"]):
                b[f] += v
        return {"main": main, "helpers": helpers, "models": self._per_model(),
                "largest_results": [dict(r) for r in self._largest],
                "injected_chars": self.injected_chars, "messages": len(self._msgs)}


def write_usage_file(task_id: str, data: dict) -> bool:
    """``${CO_TASK_DIR}/usage.json`` (0600, atomic replace, never through a
    planted link); False when the task dir is gone."""
    from ..guard import runtime as guard_runtime

    d = guard_runtime.task_dir(task_id)
    if not d.is_dir():
        return False
    tmp, path = d / (USAGE_FILE + ".tmp"), d / USAGE_FILE
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)
    return True
