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
    # 在任何出站请求前把公网域名解析改道到真实解析器（5053 优先 + 公共 DNS 兜底），
    # 并清除继承的代理环境变量，让后端出站直连真实 IP、不依赖本机代理/外部组件。
    from utils.outbound_dns import (
        install_outbound_dns_patch,
        neutralize_inherited_proxy_env,
        outbound_dns_resolver_label,
    )
    if install_outbound_dns_patch():
        import logging
        removed_proxy = neutralize_inherited_proxy_env()
        logging.getLogger("uvicorn.error").info(
            "出站 DNS 已改道至 %s（绕开系统 fake-IP）；已清除继承代理变量 %s",
            outbound_dns_resolver_label() or "system",
            removed_proxy or "无",
        )
    from reply_server import app

    app.router.lifespan_context = application_lifespan
    return app
