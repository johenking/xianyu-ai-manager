"""FastAPI application factory and lifespan ownership."""

from __future__ import annotations

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI

from application_runtime import start_runtime, stop_runtime


def assert_single_worker_configuration() -> None:
    for variable in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "WORKERS"):
        raw_value = os.getenv(variable)
        if not raw_value:
            continue
        try:
            workers = int(raw_value)
        except ValueError as exc:
            raise RuntimeError(f"{variable} 必须是整数") from exc
        if workers != 1:
            raise RuntimeError(
                "Xianyu AI Manager 仅支持单 worker；SQLite 和浏览器会话不能跨 worker 共享"
            )


@asynccontextmanager
async def application_lifespan(app: FastAPI):
    app.state.runtime = await start_runtime()
    try:
        yield
    finally:
        await stop_runtime()
        app.state.runtime = None


def create_app() -> FastAPI:
    assert_single_worker_configuration()
    from reply_server import app

    app.router.lifespan_context = application_lifespan
    return app
