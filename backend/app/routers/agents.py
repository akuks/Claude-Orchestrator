from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from ..constants import VALID_MODELS
from ..database import SessionLocal
from ..models import Agent, Project
from ..schemas import AgentCreate, AgentOut, AgentRun, AgentUpdate, TaskOut
from ..task_service import build_task

router = APIRouter(prefix="/agents", tags=["agents"])


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
        )
        await s.commit()
        await s.refresh(task)
        out = TaskOut.model_validate(task)
    from ..constants import Status

    if out.status != Status.AWAITING_APPROVAL:
        await request.app.state.worker.submit(out.id, out.priority, out.created_at)
    return out
