"""
routers/partials.py — HTMX partial/fragment endpoints.

Moved from routers/intelligence.py to keep the main router
focused on page-level routes and stage orchestration only.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

