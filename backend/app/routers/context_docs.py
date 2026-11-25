from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_, select, true

from .. import context_store, ui_checks
from ..access import ALL, require_project_role, visible_project_ids
from ..auth import Principal, current_principal, ensure_admin
from ..database import SessionLocal
from ..models import ContextDoc, Project
from pydantic import BaseModel

from ..schemas import ContextDocCreate, ContextDocOut, ContextDocUpdate

router = APIRouter(prefix="/context-docs", tags=["context-docs"])

_SCOPES = ("global", "project")


async def _visibility(s, p):
    """Global docs: everyone. Project docs: any role on that project."""
    visible = await visible_project_ids(s, p)
    if visible is ALL:
        return true()
    return or_(ContextDoc.scope != "project", ContextDoc.project_id.in_(visible))


async def _visible_doc_or_404(s, p, doc_id: str) -> ContextDoc:
    doc = (
        await s.execute(
            select(ContextDoc).where(ContextDoc.id == doc_id, await _visibility(s, p))
        )
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(404, "Doc not found")
    return doc


async def _require_write(s, p, scope: str, project_id: str | None) -> None:
    """Global docs reach every task → admin. Project docs → that project's
    maintainer (404 when the project isn't visible, 403 when the role is lower)."""
    if scope == "project":
        await require_project_role(s, p, project_id, "maintainer")
    else:
        ensure_admin(p)


async def _validate(s, scope: str, project_id: str | None) -> str | None:
    if scope not in ("global", "project"):
        raise HTTPException(400, "scope must be 'global' or 'project'")
    if scope == "global":
        return None
    if not project_id:
        raise HTTPException(400, "project scope requires a project_id")
    if await s.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    return project_id


async def _check_unique(s, path: str, scope: str, project_id: str | None, exclude_id: str | None = None):
    same_project = (
        ContextDoc.project_id.is_(None) if project_id is None else ContextDoc.project_id == project_id
    )
    q = select(ContextDoc).where(ContextDoc.path == path, ContextDoc.scope == scope, same_project)
    for d in (await s.execute(q)).scalars().all():
        if d.id != exclude_id:
            raise HTTPException(409, f"A {scope} doc already exists at '{path}'")


def _norm(path: str) -> str:
    try:
        return context_store.normalize_path(path)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("", response_model=list[ContextDocOut])
async def list_docs(
    request: Request, project_id: str | None = None, scope: str | None = None
):
    async with SessionLocal() as s:
        q = (
            select(ContextDoc)
            .where(await _visibility(s, request.state.principal))
            .order_by(ContextDoc.path)
        )
        if scope:
            q = q.where(ContextDoc.scope == scope)
        if project_id:
            q = q.where(ContextDoc.project_id == project_id)
        rows = (await s.execute(q)).scalars().all()
    return [ContextDocOut.model_validate(r) for r in rows]


@router.get("/preview")
async def preview_index(request: Request, project_id: str | None = None):
    """What a task (optionally in a project) would receive: the L0 index and the
    docs that apply after project-overrides-global resolution."""
    if project_id:
        async with SessionLocal() as s:
            await require_project_role(s, request.state.principal, project_id)
    docs = await context_store.docs_for(project_id)
    root = context_store.context_dir("<task-id>")
    return {
        "docs": [{"id": d.id, "path": d.path, "scope": d.scope} for d in docs],
        "index": context_store.build_index(docs, root) if docs else "",
    }


class TemplateIn(BaseModel):
    project_id: str


@router.post("/templates/brand", response_model=ContextDocOut, status_code=201)
async def create_brand_template(payload: TemplateIn, p: Principal = Depends(current_principal)):
    """Start a project's Brand guide from the template (phase 7f). 409 when brand/brand.md already exists."""
    async with SessionLocal() as s:
        await _require_write(s, p, "project", payload.project_id)
        project_id = await _validate(s, "project", payload.project_id)
        await _check_unique(s, ui_checks.BRAND_PATH, "project", project_id)
        doc = ContextDoc(path=ui_checks.BRAND_PATH, title=ui_checks.BRAND_TITLE, abstract=ui_checks.BRAND_ABSTRACT,
                         body=ui_checks.BRAND_BODY, scope="project", project_id=project_id, enabled=True,
                         created_by=p.user.id)
        s.add(doc)
        await s.commit()
        await s.refresh(doc)
        return ContextDocOut.model_validate(doc)


@router.get("/{doc_id}", response_model=ContextDocOut)
async def get_doc(doc_id: str, request: Request):
    async with SessionLocal() as s:
        doc = await _visible_doc_or_404(s, request.state.principal, doc_id)
        return ContextDocOut.model_validate(doc)


@router.post("", response_model=ContextDocOut, status_code=201)
async def create_doc(payload: ContextDocCreate, p: Principal = Depends(current_principal)):
    if payload.scope not in _SCOPES:
        raise HTTPException(400, "scope must be 'global' or 'project'")
    path = _norm(payload.path)
    async with SessionLocal() as s:
        await _require_write(s, p, payload.scope, payload.project_id)
        project_id = await _validate(s, payload.scope, payload.project_id)
        await _check_unique(s, path, payload.scope, project_id)
        doc = ContextDoc(
            path=path,
            title=(payload.title or "").strip() or context_store.derive_title(path),
            abstract=((payload.abstract or "").strip() or context_store.derive_abstract(payload.body))[
                : context_store.ABSTRACT_MAX
            ],
            body=payload.body,
            scope=payload.scope,
            project_id=project_id,
            enabled=payload.enabled,
            created_by=p.user.id,
        )
        s.add(doc)
        await s.commit()
        await s.refresh(doc)
        return ContextDocOut.model_validate(doc)


@router.patch("/{doc_id}", response_model=ContextDocOut)
async def update_doc(
    doc_id: str, payload: ContextDocUpdate, p: Principal = Depends(current_principal)
):
    data = payload.model_dump(exclude_unset=True)
    async with SessionLocal() as s:
        doc = await _visible_doc_or_404(s, p, doc_id)
        await _require_write(s, p, doc.scope, doc.project_id)
        new_scope = data.get("scope", doc.scope)
        new_pid = data.get("project_id", doc.project_id) if new_scope == "project" else None
        if new_scope in _SCOPES and (new_scope, new_pid) != (doc.scope, doc.project_id):
            await _require_write(s, p, new_scope, new_pid)  # moving needs both places
        if "path" in data:
            data["path"] = _norm(data["path"])
        if "abstract" in data:
            data["abstract"] = (data["abstract"] or "").strip()[: context_store.ABSTRACT_MAX]
        if "title" in data:
            data["title"] = (data["title"] or "").strip() or context_store.derive_title(
                data.get("path", doc.path)
            )
        scope = data.get("scope", doc.scope)
        project_id = await _validate(s, scope, data.get("project_id", doc.project_id))
        data["project_id"] = project_id
        await _check_unique(s, data.get("path", doc.path), scope, project_id, exclude_id=doc.id)
        for k, v in data.items():
            setattr(doc, k, v)
        if not doc.abstract:
            doc.abstract = context_store.derive_abstract(doc.body)
        await s.commit()
        await s.refresh(doc)
        return ContextDocOut.model_validate(doc)


@router.delete("/{doc_id}", status_code=204)
async def delete_doc(doc_id: str, p: Principal = Depends(current_principal)):
    async with SessionLocal() as s:
        doc = await _visible_doc_or_404(s, p, doc_id)
        await _require_write(s, p, doc.scope, doc.project_id)
        await s.delete(doc)
        await s.commit()
