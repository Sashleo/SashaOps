"""
routers/newsfeed.py - Placeholder page. Built in Phase 5.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from security.auth import require_session

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def page(request: Request):
    require_session(request)
    return templates.TemplateResponse("newsfeed.html", {"request": request})
