"""Outbound notifications for scheduled task runs (Slack incoming webhooks).

Kept dependency-free (urllib in a thread). Email and other channels are future
work; this covers the Phase 4 "notify on completion / failure" requirement,
and co-guard action requests waiting for a person (the schedule's webhook,
unless its notify policy is "never").
"""

import asyncio
import json
import threading
import urllib.request

from sqlalchemy import select

from .config import settings
from .constants import Status
from .database import SessionLocal
from .models import ActionApproval, Schedule, Task


def _post_webhook(url: str, text: str) -> None:
    data = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception:  # noqa: BLE001 - notification failure must not break tasks
        pass


async def notify_task_finished(task_id: str) -> None:
    """If the task came from a schedule, honor that schedule's notify policy."""
    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        if task is None or not task.schedule_id:
            return
        schedule = (
            await s.execute(select(Schedule).where(Schedule.id == task.schedule_id))
        ).scalar_one_or_none()
        if schedule is None or not schedule.notify_webhook:
            return
        policy = schedule.notify  # never | on_failure | always
        failed = task.status == Status.FAILED
        if policy == "never" or (policy == "on_failure" and not failed):
            return
        emoji = "❌" if failed else "✅"
        text = (
            f"{emoji} Scheduled task *{schedule.name}* {task.status}\n"
            f"> {task.title}\n"
            f"Task `{task.id}`"
        )
        webhook = schedule.notify_webhook

    await asyncio.get_event_loop().run_in_executor(None, _post_webhook, webhook, text)


async def action_waiting_message(s, a: ActionApproval, task: Task) -> tuple[str, str] | None:
    """(webhook, text) when co-guard's action request should reach the task's
    configured channel (a scheduled run's webhook, unless its policy is
    "never"); else None. Only the stored, redacted summary's first line
    (<= 200 chars) goes out."""
    if not task.schedule_id:
        return None
    schedule = await s.get(Schedule, task.schedule_id)
    if schedule is None or not schedule.notify_webhook or schedule.notify == "never":
        return None
    rules = ", ".join(str(r) for r in (a.rule_ids or [])) or "co-guard"
    first = (a.summary or a.tool or "").split("\n", 1)[0]
    if len(first) > 200:
        first = first[:199] + "…"
    base = (settings.public_url or "").rstrip("/")
    link = f"{base}/#/approvals/{a.id}" if base else f"Approvals → Actions (#/approvals/{a.id})"
    text = (
        f"⏸️ Agent action waiting for approval: {task.title} — {rules}\n"
        f"> {first}\n"
        f"{link}"
    )
    return schedule.notify_webhook, text


def post_in_background(webhook: str, text: str) -> None:
    """Fire and forget (a thread: never delays or breaks the caller)."""
    threading.Thread(target=_post_webhook, args=(webhook, text), daemon=True).start()
