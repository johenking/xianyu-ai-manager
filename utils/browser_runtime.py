"""Shared Playwright Chromium runtime policy for headed login sessions."""

from __future__ import annotations

import os
from typing import Any


_PROFILE_LOCK_MARKERS = ("processsingleton", "singletonlock")


def chromium_sandbox_enabled() -> bool:
    """Keep Chromium's sandbox unless the process runs as root.

    Chromium refuses to start as root with its sandbox enabled. Containers run
    the application as root today, while normal macOS/Linux installs retain the
    safer default.
    """

    get_effective_uid = getattr(os, "geteuid", None)
    return not callable(get_effective_uid) or get_effective_uid() != 0


def chromium_runtime_options() -> dict[str, Any]:
    """Return the channel and sandbox options shared by headed Chromium paths."""

    return {
        "channel": os.getenv("XIANYU_BROWSER_CHANNEL") or None,
        "chromium_sandbox": chromium_sandbox_enabled(),
    }


def classify_browser_launch_error(error: BaseException) -> str:
    """Classify launch failures without treating generic profile text as a lock."""

    text = str(error).lower()
    if any(marker in text for marker in _PROFILE_LOCK_MARKERS):
        return "profile_in_use"
    return "browser_error"
