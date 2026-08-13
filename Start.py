"""Start Xianyu AI Manager with one Uvicorn worker and one event loop."""

from __future__ import annotations

import os

import uvicorn

from app_factory import assert_single_worker_configuration
from config import AUTO_REPLY


def _server_address() -> tuple[str, int]:
    api_config = AUTO_REPLY.get("api", {})
    host = os.getenv("API_HOST") or api_config.get("host") or "0.0.0.0"
    port = int(
        os.getenv("PORT")
        or os.getenv("API_PORT")
        or api_config.get("port")
        or 8080
    )
    return host, port


def main() -> None:
    # 尽早改道出站 DNS 并清除继承代理（在 uvicorn 启动及任何出站请求前），
    # 绕开系统 fake-IP，且不依赖本机代理组件。
    from utils.outbound_dns import install_outbound_dns_patch, neutralize_inherited_proxy_env
    if install_outbound_dns_patch():
        neutralize_inherited_proxy_env()
    assert_single_worker_configuration()
    host, port = _server_address()
    uvicorn.run(
        "app_factory:create_app",
        factory=True,
        host=host,
        port=port,
        workers=1,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
