"""Default-off, single-worker scheduler for verified item metric snapshots."""

from __future__ import annotations

import asyncio
import random
from typing import Optional

from loguru import logger

from db_manager import db_manager
from item_metric_service import (
    collect_item_metrics_once,
    item_metric_collector_available,
)

ITEM_METRIC_SCHEDULE_SECONDS = 4 * 60 * 60
ITEM_METRIC_SCHEDULE_JITTER_SECONDS = 15 * 60


class ItemMetricScheduler:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def start(self) -> bool:
        if self._task and not self._task.done():
            return True
        if not db_manager.has_enabled_item_metric_collection():
            logger.info("商品指标定时采集保持关闭（没有通过金丝雀的账号）")
            return False
        if not item_metric_collector_available():
            logger.warning("商品指标采集已配置开启，但真实适配器尚未注册")
            return False
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="item-metric-scheduler")
        return True

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._collect_all_accounts()
            except Exception as exc:
                logger.error(
                    f"商品指标定时采集循环异常: {type(exc).__name__}"
                )
            delay = ITEM_METRIC_SCHEDULE_SECONDS + random.uniform(
                0,
                ITEM_METRIC_SCHEDULE_JITTER_SECONDS,
            )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                continue

    async def _collect_all_accounts(self) -> None:
        successes = 0
        failures = 0
        accounts = db_manager.get_all_cookies()
        for cookie_id, cookie_string in accounts.items():
            details = db_manager.get_cookie_details(cookie_id) or {}
            user_id = details.get("user_id")
            if user_id is None:
                failures += 1
                continue
            state = db_manager.get_item_metric_collection_state(
                user_id=int(user_id),
                cookie_id=cookie_id,
            )
            if not (
                state.get("enabled")
                and int(state.get("canary_success_count") or 0) >= 3
            ):
                continue
            result = await collect_item_metrics_once(
                db_manager,
                user_id=int(user_id),
                cookie_id=cookie_id,
                cookie_string=cookie_string,
                canary=False,
            )
            if result.get("success"):
                successes += 1
            else:
                failures += 1
            await asyncio.sleep(random.uniform(1.5, 3.5))
        logger.info(f"商品指标串行采集结束: 成功 {successes}, 失败 {failures}")


item_metric_scheduler = ItemMetricScheduler()
