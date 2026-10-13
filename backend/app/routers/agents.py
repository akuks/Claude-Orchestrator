from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from ..constants import VALID_MODELS
from ..database import SessionLocal
from ..models import Agent, Project
from ..schemas import (
    AgentBulkRun,
    AgentCreate,
    AgentOut,
    AgentRun,
    AgentUpdate,
    DispatchRequest,
    TaskOut,
)
from ..task_service import build_task

router = APIRouter(prefix="/agents", tags=["agents"])

# One-click starter agents / templates. Each carries not just a persona
# (system_prompt) but the governance that makes the role meaningful: model,
# budget, approval-gating, and remote-login (SSH) permission. The frontend seeds
# the Agent form from these so you pick a project and review before creating.
# `category` groups them in the UI. Connector-based ones assume the relevant MCP
# servers (Gmail, Google Calendar, GitHub…) are configured.
_DEV_TEAM = "Dev team"

PRESETS = [
    {
        "name": "Morning Brief",
        "category": "Personal",
        "description": "A daily briefing: calendar, unread mail, open PRs, and what needs you.",
        "system_prompt": (
            "You are a concise personal chief-of-staff. Produce a scannable morning "
            "brief in markdown with clear sections and short bullets. Lead with what "
            "is time-sensitive or needs a decision today. Be factual; never invent "
            "events or messages. If a data source is unavailable, note it and move on."
        ),
        "default_prompt": (
            "Give me my morning brief: today's calendar events, unread/important email, "
            "open pull requests awaiting my review, and anything urgent. End with a short "
            "'Top 3 for today' list."
        ),
        "tags": ["personal", "brief"],
    },
    {
        "name": "Weekly Review",
        "category": "Personal",
        "description": "A Sunday roll-up of the week with what shipped, decisions, and open loops.",
        "system_prompt": (
            "You are a reflective weekly-review assistant. Summarize the week factually "
            "and quote the user's own notes/commits back to them where relevant. Keep it "
            "honest and specific — progress, decisions made, and unfinished threads."
        ),
        "default_prompt": (
            "Produce my weekly review: what got done this week, key decisions, blockers, "
            "and open loops to carry into next week. Group by theme and keep it tight."
        ),
        "tags": ["personal", "review"],
    },
    {
        "name": "Security Auditor",
        "category": "Security",
        "description": "A read-only VAPT/STQC reviewer role you can point at any project.",
        "system_prompt": (
            "You are a meticulous application-security auditor (OWASP Top 10 / CWE Top 25). "
            "Read-only: never modify, commit, push, or merge. Report findings by severity "
            "with file:line, impact, and concrete remediation."
        ),
        "default_prompt": "Audit the changed files on the current branch for security issues.",
        "tags": ["security", "vapt"],
    },
    # ---- Dev team: role templates you instantiate onto a project ----
    {
        "name": "Tech Lead",
        "category": _DEV_TEAM,
        "description": "Analyses work and delegates to specialist agents (coordinator). Does not code itself.",
        "system_prompt": (
            "You are a pragmatic engineering tech lead / coordinator. You analyse "
            "incoming work (a PR, spec, or bug report), break it into sub-tasks by "
            "role, and delegate each to the right specialist agent — you do NOT "
            "implement it yourself. Dispatch independent sub-tasks together so they "
            "run in parallel; sequence dependent stages (test after code, deploy "
            "after test). Keep each delegated prompt specific and self-contained. "
            "Finish by reporting who you delegated what, and the child task ids."
        ),
        "default_prompt": "Analyse the work (PR number, spec, or description) and delegate it to the right agents.",
        "max_turns": 30,
        "max_budget_usd": 2.0,
        "coordinator": True,
        "requires_approval": True,  # review the delegation plan before it fans out
        "tags": ["dev", "coordinator", "lead"],
    },
    {
        "name": "Frontend Developer",
        "category": _DEV_TEAM,
        "description": "Builds UI: components, state, styling, accessibility — in the project's conventions.",
        "system_prompt": (
            "You are a senior frontend engineer. Work within THIS project's existing "
            "conventions, framework, and component patterns. Focus on UI, state, styling, "
            "accessibility, and responsive behaviour. Make focused, minimal diffs; reuse "
            "existing components over adding new ones. Run the project's build/lint before "
            "finishing. Stay out of backend and infrastructure code — flag those for the "
            "relevant role."
        ),
        "default_prompt": "Implement the requested frontend change on this branch.",
        "max_turns": 30,
        "max_budget_usd": 2.0,
        "tags": ["dev", "frontend"],
    },
    {
        "name": "Backend Developer",
        "category": _DEV_TEAM,
        "description": "APIs, business logic, data models, and migrations — matching the project's architecture.",
        "system_prompt": (
            "You are a senior backend engineer. Focus on APIs, business logic, data models, "
            "and database migrations, matching THIS project's architecture and conventions. "
            "Add or update tests for what you change. Treat schema migrations as high-risk: "
            "call them out explicitly and keep them reversible. Do not modify frontend code "
            "or deploy."
        ),
        "default_prompt": "Implement the requested backend change on this branch.",
        "max_turns": 30,
        "max_budget_usd": 2.0,
        "tags": ["dev", "backend"],
    },
    {
        "name": "Fullstack Developer",
        "category": _DEV_TEAM,
        "description": "End-to-end feature work across frontend and backend. The generalist.",
        "system_prompt": (
            "You are a senior fullstack engineer. Implement features end-to-end across "
            "frontend and backend, matching THIS project's conventions on both sides. Keep "
            "diffs focused, add tests for new behaviour, and run the project's build and "
            "tests before finishing. Call out any schema migration or deploy step rather "
            "than assuming it."
        ),
        "default_prompt": "Implement the requested feature end-to-end on this branch.",
        "max_turns": 40,
        "max_budget_usd": 3.0,
        "tags": ["dev", "fullstack"],
    },
    {
        "name": "Code Reviewer",
        "category": _DEV_TEAM,
        "description": "Read-only PR/code review for correctness, edge cases, and conventions.",
        "system_prompt": (
            "You are a meticulous code reviewer. READ-ONLY: never edit, commit, push, or "
            "merge — if you think a change is needed, describe it, don't make it. Review the "
            "changed code for correctness bugs, edge cases, error handling, readability, and "
            "adherence to THIS project's conventions. Report findings by severity with "
            "file:line and a concrete suggested fix. (Security vulnerabilities are the "
            "Security Auditor's job — stay on code quality.)"
        ),
        "default_prompt": "Review the changed files on the current branch and report findings.",
        "max_turns": 25,
        "max_budget_usd": 1.0,
        "tags": ["dev", "review"],
    },
    {
        "name": "QA / Tester",
        "category": _DEV_TEAM,
        "description": "Writes and runs tests; reproduces bugs with a failing test first.",
        "system_prompt": (
            "You are a QA engineer. Write and run tests (unit / integration / e2e as "
            "appropriate to THIS project). Reproduce any bug with a failing test first, then "
            "confirm the fix makes it pass. Run the project's test suite and report results "
            "honestly — never claim tests pass without actually running them. Keep changes to "
            "test code; flag larger production fixes for a developer."
        ),
        "default_prompt": "Add tests for the recent changes and run the suite; report results.",
        "max_turns": 30,
        "max_budget_usd": 2.0,
        "tags": ["dev", "qa", "testing"],
    },
    {
        "name": "DevOps Engineer",
        "category": _DEV_TEAM,
        "description": "Infra-as-code, CI/CD, containers, and deploys. SSH-capable and approval-gated.",
        "system_prompt": (
            "You are a DevOps engineer. Handle infrastructure-as-code, CI/CD pipelines, "
            "containerisation (Docker/K8s), deploy scripts, and environment/config in the "
            "repo. To deploy to a server, connect non-interactively: "
            "ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "
            "-i <pem> <user>@<host>. Take the minimum necessary action, capture current state "
            "for rollback, and verify health after each change. NEVER run destructive infra "
            "commands (terraform destroy, kubectl delete, rm -rf, force-push) unless they are "
            "explicitly part of the approved plan. If reality differs from the plan, STOP and "
            "report instead of improvising."
        ),
        "default_prompt": (
            "Describe the deploy/infra task. Include the target server (name from the "
            "inventory, or user@host) and the PEM path if deploying."
        ),
        "max_turns": 40,
        "max_budget_usd": 3.0,
        "requires_approval": True,  # deploys/infra changes are irreversible — always gated
        "allow_remote": True,  # may SSH in to deploy
        "tags": ["devops", "ops", "ssh"],
    },
]


@router.get("/presets")
async def list_presets():
    return PRESETS


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(payload: AgentCreate):
    if payload.model and payload.model not in VALID_MODELS:
        raise HTTPException(400, f"Invalid model. Use one of {sorted(VALID_MODELS)}")
    async with SessionLocal() as s:
        if payload.project_id and (await s.get(Project, payload.project_id)) is None:
            raise HTTPException(404, "Project not found")
        agent = Agent(
            name=payload.name,
            description=payload.description,
            system_prompt=payload.system_prompt,
            default_prompt=payload.default_prompt,
            project_id=payload.project_id,
            model=payload.model or "",
            max_turns=payload.max_turns,
            max_budget_usd=payload.max_budget_usd,
            priority=payload.priority,
            tags=payload.tags,
            requires_approval=payload.requires_approval,
            allow_remote=payload.allow_remote,
            folder=payload.folder,
            coordinator=payload.coordinator,
        )
        s.add(agent)
        await s.commit()
        await s.refresh(agent)
        return AgentOut.model_validate(agent)


@router.get("", response_model=list[AgentOut])
async def list_agents():
    async with SessionLocal() as s:
        rows = (
            await s.execute(select(Agent).order_by(Agent.created_at.desc()))
        ).scalars().all()
    return [AgentOut.model_validate(a) for a in rows]


async def _get_or_404(s, agent_id: str) -> Agent:
    agent = await s.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(agent_id: str, payload: AgentUpdate):
    data = payload.model_dump(exclude_unset=True)
    if data.get("model") and data["model"] not in VALID_MODELS:
        raise HTTPException(400, "Invalid model")
    async with SessionLocal() as s:
        agent = await _get_or_404(s, agent_id)
        for k, v in data.items():
            setattr(agent, k, v)
        await s.commit()
        await s.refresh(agent)
        return AgentOut.model_validate(agent)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str):
    async with SessionLocal() as s:
        agent = await _get_or_404(s, agent_id)
        await s.delete(agent)
        await s.commit()


@router.post("/{agent_id}/run", response_model=TaskOut, status_code=201)
async def run_agent(agent_id: str, payload: AgentRun, request: Request):
    """Run an agent: its role (system prompt) + governance, on the resolved
    project (override or the agent's default), with the given task input."""
    async with SessionLocal() as s:
        agent = await _get_or_404(s, agent_id)
        prompt = payload.prompt or agent.default_prompt
        if not prompt:
            raise HTTPException(400, "This agent has no default prompt; provide one")
        project_id = payload.project_id or agent.project_id
        task = await build_task(
            s,
            prompt=prompt,
            title=f"{agent.name}: {prompt.splitlines()[0][:60]}",
            project_id=project_id,
            model=agent.model or None,
            max_turns=agent.max_turns,
            max_budget_usd=agent.max_budget_usd,
            priority=agent.priority,
            tags=(agent.tags or []) + ["agent"],
            agent_id=agent.id,
            system_prompt=agent.system_prompt or None,
            requires_approval=agent.requires_approval,
            allow_remote=agent.allow_remote,
            input_files=[f.model_dump() for f in payload.input_files],
            parent_task_id=payload.parent_task_id,
        )
        await s.commit()
        await s.refresh(task)
        out = TaskOut.model_validate(task)
    from ..constants import Status

    if out.status != Status.AWAITING_APPROVAL:
        await request.app.state.worker.submit(out.id, out.priority, out.created_at)
    return out


@router.post("/dispatch", response_model=list[TaskOut], status_code=201)
async def dispatch(payload: DispatchRequest, request: Request):
    """Coordinator fan-out: create one child task per item, each delegated to the
    named agent and linked to the parent task, then submit them (worker runs them
    in parallel). Reuses the normal agent-run path — no separate orchestration."""
    from ..constants import Status

    async with SessionLocal() as s:
        # Resolve each target agent by id or exact name.
        all_agents = (await s.execute(select(Agent))).scalars().all()
        by_id = {a.id: a for a in all_agents}
        by_name = {a.name: a for a in all_agents}
        outs = []
        for item in payload.items:
            agent = by_id.get(item.agent) or by_name.get(item.agent)
            if agent is None:
                raise HTTPException(404, f"Agent not found: {item.agent}")
            task = await build_task(
                s,
                prompt=item.prompt,
                title=f"{agent.name}: {item.prompt.splitlines()[0][:60]}",
                project_id=agent.project_id or payload.project_id,
                model=agent.model or None,
                max_turns=agent.max_turns,
                max_budget_usd=agent.max_budget_usd,
                priority=agent.priority,
                tags=(agent.tags or []) + ["agent", "delegated"],
                agent_id=agent.id,
                system_prompt=agent.system_prompt or None,
                requires_approval=agent.requires_approval,
                allow_remote=agent.allow_remote,
                parent_task_id=payload.parent_task_id,
            )
            outs.append(task)
        await s.commit()
        for t in outs:
            await s.refresh(t)
        results = [TaskOut.model_validate(t) for t in outs]

    for o in results:
        if o.status != Status.AWAITING_APPROVAL:
            await request.app.state.worker.submit(o.id, o.priority, o.created_at)
    return results


@router.post("/{agent_id}/bulk", response_model=list[TaskOut], status_code=201)
async def bulk_run_agent(agent_id: str, payload: AgentBulkRun, request: Request):
    """Create one task per prompt — upload a task list and the agent works each."""
    prompts = [p.strip() for p in payload.prompts if p.strip()]
    if not prompts:
        raise HTTPException(400, "No tasks found in the upload")
    if len(prompts) > 50:
        raise HTTPException(400, "Too many tasks at once (max 50)")
    async with SessionLocal() as s:
        agent = await _get_or_404(s, agent_id)
        project_id = payload.project_id or agent.project_id
        tasks = []
        for p in prompts:
            tasks.append(
                await build_task(
                    s,
                    prompt=p,
                    title=f"{agent.name}: {p.splitlines()[0][:60]}",
                    project_id=project_id,
                    model=agent.model or None,
                    max_turns=agent.max_turns,
                    max_budget_usd=agent.max_budget_usd,
                    priority=agent.priority,
                    tags=(agent.tags or []) + ["agent", "bulk"],
                    agent_id=agent.id,
                    system_prompt=agent.system_prompt or None,
                    requires_approval=agent.requires_approval,
                    allow_remote=agent.allow_remote,
                )
            )
        await s.commit()
        for t in tasks:
            await s.refresh(t)
        outs = [TaskOut.model_validate(t) for t in tasks]
    from ..constants import Status

    for o in outs:
        if o.status != Status.AWAITING_APPROVAL:
            await request.app.state.worker.submit(o.id, o.priority, o.created_at)
    return outs
