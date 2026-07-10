"""Single-event-loop startup and shutdown for account listeners and browsers."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import List, Tuple

from loguru import logger

import cookie_manager as cookie_manager_module
from config import COOKIES_LIST
from db_manager import db_manager
from session_registry import initialize_session_registry
from skill_monitor_scheduler import skill_monitor_scheduler


def _load_keywords_file(path: str) -> List[Tuple[str, str]]:
    keywords: List[Tuple[str, str]] = []
    source = Path(path)
    if not source.exists():
        return keywords
    with source.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            for separator in ("\t", " ", ":"):
                if separator in line:
                    keyword, reply = line.split(separator, 1)
                    keywords.append((keyword.strip(), reply.strip()))
                    break
    return keywords


async def start_runtime() -> cookie_manager_module.CookieManager:
    loop = asyncio.get_running_loop()
    initialize_session_registry(db_manager).cleanup()
    manager = cookie_manager_module.manager
    if manager is None:
        manager = cookie_manager_module.CookieManager(loop)
        cookie_manager_module.manager = manager
    elif manager.loop is not loop:
        raise RuntimeError("CookieManager 已绑定到另一个事件循环，拒绝重复启动")

    for cookie_id, cookie_value in list(manager.cookies.items()):
        if not manager.get_cookie_status(cookie_id):
            continue
        task = manager.tasks.get(cookie_id)
        if task and not task.done():
            continue
        details = db_manager.get_cookie_details(cookie_id) or {}
        manager.tasks[cookie_id] = loop.create_task(
            manager._run_xianyu(cookie_id, cookie_value, details.get("user_id")),
            name=f"xianyu-listener:{cookie_id}",
        )

    for entry in COOKIES_LIST:
        cookie_id = entry.get("id")
        cookie_value = entry.get("value")
        if not cookie_id or not cookie_value or cookie_id in manager.cookies:
            continue
        keywords_file = entry.get("keywords_file")
        keywords = _load_keywords_file(keywords_file) if keywords_file else None
        await manager._add_cookie_async(cookie_id, cookie_value)
        if keywords is not None:
            manager.update_keywords(cookie_id, keywords)

    env_cookie = os.getenv("COOKIES_STR")
    if env_cookie and "default" not in manager.cookies:
        await manager._add_cookie_async("default", env_cookie)

    await skill_monitor_scheduler.start()
    logger.info(f"运行时启动完成，账号监听任务: {len(manager.tasks)}")
    return manager


async def stop_runtime() -> None:
    await skill_monitor_scheduler.stop()

    manager = cookie_manager_module.manager
    if manager is not None:
        await manager.shutdown()
        cookie_manager_module.manager = None

    try:
        from utils.browser_pool import close_global_browser_pool

        await close_global_browser_pool()
    except Exception as exc:
        logger.warning(f"关闭浏览器池时出现问题: {exc}")
    logger.info("运行时已停止")
