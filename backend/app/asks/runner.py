"""The tool-less one-shot `claude --print` call that answers an ask."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass

from ..config import settings
from .texts import ASK_FRAME

DISALLOWED = ["Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "Read", "Glob", "Grep",
              "WebFetch", "WebSearch", "Task", "Agent", "PowerShell"]


@dataclass
class AskResult:
    answer: str | None = None
    cost_usd: float | None = None
    error: str | None = None


def argv() -> list[str]:
    return [
        settings.claude_bin, "--print", "--output-format", "json", "--model", settings.ask_model,
        "--max-turns", "1", "--permission-mode", "default", "--strict-mcp-config",
        "--max-budget-usd", str(settings.ask_budget_usd), "--append-system-prompt", ASK_FRAME,
        "--disallowedTools", *DISALLOWED,
    ]


async def _kill(proc) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.shield(proc.wait())
    except BaseException:
        pass


def _parse(text: str) -> AskResult:
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    if isinstance(obj, dict):
        if obj.get("is_error"):
            return AskResult(error="no answer")
        ans = obj.get("result")
        cost = obj.get("total_cost_usd")
        cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
        if isinstance(ans, str) and ans.strip():
            return AskResult(answer=ans.strip(), cost_usd=cost)
        return AskResult(error="no answer")
    return AskResult(answer=text, cost_usd=None)


async def run_ask(prompt: str, env: dict) -> AskResult:
    cwd = tempfile.mkdtemp(prefix="co-ask-")
    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=cwd,
                env=dict(env),  # uvloop only accepts a plain dict
            )
        except (FileNotFoundError, OSError):
            return AskResult(error="claude not available")
        try:
            out, _ = await asyncio.wait_for(
                proc.communicate(prompt.encode()), timeout=settings.ask_timeout_seconds
            )
        except asyncio.TimeoutError:
            await _kill(proc)
            return AskResult(error="timed out")
        except asyncio.CancelledError:
            await _kill(proc)
            raise
        text = out.decode(errors="replace").strip()
        if proc.returncode != 0 or not text:
            return AskResult(error="no answer")
        return _parse(text)
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
