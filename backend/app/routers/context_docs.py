from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from .. import context_store
from ..auth import Principal, current_principal, ensure_admin
from ..database import SessionLocal
from ..models import ContextDoc, Project
from ..schemas import ContextDocCreate, ContextDocOut, ContextDocUpdate

router = APIRouter(prefix="/context-docs", tags=["context-docs"])


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
async def list_docs(project_id: str | None = None, scope: str | None = None):
    async with SessionLocal() as s:
        q = select(ContextDoc).order_by(ContextDoc.path)
        if scope:
            q = q.where(ContextDoc.scope == scope)
        if project_id:
            q = q.where(ContextDoc.project_id == project_id)
        rows = (await s.execute(q)).scalars().all()
    return [ContextDocOut.model_validate(r) for r in rows]


@router.get("/preview")
async def preview_index(project_id: str | None = None):
    """What a task (optionally in a project) would receive: the L0 index and the
    docs that apply after project-overrides-global resolution."""
    docs = await context_store.docs_for(project_id)
    root = context_store.context_dir("<task-id>")
    return {
        "docs": [{"id": d.id, "path": d.path, "scope": d.scope} for d in docs],
        "index": context_store.build_index(docs, root) if docs else "",
    }


@router.get("/{doc_id}", response_model=ContextDocOut)
async def get_doc(doc_id: str):
    async with SessionLocal() as s:
        doc = await s.get(ContextDoc, doc_id)
        if doc is None:
            raise HTTPException(404, "Doc not found")
        return ContextDocOut.model_validate(doc)


@router.post("", response_model=ContextDocOut, status_code=201)
async def create_doc(payload: ContextDocCreate, p: Principal = Depends(current_principal)):
    if payload.scope == "global":
        ensure_admin(p)  # global docs reach every task
    path = _norm(payload.path)
    async with SessionLocal() as s:
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
        doc = await s.get(ContextDoc, doc_id)
        if doc is None:
            raise HTTPException(404, "Doc not found")
        if doc.scope == "global" or data.get("scope") == "global":
            ensure_admin(p)
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
        doc = await s.get(ContextDoc, doc_id)
        if doc is None:
            raise HTTPException(404, "Doc not found")
        if doc.scope == "global":
            ensure_admin(p)
        await s.delete(doc)
        await s.commit()
