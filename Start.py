"""Start Xianyu AI Manager with one Uvicorn worker and one event loop."""

from __future__ import annotations

import os
from urllib.parse import urlparse

import uvicorn

from config import AUTO_REPLY


def _server_address() -> tuple[str, int]:
    api_config = AUTO_REPLY.get("api", {})
    host = api_config.get("host") or os.getenv("API_HOST", "0.0.0.0")
    port = api_config.get("port")
    if "url" in api_config and "host" not in api_config and "port" not in api_config:
        parsed = urlparse(api_config.get("url", "http://0.0.0.0:8080/xianyu/reply"))
        host = parsed.hostname or host
        port = parsed.port or 8080
    port = int(os.getenv("PORT") or os.getenv("API_PORT") or port or 8080)
    return host, port


def main() -> None:
    host, port = _server_address()
    uvicorn.run(
        "app_factory:create_app",
        factory=True,
        host=host,
        port=port,
        workers=1,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
