"""Command line entry point for the native browser helper."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .helper import NativeBrowserHelper
from .server import run_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Xianyu native browser login helper")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("XMC_HELPER_PORT", "17890")))
    parser.add_argument("--browser", choices=("chrome", "edge"), default=os.environ.get("XMC_HELPER_BROWSER", "chrome"))
    parser.add_argument("--state-dir", type=Path, default=None)
    args = parser.parse_args()
    helper = NativeBrowserHelper(browser_family=args.browser, state_dir=args.state_dir)
    try:
        run_server(helper, host=args.host, port=args.port)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
