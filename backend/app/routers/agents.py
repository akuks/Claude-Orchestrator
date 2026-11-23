from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from ..access import project_role, require_project_role, require_task
from ..auth import ensure_admin
from .. import tiers
from ..constants import VALID_MODELS
from ..database import SessionLocal
from ..models import Agent, Project
from ..resume import ground_rules
from ..schemas import (
    AgentBulkRun,
    AgentCreate,
    AgentOut,
    AgentRun,
    AgentUpdate,
    DispatchRequest,
    TaskOut,
)
from ..models import Task
from ..task_service import build_task

router = APIRouter(prefix="/agents", tags=["agents"])


async def _require_parent(s, request: Request, parent_task_id: str | None) -> None:
    """A human caller may only link children under a task they can see (404
    otherwise). Agent tokens are confined to their own task by dispatch."""
    p = request.state.principal
    if parent_task_id and not p.is_agent:
        await require_task(s, p, parent_task_id)


async def _creator(s, request: Request, parent_task_id: str | None) -> str:
    """Owner for a task created under a parent. Only an agent-task token inherits
    the parent's creator (its coordinator's owner); a human caller always owns
    what they create, so nobody's tasks run as someone else."""
    if parent_task_id and request.state.principal.is_agent:
        parent = await s.get(Task, parent_task_id)
        if parent is not None and parent.created_by:
            return parent.created_by
    return request.state.principal.user.id

# One-click starter agents / templates. Each carries not just a persona
# (system_prompt) but the governance that makes the role meaningful: model,
# budget, approval-gating, and remote-login (SSH) permission. The frontend seeds
# the Agent form from these so you pick a project and review before creating.
# `category` groups them in the UI. Connector-based ones assume the relevant MCP
# servers (Gmail, Google Calendar, GitHub…) are configured.
_DEV_TEAM = "Dev team"
_HELPERS = "Helpers"

PRESETS = [
    {
        "name": "Morning Brief",
        "category": "Personal",
        "description": "A daily briefing of calendar, unread mail, open PRs and what needs you. Use for a start-of-day summary, because one scannable page saves checking each source.",
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
        "tier": "light",
        "tags": ["personal", "brief"],
    },
    {
        "name": "Weekly Review",
        "category": "Personal",
        "description": "A Sunday roll-up of what shipped, decisions and open loops. Use for a weekly review, because writing the week down keeps unfinished threads from being lost.",
        "system_prompt": (
            "You are a reflective weekly-review assistant. Summarize the week factually "
            "and quote the user's own notes/commits back to them where relevant. Keep it "
            "honest and specific — progress, decisions made, and unfinished threads."
        ),
        "default_prompt": (
            "Produce my weekly review: what got done this week, key decisions, blockers, "
            "and open loops to carry into next week. Group by theme and keep it tight."
        ),
        "tier": "light",
        "tags": ["personal", "review"],
    },
    {
        "name": "Security Auditor",
        "category": "Security",
        "description": "A read-only VAPT/STQC security reviewer for any project. Use for a security audit of a branch, because it cannot change code while it reports findings.",
        "system_prompt": (
            "You are a meticulous application-security auditor (OWASP Top 10 / CWE Top 25). "
            "Read-only: never modify, commit, push, or merge. Report findings by severity "
            "with file:line, impact, and concrete remediation."
        ),
        "default_prompt": "Audit the changed files on the current branch for security issues.",
        "read_only": True,
        "tags": ["security", "vapt"],
    },
    # ---- Dev team: role templates you instantiate onto a project ----
    {
        "name": "Tech Lead",
        "category": _DEV_TEAM,
        "description": "Analyses work and delegates it to specialist agents; does not code itself. Use for splitting a PR, spec or bug into role-based sub-tasks, because independent parts then run in parallel.",
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
        "tier": "standard",
        "tags": ["dev", "coordinator", "lead"],
    },
    {
        "name": "Frontend Developer",
        "category": _DEV_TEAM,
        "description": "Builds UI: components, state, styling and accessibility in the project's conventions. Use for frontend changes, because it reuses existing components and keeps diffs small.",
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
        "tier": "standard",
        "tags": ["dev", "frontend"],
    },
    {
        "name": "Backend Developer",
        "category": _DEV_TEAM,
        "description": "APIs, business logic, data models and migrations, matching the project's architecture. Use for backend changes, because it tests what it changes and treats migrations as high-risk.",
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
        "tier": "standard",
        "tags": ["dev", "backend"],
    },
    {
        "name": "Fullstack Developer",
        "category": _DEV_TEAM,
        "description": "End-to-end feature work across frontend and backend. Use for a feature that touches both sides, because one agent keeps the two halves consistent.",
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
        "tier": "standard",
        "tags": ["dev", "fullstack"],
    },
    {
        "name": "Code Reviewer",
        "category": _DEV_TEAM,
        "description": "Read-only PR and code review for correctness, edge cases and conventions. Use for a review before merge, because it cannot change the code it judges.",
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
        "tier": "light",
        "read_only": True,
        "tags": ["dev", "review"],
    },
    {
        "name": "QA / Tester",
        "category": _DEV_TEAM,
        "description": "Writes and runs tests and reproduces bugs with a failing test first. Use for adding coverage or confirming a fix, because results come from tests it actually ran.",
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
        "tier": "light",
        "tags": ["dev", "qa", "testing"],
    },
    {
        "name": "DevOps Engineer",
        "category": _DEV_TEAM,
        "description": "Infra-as-code, CI/CD, containers and deploys; SSH-capable and approval-gated. Use for infrastructure and deploy work, because every run is reviewed before it touches a server.",
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
        "tier": "standard",
        "tags": ["devops", "ops", "ssh"],
    },
    # ---- Helpers: forge's four helpers, bound to the org model tiers ----
    {
        "name": "Planner",
        "category": _HELPERS,
        "description": "Designs a change and writes the plan and briefs. Use for turning a goal into a step-by-step plan with briefs, because a plan checked against recorded decisions saves rework.",
        "system_prompt": (
            "You are the planner. You design a change and write the plan; you do not implement it. Work from "
            "your brief (Goal, CAN change, CANNOT change, Done, Stop). Read the code you plan against and cite "
            "path:line. Never plan against a recorded project decision: when the goal conflicts with one, stop "
            "and file a question with your recommendation instead of planning around it. Split the work into "
            "small steps, each with its own brief: Goal, CAN change, CANNOT change, Done, Stop, and the tests "
            "that prove it. End with Outputs (the plan and the briefs, by path) and the evidence you relied on."
        ),
        "default_prompt": "Plan the change described in the brief.",
        "tier": "strongest",
        "max_turns": 30,
        "max_budget_usd": 2.0,
        "tags": ["helper", "planner"],
    },
    {
        "name": "Reviewer",
        "category": _HELPERS,
        "description": "Reviews a finished change and ranks findings. Use for a review before merge, because ranked findings with file:line and evidence are quick to act on.",
        "system_prompt": (
            "You are the reviewer. Read-only: never edit, commit, push or merge; describe a fix, do not make "
            "it. Work from your brief. Review the change for correctness, edge cases, error handling, tests and "
            "the project's recorded decisions. Rank every finding H (must fix), M (should fix) or L (nice to "
            "have), and give for each the file:line, what is wrong, and the evidence (the code you read or the "
            "command you ran and its output). Say \"no findings\" when there are none; never invent one. End "
            "with Outputs (the findings by rank) and the evidence."
        ),
        "default_prompt": "Review the change on this branch against the brief.",
        "tier": "strongest",
        "read_only": True,
        "max_turns": 25,
        "max_budget_usd": 2.0,
        "tags": ["helper", "review"],
    },
    {
        "name": "Builder",
        "category": _HELPERS,
        "description": "Implements one brief, test first. Use for a scoped change with clear CAN change files, because a tight brief keeps the diff small and the review short.",
        "system_prompt": (
            "You are the builder. Work from your brief and change only files it lets you change (CAN change); "
            "never touch CANNOT change (co-guard blocks it). Test first: write the failing test, see it fail, "
            "make it pass. Keep the diff small and in the project's style. Stop when Done is met, or when Stop "
            "says so, and report; do not widen the scope. End with Outputs (what changed, by path) and the "
            "evidence (the tests you ran and their results).\n\n" + ground_rules.USAGE_CARE_HELPER
        ),
        "default_prompt": "Implement the brief.",
        "tier": "standard",
        "max_turns": 30,
        "max_budget_usd": 2.0,
        "tags": ["helper", "builder"],
    },
    {
        "name": "Checker",
        "category": _HELPERS,
        "description": "Runs checks and reports facts only. Use for running test suites, builds and lints, because a cheap model can read long output and report the totals.",
        "system_prompt": (
            "You are the checker. Read-only: run the checks your brief names (tests, builds, lints) and report "
            "facts only: commands run, totals, failing tests with their first error lines. No opinions, no "
            "fixes, no guesses; say \"not run\" for anything you could not run. End with Outputs (each check: "
            "pass or fail, with totals) and the evidence.\n\n" + ground_rules.USAGE_CARE_HELPER
        ),
        "default_prompt": "Run the checks in the brief and report.",
        "tier": "light",
        "read_only": True,
        "max_turns": 20,
        "max_budget_usd": 1.0,
        "tags": ["helper", "checker"],
    },
]


@router.get("/presets")
async def list_presets():
    return PRESETS


async def _check_model_tier(s, model, tier) -> None:
    if tier and tier not in tiers.TIERS:
        raise HTTPException(400, f"Invalid tier. Use one of {', '.join(tiers.TIERS)}")
    if model and not tiers.valid_agent_model(model, await tiers.org_tiers(s)):
        raise HTTPException(400, f"Invalid model. Use one of {sorted(VALID_MODELS)} or a tier model")


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(payload: AgentCreate, request: Request):
    ensure_admin(request.state.principal)
    async with SessionLocal() as s:
        await _check_model_tier(s, payload.model, payload.tier)
        if payload.project_id and (await s.get(Project, payload.project_id)) is None:
            raise HTTPException(404, "Project not found")
        agent = Agent(
            name=payload.name,
            description=payload.description,
            system_prompt=payload.system_prompt,
            default_prompt=payload.default_prompt,
            project_id=payload.project_id,
            model=payload.model or "",
            tier=payload.tier or None,
            max_turns=payload.max_turns,
            max_budget_usd=payload.max_budget_usd,
            priority=payload.priority,
            tags=payload.tags,
            requires_approval=payload.requires_approval,
            allow_remote=payload.allow_remote,
            read_only=payload.read_only,
            folder=payload.folder,
            coordinator=payload.coordinator,
            created_by=request.state.principal.user.id,
        )
        s.add(agent)
        await s.commit()
        await s.refresh(agent)
        return await _out(s, agent)


async def _out(s, agent: Agent, org: dict | None = None) -> AgentOut:
    out = AgentOut.model_validate(agent)
    out.lint = tiers.lint_agent(agent.model, agent.description, org or await tiers.org_tiers(s))
    return out


@router.get("", response_model=list[AgentOut])
async def list_agents():
    async with SessionLocal() as s:
        rows = (
            await s.execute(select(Agent).order_by(Agent.created_at.desc()))
        ).scalars().all()
        org = await tiers.org_tiers(s)
        return [await _out(s, a, org) for a in rows]


async def _get_or_404(s, agent_id: str) -> Agent:
    agent = await s.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(agent_id: str, payload: AgentUpdate, request: Request):
    ensure_admin(request.state.principal)
    data = payload.model_dump(exclude_unset=True)
    async with SessionLocal() as s:
        await _check_model_tier(s, data.get("model"), data.get("tier"))
        agent = await _get_or_404(s, agent_id)
        if "tier" in data:
            data["tier"] = data["tier"] or None
        for k, v in data.items():
            setattr(agent, k, v)
        await s.commit()
        await s.refresh(agent)
        return await _out(s, agent)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, request: Request):
    ensure_admin(request.state.principal)
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
        await require_project_role(s, request.state.principal, project_id)
        await _require_parent(s, request, payload.parent_task_id)
        task = await build_task(
            s,
            prompt=prompt,
            title=f"{agent.name}: {prompt.splitlines()[0][:60]}",
            project_id=project_id,
            model=await tiers.agent_model(s, agent),
            max_turns=agent.max_turns,
            max_budget_usd=agent.max_budget_usd,
            priority=agent.priority,
            tags=(agent.tags or []) + ["agent"],
            agent_id=agent.id,
            system_prompt=agent.system_prompt or None,
            requires_approval=agent.requires_approval,
            allow_remote=agent.allow_remote,
            read_only=agent.read_only,
            input_files=[f.model_dump() for f in payload.input_files],
            parent_task_id=payload.parent_task_id,
            created_by=await _creator(s, request, payload.parent_task_id),
        )
        await s.commit()
        await s.refresh(task)
        out = TaskOut.model_validate(task)
    from ..constants import Status

    if out.status != Status.AWAITING_APPROVAL:
        await request.app.state.worker.submit(out.id, out.priority, out.created_at)
    return out


async def _require_dispatch_target(s, p, project_id: str) -> None:
    project = await s.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    if await project_role(s, p, project_id) is None:
        # A human caller can't see the project: don't reveal it (404). An agent
        # token gets the named refusal so its coordinator can explain it.
        if not p.is_agent:
            raise HTTPException(404, "Project not found")
        raise HTTPException(403, f"{p.user.email} is not a contributor on project {project.name}")


@router.post("/dispatch", response_model=list[TaskOut], status_code=201)
async def dispatch(payload: DispatchRequest, request: Request):
    """Coordinator fan-out: create one child task per item, each delegated to the
    named agent and linked to the parent task, then submit them (worker runs them
    in parallel). Reuses the normal agent-run path — no separate orchestration."""
    from ..constants import Status

    # An agent-task token may only fan out under its own task.
    p = request.state.principal
    if p.is_agent and payload.parent_task_id != p.token.task_id:
        raise HTTPException(403, "Token may only dispatch under its own task")

    async with SessionLocal() as s:
        await _require_parent(s, request, payload.parent_task_id)
        # Resolve each target agent by id or exact name.
        all_agents = (await s.execute(select(Agent))).scalars().all()
        by_id = {a.id: a for a in all_agents}
        by_name = {a.name: a for a in all_agents}
        creator = await _creator(s, request, payload.parent_task_id)
        targets = []
        for item in payload.items:
            agent = by_id.get(item.agent) or by_name.get(item.agent)
            if agent is None:
                raise HTTPException(404, f"Agent not found: {item.agent}")
            targets.append((item, agent, agent.project_id or payload.project_id))
        # Every child must land where the caller (for a token: its owner) may
        # contribute; check them all before creating any.
        for pid in dict.fromkeys(t[2] for t in targets if t[2]):
            await _require_dispatch_target(s, p, pid)
        outs = []
        for item, agent, project_id in targets:
            task = await build_task(
                s,
                prompt=item.prompt,
                title=f"{agent.name}: {item.prompt.splitlines()[0][:60]}",
                project_id=project_id,
                model=await tiers.agent_model(s, agent),
                max_turns=agent.max_turns,
                max_budget_usd=agent.max_budget_usd,
                priority=agent.priority,
                tags=(agent.tags or []) + ["agent", "delegated"],
                agent_id=agent.id,
                system_prompt=agent.system_prompt or None,
                requires_approval=agent.requires_approval,
                allow_remote=agent.allow_remote,
                read_only=agent.read_only,
                parent_task_id=payload.parent_task_id,
                created_by=creator,
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
        await require_project_role(s, request.state.principal, project_id)
        tasks = []
        for p in prompts:
            tasks.append(
                await build_task(
                    s,
                    prompt=p,
                    title=f"{agent.name}: {p.splitlines()[0][:60]}",
                    project_id=project_id,
                    model=await tiers.agent_model(s, agent),
                    max_turns=agent.max_turns,
                    max_budget_usd=agent.max_budget_usd,
                    priority=agent.priority,
                    tags=(agent.tags or []) + ["agent", "bulk"],
                    agent_id=agent.id,
                    system_prompt=agent.system_prompt or None,
                    requires_approval=agent.requires_approval,
                    allow_remote=agent.allow_remote,
                    read_only=agent.read_only,
                    created_by=request.state.principal.user.id,
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
