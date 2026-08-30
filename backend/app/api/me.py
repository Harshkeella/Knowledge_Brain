"""The signed-in user: who they are and what they have used.

The frontend renders "2.1 GB / 5 GB" from this, but it is a display of a
decision already made server-side -- `ingestion` enforces the same numbers
before a byte is indexed, so a client that lies about the remaining space only
lies to itself.
"""

from fastapi import APIRouter, Depends

from app.core.auth import User, get_current_user
from app.services import llm_limits, manifest

router = APIRouter(
    prefix="/api/v1/me",
    tags=["me"],
    dependencies=[Depends(get_current_user)],
)


@router.get("")
async def get_me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email}


@router.get("/usage")
async def get_usage():
    await manifest.init_db()
    return {**await manifest.usage(), "llm": await llm_limits.summary()}
