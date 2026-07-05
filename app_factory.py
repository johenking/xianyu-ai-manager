"""FastAPI application factory and lifespan ownership."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from application_runtime import start_runtime, stop_runtime


@asynccontextmanager
async def application_lifespan(app: FastAPI):
    app.state.runtime = await start_runtime()
    try:
        yield
    finally:
        await stop_runtime()
        app.state.runtime = None


def create_app() -> FastAPI:
    from reply_server import app

    app.router.lifespan_context = application_lifespan
    return app

