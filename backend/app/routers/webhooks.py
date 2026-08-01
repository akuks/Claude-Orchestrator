"""Inbound webhooks — GitHub PR events auto-trigger a security review.

Point a repo's webhook (content-type application/json, event: pull_request) at
POST /webhooks/github with the secret in CO_GITHUB_WEBHOOK_SECRET. When a PR is
opened/reopened/synchronized on a project whose github_repo matches and which has
auto_review_prs enabled, a read-only VAPT security review runs on the PR branch.
"""

import hashlib
import hmac

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select

from ..config import settings
from ..database import SessionLocal
from ..models import Project
from ..security import build_security_prompt
from ..task_service import build_task

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_PR_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}


def _verify(body: bytes, signature: str | None) -> bool:
    if not settings.github_webhook_secret:
        return True  # verification disabled
    if not signature or not signature.startswith("sha256="):
        return False
    digest = hmac.new(
        settings.github_webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", signature)


@router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str | None = Header(default=None),
):
    body = await request.body()
    if not _verify(body, x_hub_signature_256):
        raise HTTPException(401, "Invalid signature")

    if x_github_event == "ping":
        return {"ok": True, "pong": True}
    if x_github_event != "pull_request":
        return {"ok": True, "ignored": f"event {x_github_event}"}

    payload = await request.json()
    action = payload.get("action")
    if action not in _PR_ACTIONS:
        return {"ok": True, "ignored": f"action {action}"}

    repo = (payload.get("repository") or {}).get("full_name")
    pr = payload.get("pull_request") or {}
    branch = (pr.get("head") or {}).get("ref")
    base = (pr.get("base") or {}).get("ref") or "main"
    number = pr.get("number")
    if not (repo and branch):
        return {"ok": True, "ignored": "missing repo/branch"}

    async with SessionLocal() as s:
        project = (
            await s.execute(
                select(Project).where(
                    Project.github_repo == repo, Project.auto_review_prs.is_(True)
                )
            )
        ).scalar_one_or_none()
        if project is None:
            return {"ok": True, "ignored": f"no auto-review project for {repo}"}

        task = await build_task(
            s,
            prompt=build_security_prompt(branch, base, "changed"),
            title=f"Security review: PR #{number} ({branch})",
            project_id=project.id,
            model="opus",
            max_turns=60,
            tags=["security", "vapt", "code-review", "auto"],
        )
        info = (task.id, task.priority, task.created_at, task.status)
        await s.commit()

    from ..constants import Status

    if info[3] != Status.AWAITING_APPROVAL:
        await request.app.state.worker.submit(info[0], info[1], info[2])
    return {"ok": True, "task_id": info[0], "project": project.name, "branch": branch}
