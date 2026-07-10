"""Single-loop scheduler for Skill Center monitor tasks."""

from __future__ import annotations

import asyncio
from typing import Optional, Set

from loguru import logger

from db_manager import db_manager


class SkillMonitorScheduler:
    """Polls SQLite for due monitor tasks and runs them in the app event loop."""

    def __init__(self, poll_interval_seconds: int = 30):
        self.poll_interval_seconds = poll_interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()
        self._running_task_ids: Set[int] = set()
        self._execution_tasks: Set[asyncio.Task] = set()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stopping = asyncio.Event()
        reset_count = db_manager.reset_running_skill_monitor_tasks()
        if reset_count:
            logger.warning(f"已重置 {reset_count} 个重启前未完成的技能监控任务")
        self._task = asyncio.create_task(self._run(), name="skill-monitor-scheduler")
        logger.info("技能中心定时监控调度器已启动")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        execution_tasks = list(self._execution_tasks)
        for task in execution_tasks:
            task.cancel()
        if execution_tasks:
            await asyncio.gather(*execution_tasks, return_exceptions=True)
        self._execution_tasks.clear()
        self._running_task_ids.clear()
        logger.info("技能中心定时监控调度器已停止")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_due_once()
            except Exception as exc:
                logger.error(f"技能中心定时调度循环异常: {exc}")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def run_due_once(self) -> int:
        due_tasks = db_manager.list_due_skill_monitor_tasks()
        if not due_tasks:
            return 0

        started = 0
        for task in due_tasks:
            task_id = int(task["id"])
            if task_id in self._running_task_ids:
                continue
            self._running_task_ids.add(task_id)
            execution_task = asyncio.create_task(
                self._execute(task),
                name=f"skill-monitor-task:{task_id}",
            )
            self._execution_tasks.add(execution_task)
            execution_task.add_done_callback(self._execution_tasks.discard)
            started += 1
        return started

    async def _execute(self, task: dict) -> None:
        task_id = int(task["id"])
        try:
            from reply_server import execute_skill_monitor_task

            await execute_skill_monitor_task(task, int(task["user_id"]), scheduled_run=True)
        except Exception as exc:
            logger.error(f"定时技能监控任务失败 task_id={task_id}: {exc}")
        finally:
            self._running_task_ids.discard(task_id)


skill_monitor_scheduler = SkillMonitorScheduler()
