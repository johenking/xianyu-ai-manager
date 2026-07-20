"""Local-only retention cleanup for durable Skill Center monitor records."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from loguru import logger

from db_manager import db_manager


SKILL_MONITOR_RETENTION_INTERVAL_SECONDS = 6 * 60 * 60


class SkillMonitorRetentionJanitor:
    def __init__(
        self,
        interval_seconds: int = SKILL_MONITOR_RETENTION_INTERVAL_SECONDS,
        *,
        database: Optional[Any] = None,
    ) -> None:
        self.interval_seconds = max(60, int(interval_seconds))
        self.database = database or db_manager
        self._task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def run_once(self) -> Dict[str, int]:
        result = await asyncio.to_thread(
            self.database.cleanup_expired_skill_monitor_records
        )
        total_deleted = sum(
            int(result.get(key) or 0)
            for key in (
                'deliveries',
                'events',
                'result_identities',
                'results',
                'runs',
                'request_budgets',
                'mtop_breakers',
            )
        )
        if total_deleted:
            logger.info(
                "技能监控留存清理完成 "
                f"deleted={total_deleted}, "
                f"recovered_runs={int(result.get('recovered_runs') or 0)}, "
                "recovered_deliveries="
                f"{int(result.get('recovered_deliveries') or 0)}"
            )
        return result

    async def start(self) -> None:
        if self.running:
            return
        self._stopping = asyncio.Event()
        try:
            await self.run_once()
        except Exception as exc:
            logger.error(
                f"技能监控启动留存清理失败: {type(exc).__name__}"
            )
        self._task = asyncio.create_task(
            self._run(),
            name="skill-monitor-retention-janitor",
        )

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("技能监控留存清理器已停止")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.interval_seconds,
                )
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self.run_once()
            except Exception as exc:
                logger.error(
                    f"技能监控定期留存清理失败: {type(exc).__name__}"
                )


skill_monitor_retention_janitor = SkillMonitorRetentionJanitor()
