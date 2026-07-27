"""Outbound notifications for scheduled task runs (Slack incoming webhooks).

Kept dependency-free (urllib in a thread). Email and other channels are future
work; this covers the Phase 4 "notify on completion / failure" requirement.
"""

import asyncio
import json
import urllib.request

from sqlalchemy import select

from .constants import Status
from .database import SessionLocal
from .models import Schedule, Task


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
