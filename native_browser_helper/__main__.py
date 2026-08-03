"""Command line entry point for the native browser helper."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from .helper import NativeBrowserHelper
from .installer import (
    InstallerError,
    NativeHelperInstaller,
    is_packaged,
    notify_user,
)
from .server import run_server
from .cdp import BrowserLauncher
from .keystore import default_state_dir
from .runtime import ServiceAlreadyRunning, ServicePidFile


def _emit_result(value: dict[str, Any], result_file: Optional[Path]) -> None:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True)
    if result_file:
        target = Path(result_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        temporary.write_text(payload + "\n", encoding="utf-8")
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(target)
    if sys.stdout is not None:
        print(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Xianyu native browser login helper")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("XMC_HELPER_PORT", "17890")))
    parser.add_argument("--browser", choices=("auto", "chrome", "edge"), default=os.environ.get("XMC_HELPER_BROWSER", "auto"))
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--result-file", type=Path, default=None)
    parser.add_argument("--no-ui", action="store_true", help="suppress installer dialogs")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--serve", action="store_true", help="run the loopback service")
    action.add_argument("--install", action="store_true", help="install for the current user")
    action.add_argument("--uninstall", action="store_true", help="remove the current-user installation")
    action.add_argument("--status", action="store_true", help="print installation status")
    args = parser.parse_args()
    try:
        installer = NativeHelperInstaller(port=args.port, state_dir=args.state_dir)
        if args.status:
            _emit_result(installer.status().public_dict(), args.result_file)
            return
        if args.uninstall:
            status = installer.uninstall()
            _emit_result(status.public_dict(), args.result_file)
            return
        if args.install or (is_packaged() and not args.serve):
            status = installer.install_and_start()
            if not args.no_ui:
                notify_user("安装完成。现在回到监控台点击“本机 Chrome 登录”。")
            _emit_result(status.public_dict(), args.result_file)
            return
    except InstallerError as exc:
        if not args.no_ui:
            notify_user(str(exc), error=True)
        raise SystemExit(1) from exc
    state_dir = Path(args.state_dir or default_state_dir())
    launcher = BrowserLauncher(state_dir)
    browser_family = launcher.resolve_browser_family(args.browser)
    helper = NativeBrowserHelper(
        browser_family=browser_family,
        state_dir=state_dir,
        launcher=launcher,
    )
    try:
        with ServicePidFile(state_dir):
            run_server(helper, host=args.host, port=args.port)
    except ServiceAlreadyRunning:
        return
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
