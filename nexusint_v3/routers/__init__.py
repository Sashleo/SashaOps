"""
routers/__init__.py — Router exports for app.py.
"""

from .auth import router as auth_router
from .dashboard import router as dashboard_router
from .intelligence import router as intelligence_router
from .feeds import router as feeds_router
from .markets import router as markets_router
from .documents import router as documents_router
from .reports import router as reports_router
from .memory import router as memory_router
from .settings import router as settings_router

__all__ = [
    "auth_router",
    "dashboard_router",
    "intelligence_router",
    "feeds_router",
    "markets_router",
    "documents_router",
    "reports_router",
    "memory_router",
    "settings_router",
]
