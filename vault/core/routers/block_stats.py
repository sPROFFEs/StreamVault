"""Block-stats router."""
from fastapi import APIRouter

from services.block_list import stats, reset_session

router = APIRouter(prefix="/api/block-stats", tags=["BlockStats"])


@router.get("")
async def get_stats():
    return stats()


@router.post("/reset-session")
async def reset():
    reset_session()
    return {"ok": True}
