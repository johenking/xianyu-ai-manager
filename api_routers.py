"""Domain routers used by the compatibility API module."""

from fastapi import APIRouter, FastAPI

from invite_bridge import invite_bridge_router


auth_router = APIRouter(tags=["auth"])
accounts_router = APIRouter(tags=["accounts"])
ai_router = APIRouter(tags=["ai"])
orders_router = APIRouter(tags=["orders"])
settings_router = APIRouter(tags=["settings"])
content_router = APIRouter(tags=["content"])
admin_router = APIRouter(tags=["admin"])
system_router = APIRouter(tags=["system"])
frontend_router = APIRouter(include_in_schema=False)


DOMAIN_ROUTERS = {
    "auth": auth_router,
    "accounts": accounts_router,
    "ai": ai_router,
    "orders": orders_router,
    "settings": settings_router,
    "content": content_router,
    "admin": admin_router,
    "system": system_router,
    "frontend": frontend_router,
    "invite_bridge": invite_bridge_router,
}


def include_domain_routers(app: FastAPI) -> None:
    if getattr(app.state, "domain_routers_included", False):
        return
    for name in (
        "auth",
        "accounts",
        "ai",
        "orders",
        "settings",
        "content",
        "admin",
        "system",
        "invite_bridge",
        "frontend",
    ):
        app.include_router(DOMAIN_ROUTERS[name])
    app.state.domain_routers = DOMAIN_ROUTERS
    app.state.domain_routers_included = True
