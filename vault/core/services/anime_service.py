import httpx


class AnimeService:
    def __init__(self) -> None:
        self.base_url = "https://api.jikan.moe/v4"

    async def get_current_season(self) -> list[dict]:
        data = await self._get("/seasons/now")
        return [self._format_item(item) for item in data.get("data", [])]

    async def get_top(self) -> list[dict]:
        data = await self._get("/top/anime")
        return [self._format_item(item) for item in data.get("data", [])]

    async def search(self, query: str) -> list[dict]:
        data = await self._get("/anime", params={"q": query, "limit": 24})
        return [self._format_item(item) for item in data.get("data", [])]

    async def get_details(self, mal_id: int) -> dict:
        data = await self._get(f"/anime/{mal_id}/full")
        return self._format_detail(data.get("data", {}))

    async def _get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            return response.json()

    def _format_item(self, item: dict) -> dict:
        return {
            "id": item.get("mal_id"),
            "title": item.get("title"),
            "synopsis": item.get("synopsis"),
            "poster_url": (((item.get("images") or {}).get("jpg") or {}).get("large_image_url")
                           or ((item.get("images") or {}).get("jpg") or {}).get("image_url")),
            "score": item.get("score"),
            "episodes": item.get("episodes"),
            "year": item.get("year"),
            "season": item.get("season"),
            "status": item.get("status"),
            "trailer_url": ((item.get("trailer") or {}).get("embed_url") or (item.get("trailer") or {}).get("url")),
        }

    def _format_detail(self, item: dict) -> dict:
        detail = self._format_item(item)
        detail["streaming"] = item.get("streaming") or []
        detail["external"] = item.get("external") or []
        detail["studios"] = [studio.get("name") for studio in item.get("studios", [])]
        detail["genres"] = [genre.get("name") for genre in item.get("genres", [])]
        detail["url"] = item.get("url")
        return detail


anime_service = AnimeService()
