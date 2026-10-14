import asyncio

from routers.anime import anime_page


def test_anime_page_redirects_to_enma():
    response = asyncio.run(anime_page())

    assert response.status_code == 302
    assert response.headers["location"] == "https://www.enma.lol"
