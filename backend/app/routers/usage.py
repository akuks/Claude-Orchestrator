from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import func, select

from ..database import SessionLocal
from ..models import Project, Task

router = APIRouter(prefix="/usage", tags=["usage"])

_COST = func.coalesce(func.sum(Task.total_cost_usd), 0.0)
_IN = func.coalesce(func.sum(Task.input_tokens), 0)
_OUT = func.coalesce(func.sum(Task.output_tokens), 0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _totals(s, since: datetime | None) -> dict:
    q = select(_COST, _IN, _OUT, func.count()).where(Task.total_cost_usd.isnot(None))
    if since is not None:
        q = q.where(Task.completed_at >= since)
    cost, tin, tout, n = (await s.execute(q)).one()
    return {
        "cost_usd": round(float(cost or 0), 4),
        "input_tokens": int(tin or 0),
        "output_tokens": int(tout or 0),
        "tokens": int((tin or 0) + (tout or 0)),
        "tasks": int(n or 0),
    }


@router.get("/summary")
async def summary():
    now = _now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    async with SessionLocal() as s:
        return {
            "today": await _totals(s, today),
            "last_7d": await _totals(s, now - timedelta(days=7)),
            "last_30d": await _totals(s, now - timedelta(days=30)),
            "all_time": await _totals(s, None),
        }


@router.get("/timeseries")
async def timeseries(days: int = 30):
    since = _now() - timedelta(days=days)
    day = func.date(Task.completed_at)
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(day, _COST, _IN + _OUT)
                .where(Task.completed_at >= since, Task.total_cost_usd.isnot(None))
                .group_by(day)
                .order_by(day)
            )
        ).all()
    return [
        {"date": d, "cost_usd": round(float(c or 0), 4), "tokens": int(t or 0)}
        for d, c, t in rows
    ]


@router.get("/by-project")
async def by_project():
    # Group by project name so legacy tasks (name set, no project_id) merge.
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(Task.project, _COST, _IN + _OUT, func.count())
                .where(Task.total_cost_usd.isnot(None))
                .group_by(Task.project)
            )
        ).all()
        budgets = {
            p.name: p.budget_usd
            for p in (await s.execute(select(Project))).scalars().all()
        }
    out = []
    for pname, cost, toks, n in rows:
        name = pname or "(no project)"
        budget = budgets.get(pname)
        spent = round(float(cost or 0), 4)
        out.append(
            {
                "project": name,
                "cost_usd": spent,
                "tokens": int(toks or 0),
                "tasks": int(n or 0),
                "budget_usd": budget,
                "over_budget": bool(budget and spent > budget),
            }
        )
    out.sort(key=lambda x: x["cost_usd"], reverse=True)
    return out


@router.get("/by-model")
async def by_model():
    label = func.coalesce(func.nullif(Task.model_used, ""), Task.model, "default")
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(label, _COST, _IN + _OUT, func.count())
                .where(Task.total_cost_usd.isnot(None))
                .group_by(label)
            )
        ).all()
    out = [
        {
            "model": m or "default",
            "cost_usd": round(float(c or 0), 4),
            "tokens": int(t or 0),
            "tasks": int(n or 0),
        }
        for m, c, t, n in rows
    ]
    out.sort(key=lambda x: x["cost_usd"], reverse=True)
    return out
