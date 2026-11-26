"""Admin-only user management. No hard delete: users are disabled so ownership
history is preserved."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import auth
from ..database import SessionLocal
from ..editor_mcp import keys as editor_keys
from ..models import User
from ..schemas import PasswordResetIn, UserBrief, UserCreate, UserOut, UserPatch

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(auth.require_admin)])

# Any logged-in user may read the directory (to pick team/project members).
directory_router = APIRouter(prefix="/users", tags=["users"])

ROLES = ("admin", "member")


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise HTTPException(400, f"role must be one of: {', '.join(ROLES)}")


def _check_password(pw: str) -> None:
    try:
        auth.validate_password(pw)
    except ValueError as e:
        raise HTTPException(400, str(e))


async def _get(s, user_id: str) -> User:
    u = await s.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    return u


@router.get("", response_model=list[UserOut])
async def list_users():
    async with SessionLocal() as s:
        rows = (await s.execute(select(User).order_by(User.created_at))).scalars().all()
        return list(rows)


@router.post("", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate):
    _check_role(body.role)
    _check_password(body.password)
    email = auth.normalize_email(body.email)
    if "@" not in email:
        raise HTTPException(400, "A valid email is required")
    async with SessionLocal() as s:
        if (await s.execute(select(User.id).where(User.email == email))).first():
            raise HTTPException(409, "A user with that email already exists")
        u = User(
            email=email,
            name=body.name,
            password_hash=auth.hash_password(body.password),
            role=body.role,
        )
        s.add(u)
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise HTTPException(409, "A user with that email already exists")
        await s.refresh(u)
        return u


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: str, body: UserPatch):
    if body.role is not None:
        _check_role(body.role)
    async with SessionLocal() as s:
        u = await _get(s, user_id)
        new_role = body.role if body.role is not None else u.role
        new_active = body.active if body.active is not None else u.active
        was_active_admin = u.role == "admin" and u.active
        if was_active_admin and not (new_role == "admin" and new_active):
            others = (
                await s.execute(
                    select(func.count())
                    .select_from(User)
                    .where(User.role == "admin", User.active.is_(True), User.id != u.id)
                )
            ).scalar_one()
            if others == 0:
                raise HTTPException(400, "Cannot remove the last active admin")
        if body.name is not None:
            u.name = body.name
        u.role = new_role
        disabling = u.active and not new_active
        u.active = new_active
        if disabling:
            await auth.revoke_user_sessions(s, u.id)
            await editor_keys.revoke_user_keys(s, u.id)  # re-enabling never brings old editor keys back
        await s.commit()
        await s.refresh(u)
        return u


@router.post("/{user_id}/reset-password", status_code=204)
async def reset_password(user_id: str, body: PasswordResetIn):
    _check_password(body.password)
    async with SessionLocal() as s:
        u = await _get(s, user_id)
        u.password_hash = auth.hash_password(body.password)
        await auth.revoke_user_sessions(s, u.id)
        await editor_keys.revoke_user_keys(s, u.id)  # a key minted by whoever had the account goes too
        await s.commit()


@directory_router.get("/directory", response_model=list[UserBrief])
async def user_directory():
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(User).where(User.active.is_(True)).order_by(User.name, User.email)
            )
        ).scalars().all()
        return list(rows)
