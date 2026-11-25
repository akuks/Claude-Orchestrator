"""Inbound webhooks — GitHub PR events auto-trigger a security review.

Point a repo's webhook (content-type application/json, event: pull_request) at
POST /webhooks/github with the secret in CO_GITHUB_WEBHOOK_SECRET. When a PR is
opened/reopened/synchronized on a project whose github_repo matches and which has
auto_review_prs enabled, a read-only VAPT security review runs on the PR branch.
"""

import hashlib
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select

from ..access import project_role, user_is_maintainer
from ..auth import Principal
from ..config import settings
from ..credentials import credential_status
from ..database import SessionLocal
from ..models import Project, User
from ..security import build_security_prompt
from ..task_service import build_task

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = logging.getLogger(__name__)

_PR_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}


def _verify(body: bytes, signature: str | None) -> bool:
    if not settings.github_webhook_secret:
        return False  # never accept unsigned payloads (handler 503s first)
    if not signature or not signature.startswith("sha256="):
        return False
    digest = hmac.new(
        settings.github_webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", signature)


async def _automation_owner(s, project: Project) -> str | None:
    """Who the review runs as: the project's automation owner while they are
    still an active admin or maintainer of it, else the project creator while
    they are active and still have a role on it; None when neither is usable."""
    if project.automation_owner_id and await user_is_maintainer(
        s, project.automation_owner_id, project.id
    ):
        return project.automation_owner_id
    if project.created_by:
        user = await s.get(User, project.created_by)
        if user is not None and await project_role(s, Principal(user), project.id):
            return project.created_by
    return None


@router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str | None = Header(default=None),
):
    if not settings.github_webhook_secret:
        raise HTTPException(503, "GitHub webhook secret not configured")
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
        owner_id = await _automation_owner(s, project)
        if owner_id is None:
            log.warning("Webhook ignored for project %s: no automation owner", project.id)
            return {"ok": True, "ignored": "no automation owner"}
        if (await credential_status(s, owner_id))["claude"] == "none":
            log.warning(
                "Webhook ignored for project %s: automation owner has no Claude credential",
                project.id,
            )
            return {"ok": True, "ignored": "automation owner has no Claude credential"}

        task = await build_task(
            s,
            prompt=build_security_prompt(branch, base, "changed", project.directory),
            title=f"Security review: PR #{number} ({branch})",
            project_id=project.id,
            model="opus",
            max_turns=60,
            tags=["security", "vapt", "code-review", "auto"],
            created_by=owner_id,
        )
        info = (task.id, task.priority, task.created_at, task.status)
        await s.commit()

    from ..constants import Status

    if info[3] != Status.AWAITING_APPROVAL:
        await request.app.state.worker.submit(info[0], info[1], info[2])
    return {"ok": True, "task_id": info[0], "project": project.name, "branch": branch}
