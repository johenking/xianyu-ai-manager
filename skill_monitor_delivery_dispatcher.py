"""Lease-owned dispatcher for the Skill Center notification outbox."""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional, Set

import requests
from loguru import logger

from db_manager import db_manager
from skill_monitor_features import skill_monitor_feature_enabled


SKILL_MONITOR_DELIVERY_HEARTBEAT_SECONDS = 15
SKILL_MONITOR_DELIVERY_SEND_TIMEOUT_SECONDS = 15


class SkillMonitorDeliveryDispatcher:
    def __init__(self, poll_interval_seconds: int = 5, batch_size: int = 10):
        self.poll_interval_seconds = max(1, int(poll_interval_seconds))
        self.batch_size = max(1, min(int(batch_size), 50))
        self._task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()
        self._execution_tasks: Set[asyncio.Task] = set()
        self._active_claims: Dict[int, str] = {}

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        if not skill_monitor_feature_enabled("skill_monitor_delivery_enabled"):
            logger.info("技能监控通知 dispatcher 未启动（开关关闭）")
            return
        self._stopping = asyncio.Event()
        recovered = db_manager.recover_stale_skill_monitor_deliveries()
        if recovered:
            logger.warning(f"已标记 {recovered} 个结果未知的技能通知投递")
        self._task = asyncio.create_task(
            self._run(),
            name="skill-monitor-delivery-dispatcher",
        )
        logger.info("技能监控通知 dispatcher 已启动")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Persist the shutdown outcome before cancelling worker tasks. A
        # graceful server timeout can cancel the lifespan coroutine before an
        # in-flight worker reaches its CancelledError handler; leaving a row
        # in sending would otherwise require stale-lease recovery.
        for delivery_id, claim_token in list(self._active_claims.items()):
            try:
                db_manager.finish_skill_monitor_delivery(
                    delivery_id,
                    claim_token,
                    status="unknown",
                    error_code="dispatcher_interrupted",
                    error_message="服务停止时通知发送结果未知",
                )
            except Exception as exc:
                logger.warning(
                    "服务停止时无法确认技能通知结果 "
                    f"delivery_id={delivery_id}, error={type(exc).__name__}"
                )
        tasks = list(self._execution_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._execution_tasks.clear()
        self._active_claims.clear()
        logger.info("技能监控通知 dispatcher 已停止")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                logger.error(
                    f"技能监控通知 dispatcher 异常: {type(exc).__name__}"
                )
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def run_once(self) -> int:
        if not skill_monitor_feature_enabled("skill_monitor_delivery_enabled"):
            return 0
        db_manager.recover_stale_skill_monitor_deliveries()
        started = 0
        for _ in range(self.batch_size):
            delivery = db_manager.claim_skill_monitor_delivery()
            if not delivery:
                break
            task = asyncio.create_task(
                self._execute(delivery),
                name=f"skill-monitor-delivery:{delivery['id']}",
            )
            delivery_id = int(delivery["id"])
            self._active_claims[delivery_id] = str(delivery["claim_token"])
            self._execution_tasks.add(task)
            task.add_done_callback(self._execution_tasks.discard)
            task.add_done_callback(
                lambda _task, claim_id=delivery_id: self._active_claims.pop(
                    claim_id,
                    None,
                )
            )
            started += 1
        return started

    async def _heartbeat(
        self,
        delivery_id: int,
        claim_token: str,
        stop_event: asyncio.Event,
        lease_lost: asyncio.Event,
        owner_task: Optional[asyncio.Task],
    ) -> None:
        while not stop_event.is_set():
            if not skill_monitor_feature_enabled(
                "skill_monitor_delivery_enabled"
            ):
                lease_lost.set()
                if owner_task is not None and not owner_task.done():
                    owner_task.cancel()
                return
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=SKILL_MONITOR_DELIVERY_HEARTBEAT_SECONDS,
                )
                return
            except asyncio.TimeoutError:
                pass
            if db_manager.heartbeat_skill_monitor_delivery(
                delivery_id,
                claim_token,
            ):
                continue
            lease_lost.set()
            if owner_task is not None and not owner_task.done():
                owner_task.cancel()
            return

    async def _execute(self, delivery: dict) -> None:
        delivery_id = int(delivery["id"])
        claim_token = str(delivery["claim_token"])
        if not skill_monitor_feature_enabled("skill_monitor_delivery_enabled"):
            db_manager.finish_skill_monitor_delivery(
                delivery_id,
                claim_token,
                status="retry",
                error_code="delivery_disabled",
                error_message="监控通知开关关闭",
                next_attempt_at=time.time() + 60,
            )
            return

        context = db_manager.get_skill_monitor_delivery_context(
            delivery_id,
            claim_token,
        )
        channel = (context or {}).get("channel")
        task = (context or {}).get("task")
        result = (context or {}).get("result")
        if (
            not context
            or not channel
            or not channel.get("enabled")
            or not task.get("notify_enabled")
            or str(channel.get("type") or "").lower()
            != str(delivery.get("channel_type") or "").lower()
        ):
            db_manager.finish_skill_monitor_delivery(
                delivery_id,
                claim_token,
                status="failed",
                error_code="channel_unavailable",
                error_message="通知渠道不可用或不再属于当前用户",
            )
            return

        result = dict(result)
        result["_delivery_idempotency_key"] = str(
            delivery.get("idempotency_key") or ""
        )
        stop_heartbeat = asyncio.Event()
        lease_lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat(
                delivery_id,
                claim_token,
                stop_heartbeat,
                lease_lost,
                asyncio.current_task(),
            ),
            name=f"skill-monitor-delivery-heartbeat:{delivery_id}",
        )
        try:
            from reply_server import _send_skill_notification_to_channel

            await asyncio.wait_for(
                asyncio.to_thread(
                    _send_skill_notification_to_channel,
                    channel,
                    task,
                    result,
                ),
                timeout=SKILL_MONITOR_DELIVERY_SEND_TIMEOUT_SECONDS,
            )
            stop_heartbeat.set()
            await heartbeat_task
            if not db_manager.finish_skill_monitor_delivery(
                delivery_id,
                claim_token,
                status="sent",
            ):
                logger.warning(
                    f"技能监控通知发送后未能确认数据库状态 delivery_id={delivery_id}"
                )
        except asyncio.CancelledError:
            stop_heartbeat.set()
            if not heartbeat_task.done():
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            db_manager.finish_skill_monitor_delivery(
                delivery_id,
                claim_token,
                status="unknown",
                error_code=(
                    "delivery_lease_lost"
                    if lease_lost.is_set()
                    else "dispatcher_interrupted"
                ),
                error_message="通知发送结果未知，未自动重试",
            )
            raise
        except Exception as exc:
            stop_heartbeat.set()
            if not heartbeat_task.done():
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            from reply_server import _safe_skill_notification_error

            outcome_unknown = isinstance(
                exc,
                (requests.RequestException, asyncio.TimeoutError, TimeoutError),
            )
            db_manager.finish_skill_monitor_delivery(
                delivery_id,
                claim_token,
                status="unknown" if outcome_unknown else "failed",
                error_code=(
                    "send_outcome_unknown"
                    if outcome_unknown
                    else type(exc).__name__.lower()[:80]
                ),
                error_message=_safe_skill_notification_error(exc),
            )
        finally:
            stop_heartbeat.set()
            if not heartbeat_task.done():
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)


skill_monitor_delivery_dispatcher = SkillMonitorDeliveryDispatcher()
