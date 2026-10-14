from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from services.anime_service import anime_service

router = APIRouter(prefix="/anime", tags=["Anime"])


@router.get("")
async def anime_page():
    """Match the public 1Flex navigation: Anime lives on Enma."""
    return RedirectResponse("https://www.enma.lol", status_code=302)


@router.get("/api/season")
async def anime_season():
    return await anime_service.get_current_season()


@router.get("/api/top")
async def anime_top():
    return await anime_service.get_top()


@router.get("/api/search")
async def anime_search(q: str):
    if not q.strip():
        return []
    return await anime_service.search(q.strip())


@router.get("/api/{mal_id}")
async def anime_detail(mal_id: int):
    detail = await anime_service.get_details(mal_id)
    if not detail:
        raise HTTPException(404, "Anime no encontrado")
    return detail
