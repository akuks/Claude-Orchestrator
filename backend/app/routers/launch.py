"""Project launch list (phase 5b): before/after items numbered #1..., a launch date and critical paths.
Anyone who can see the project reads. Contributors add items and start/finish/drop them; maintainers (and
admins) also edit titles, move sections, reopen, delete, and set the date and critical paths."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..access import require_project_role
from ..database import SessionLocal
from ..launch import store
from ..launch.validate import SECTIONS, STATUSES, clean_paths, clean_title, title_key
from ..models import LaunchItem, ProjectLaunch, Task, User
from ..schemas import LaunchItemIn, LaunchItemOut, LaunchItemPatch, LaunchOut, LaunchPut, LaunchSettingsOut

router = APIRouter(prefix="/projects/{project_id}/launch", tags=["launch"])

STALE = "changed by someone else — reload"
DUPLICATE = "an open item with this title already exists"
# moves a contributor may make; maintainers may make any move
_CONTRIBUTOR_MOVES = {("open", "started"), ("started", "done")}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _item_out(i: LaunchItem, names: dict) -> LaunchItemOut:
    return LaunchItemOut(
        id=i.id, number=i.number, title=i.title, section=i.section, status=i.status, added_at=i.added_at,
        started_at=i.started_at, done_at=i.done_at, added_by=i.added_by, added_by_name=names.get(i.added_by),
        version=i.version)


async def _names(s, rows) -> dict:
    ids = {i.added_by for i in rows if i.added_by}
    if not ids:
        return {}
    users = (await s.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {u.id: (u.name or u.email) for u in users}


async def _full(s, project_id: str) -> LaunchOut:
    row = await store.get_or_none(s, project_id)
    if row is not None:
        await s.refresh(row)
    items = await store.launch_items_for(s, project_id)
    for i in items:
        await s.refresh(i)
    names = await _names(s, items)
    return LaunchOut(
        launch=LaunchSettingsOut(launch_date=row.launch_date, critical_paths=list(row.critical_paths or []),
                                 version=row.version) if row is not None else None,
        items=[_item_out(i, names) for i in items],
        all_linked_done=await store.all_linked_done(s, project_id))


async def _get_item(s, project_id: str, number: int) -> LaunchItem:
    i = (await s.execute(select(LaunchItem).where(
        LaunchItem.project_id == project_id, LaunchItem.number == number))).scalar_one_or_none()
    if i is None:
        raise HTTPException(404, "Launch item not found")
    return i


async def _title_taken(s, project_id: str, title: str, *, except_id: str | None = None) -> bool:
    """Another live (open or started) item with this title, case-insensitive after trim."""
    rows = (await s.execute(select(LaunchItem.id, LaunchItem.title).where(
        LaunchItem.project_id == project_id, LaunchItem.status.in_(("open", "started"))))).all()
    key = title_key(title)
    return any(i != except_id and title_key(t) == key for i, t in rows)


def _check_section(section: str) -> None:
    if section not in SECTIONS:
        raise HTTPException(422, f"section must be one of: {', '.join(SECTIONS)}")


def _clean_title(text) -> str:
    try:
        return clean_title(text)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("", response_model=LaunchOut)
async def get_launch(project_id: str, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        return await _full(s, project_id)


@router.put("", response_model=LaunchOut)
async def put_launch(project_id: str, payload: LaunchPut, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id, "maintainer")
        try:
            paths = clean_paths(payload.critical_paths)
        except ValueError as e:
            raise HTTPException(422, str(e))
        row = await store.get_or_none(s, project_id)
        if row is None:
            if payload.version not in (None, 0):
                raise HTTPException(409, STALE)
            s.add(ProjectLaunch(project_id=project_id, launch_date=payload.launch_date, critical_paths=paths,
                                version=1, updated_at=_now()))
            try:
                await s.commit()
            except IntegrityError:  # two first writes at once: the loser reloads
                await s.rollback()
                raise HTTPException(409, STALE)
        else:
            if payload.version is None or payload.version != row.version:
                raise HTTPException(409, STALE)
            res = await s.execute(update(ProjectLaunch).where(
                ProjectLaunch.project_id == project_id, ProjectLaunch.version == payload.version).values(
                launch_date=payload.launch_date, critical_paths=paths, version=ProjectLaunch.version + 1,
                updated_at=_now()))
            if res.rowcount != 1:
                await s.rollback()
                raise HTTPException(409, STALE)
            await s.commit()
        return await _full(s, project_id)


@router.post("/items", response_model=LaunchItemOut, status_code=201)
async def add_item(project_id: str, payload: LaunchItemIn, request: Request):
    principal = request.state.principal
    async with SessionLocal() as s:
        await require_project_role(s, principal, project_id)
        title = _clean_title(payload.title)
        _check_section(payload.section)
        if await _title_taken(s, project_id, title):
            raise HTTPException(422, DUPLICATE)
        n = await store.next_number(s, project_id)  # takes the project row lock until commit
        item = LaunchItem(project_id=project_id, number=n, title=title, section=payload.section,
                          status="open", added_at=_now(), added_by=principal.user.id)
        s.add(item)
        await s.commit()
        await s.refresh(item)
        return _item_out(item, await _names(s, [item]))


@router.patch("/items/{number}", response_model=LaunchItemOut)
async def patch_item(project_id: str, number: int, payload: LaunchItemPatch, request: Request):
    async with SessionLocal() as s:
        role = await require_project_role(s, request.state.principal, project_id)
        maintainer = role == "maintainer"
        body = payload.model_dump(exclude_unset=True)
        version = body.pop("version")
        body = {k: v for k, v in body.items() if v is not None}
        item = await _get_item(s, project_id, number)
        values: dict = {}
        if ("title" in body or "section" in body) and not maintainer:
            raise HTTPException(403, "Insufficient role")
        if "title" in body:
            values["title"] = _clean_title(body["title"])
        if "section" in body:
            _check_section(body["section"])
            values["section"] = body["section"]
        new_status = item.status
        if "status" in body:
            new_status = body["status"]
            if new_status not in STATUSES:
                raise HTTPException(422, f"status must be one of: {', '.join(STATUSES)}")
            if new_status != item.status:
                allowed = maintainer or new_status == "dropped" or (item.status, new_status) in _CONTRIBUTOR_MOVES
                if not allowed:
                    if item.status in ("done", "dropped") and new_status == "open":
                        raise HTTPException(403, "Insufficient role")
                    raise HTTPException(422, f"an item cannot move from {item.status} to {new_status}")
                now = _now()
                values["status"] = new_status
                if new_status == "started":
                    values["started_at"] = now
                    values["done_at"] = None
                elif new_status == "done":
                    values["done_at"] = now
                elif new_status == "open":
                    values["done_at"] = None
        if not values:
            if version != item.version:
                raise HTTPException(409, STALE)
            return _item_out(item, await _names(s, [item]))
        # a title edit or a reopen must not duplicate another live item
        title = values.get("title", item.title)
        if ("title" in values or values.get("status") == "open") and \
                await _title_taken(s, project_id, title, except_id=item.id):
            raise HTTPException(422, DUPLICATE)
        if version != item.version:
            raise HTTPException(409, STALE)
        res = await s.execute(update(LaunchItem).where(
            LaunchItem.id == item.id, LaunchItem.version == version).values(
            version=LaunchItem.version + 1, **values))
        if res.rowcount != 1:
            await s.rollback()
            raise HTTPException(409, STALE)
        await s.commit()
        await s.refresh(item)
        return _item_out(item, await _names(s, [item]))


@router.delete("/items/{number}", status_code=204)
async def delete_item(project_id: str, number: int, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id, "maintainer")
        item = await _get_item(s, project_id, number)
        await s.execute(update(Task).where(Task.launch_item_id == item.id).values(launch_item_id=None))
        await s.delete(item)
        await s.commit()
