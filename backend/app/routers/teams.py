"""Teams: readable by any user, writable by admins only."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from .. import auth
from ..database import SessionLocal
from ..models import ProjectMember, Team, TeamMember, User
from ..schemas import TeamIn, TeamMembersIn, TeamOut, UserBrief

router = APIRouter(prefix="/teams", tags=["teams"])


async def _out(s, teams: list[Team]) -> list[TeamOut]:
    ids = [t.id for t in teams]
    by_team: dict[str, list[UserBrief]] = {i: [] for i in ids}
    if ids:
        rows = (
            await s.execute(
                select(TeamMember.team_id, User)
                .join(User, User.id == TeamMember.user_id)
                .where(TeamMember.team_id.in_(ids))
                .order_by(User.name, User.email)
            )
        ).all()
        for tid, u in rows:
            by_team[tid].append(UserBrief.model_validate(u))
    return [TeamOut(id=t.id, name=t.name, members=by_team[t.id]) for t in teams]


async def _get(s, team_id: str) -> Team:
    t = await s.get(Team, team_id)
    if not t:
        raise HTTPException(404, "Team not found")
    return t


@router.get("", response_model=list[TeamOut])
async def list_teams():
    async with SessionLocal() as s:
        teams = (await s.execute(select(Team).order_by(Team.name))).scalars().all()
        return await _out(s, list(teams))


@router.post("", response_model=TeamOut, status_code=201)
async def create_team(body: TeamIn, request: Request):
    auth.ensure_admin(request.state.principal)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    async with SessionLocal() as s:
        if (await s.execute(select(Team.id).where(Team.name == name))).first():
            raise HTTPException(409, "A team with that name already exists")
        t = Team(name=name)
        s.add(t)
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise HTTPException(409, "A team with that name already exists")
        return (await _out(s, [t]))[0]


@router.patch("/{team_id}", response_model=TeamOut)
async def rename_team(team_id: str, body: TeamIn, request: Request):
    auth.ensure_admin(request.state.principal)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    async with SessionLocal() as s:
        t = await _get(s, team_id)
        clash = (
            await s.execute(select(Team.id).where(Team.name == name, Team.id != t.id))
        ).first()
        if clash:
            raise HTTPException(409, "A team with that name already exists")
        t.name = name
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise HTTPException(409, "A team with that name already exists")
        return (await _out(s, [t]))[0]


@router.delete("/{team_id}", status_code=204)
async def delete_team(team_id: str, request: Request):
    auth.ensure_admin(request.state.principal)
    async with SessionLocal() as s:
        t = await _get(s, team_id)
        await s.execute(delete(TeamMember).where(TeamMember.team_id == t.id))
        await s.execute(
            delete(ProjectMember).where(
                ProjectMember.subject_type == "team", ProjectMember.subject_id == t.id
            )
        )
        await s.delete(t)
        await s.commit()


@router.put("/{team_id}/members", response_model=TeamOut)
async def set_team_members(team_id: str, body: TeamMembersIn, request: Request):
    auth.ensure_admin(request.state.principal)
    wanted = list(dict.fromkeys(body.user_ids))
    async with SessionLocal() as s:
        t = await _get(s, team_id)
        if wanted:
            found = set(
                (await s.execute(select(User.id).where(User.id.in_(wanted)))).scalars()
            )
            missing = [u for u in wanted if u not in found]
            if missing:
                raise HTTPException(400, f"Unknown user ids: {', '.join(missing)}")
        await s.execute(delete(TeamMember).where(TeamMember.team_id == t.id))
        for uid in wanted:
            s.add(TeamMember(team_id=t.id, user_id=uid))
        await s.commit()
        return (await _out(s, [t]))[0]
