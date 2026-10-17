"""
routers/__init__.py - Router exports for app.py.
"""

from .auth import router as auth_router
from .dashboard import router as dashboard_router
from .psx import router as psx_router
from .private import router as private_router
from .newsfeed import router as newsfeed_router
from .documents import router as documents_router
from .reports import router as reports_router
from .settings import router as settings_router

__all__ = [
    "auth_router",
    "dashboard_router",
    "psx_router",
    "private_router",
    "newsfeed_router",
    "documents_router",
    "reports_router",
    "settings_router",
]
