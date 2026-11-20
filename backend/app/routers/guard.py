"""co-guard server routes (Task 3: identity only; Task 4 adds approvals)."""

import re

from fastapi import APIRouter, HTTPException, Query

from ..guard import runtime as guard_runtime

router = APIRouter(prefix="/guard", tags=["guard"])

_CHALLENGE = re.compile(r"[0-9a-f]{16,128}")


@router.get("/whoami")
async def whoami(challenge: str = Query(...)):
    """Ruling R22: proves this is the orchestrator itself (HMAC with a per-boot
    secret held only in server memory). No auth: it reveals nothing - the
    answer is useless without the secret, and the worker checks it before
    every task so an agent's listener on the approval address is caught."""
    if not _CHALLENGE.fullmatch(challenge):
        raise HTTPException(status_code=422, detail="bad challenge")
    return {"proof": guard_runtime.whoami_proof(challenge)}
