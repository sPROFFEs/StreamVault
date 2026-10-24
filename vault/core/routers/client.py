"""Minimal authenticated bootstrap and About for browser and installed clients."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from services.client_release import REPOSITORY_URL, latest_release
from shared_templates import templates
from version import VERSION

router = APIRouter(tags=['Client'])


@router.get('/api/mobile/bootstrap')
def bootstrap(request: Request):
    user = request.state.user
    return JSONResponse({'protocol': 1, 'username': user.username, 'is_admin': bool(user.is_admin),
                         'can_download': bool(user.is_admin or user.can_download),
                         'language': 'es' if request.cookies.get('lang') == 'es' else 'en',
                         'server_version': VERSION}, headers={'Cache-Control': 'private, no-store'})


@router.get('/api/about')
async def about_info():
    return JSONResponse({'server_version': VERSION, 'repository': REPOSITORY_URL,
                         'release': await latest_release()}, headers={'Cache-Control': 'private, no-store'})


@router.get('/about')
def about_page(request: Request):
    return templates.TemplateResponse(request, 'about.html', {'request': request},
                                      headers={'Cache-Control': 'private, no-store'})
