"""Verified seller-backend item metric ingestion with a three-canary gate."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence
from typing import Any, Awaitable, Callable, Dict, Optional
from weakref import WeakKeyDictionary


ItemMetricCollector = Callable[..., Awaitable[Sequence[Dict[str, Any]]]]
_collector: Optional[ItemMetricCollector] = None
_account_locks_by_loop: WeakKeyDictionary = WeakKeyDictionary()
ITEM_METRIC_ADAPTER_TIMEOUT_SECONDS = 30
ITEM_METRIC_MAX_ROWS = 200


class ItemMetricCollectorContractError(TypeError):
    """Raised before invoking a collector that cannot be cancelled safely."""


class ItemMetricCollectorResultError(TypeError):
    """Raised when an adapter result cannot be proven bounded before copying."""


def register_item_metric_collector(collector: Optional[ItemMetricCollector]) -> None:
    global _collector
    _collector = collector


def item_metric_collector_available() -> bool:
    return _collector is not None


def _account_collection_lock(cookie_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _account_locks_by_loop.setdefault(loop, {})
    return locks.setdefault(str(cookie_id), asyncio.Lock())


async def _invoke_collector(
    collector: ItemMetricCollector,
    *,
    cookie_id: str,
    cookie_string: str,
) -> list[Dict[str, Any]]:
    async_callable = inspect.iscoroutinefunction(collector) or inspect.iscoroutinefunction(
        getattr(collector, "__call__", None)
    )
    if not async_callable:
        raise ItemMetricCollectorContractError(
            "商品指标适配器必须实现可取消的异步调用合同"
        )

    result = await collector(cookie_id=cookie_id, cookie_string=cookie_string)
    if result is None:
        return []
    if isinstance(result, (str, bytes, bytearray)) or not isinstance(
        result,
        Sequence,
    ):
        raise ItemMetricCollectorResultError(
            "商品指标适配器必须返回有界序列"
        )
    row_count = len(result)
    if row_count > ITEM_METRIC_MAX_ROWS:
        raise ItemMetricCollectorResultError(
            "商品指标适配器单批返回行数超过上限"
        )
    return [result[index] for index in range(row_count)]


def item_metric_collection_enabled(
    db: Any,
    *,
    user_id: int,
    cookie_id: str,
) -> bool:
    state = db.get_item_metric_collection_state(
        user_id=user_id,
        cookie_id=cookie_id,
    )
    return bool(state.get("enabled") and state.get("canary_success_count") >= 3)


def item_metric_collection_status(
    db: Any,
    *,
    user_id: int,
    cookie_id: str,
) -> Dict[str, Any]:
    state = db.get_item_metric_collection_state(
        user_id=user_id,
        cookie_id=cookie_id,
    )
    return {
        **state,
        "adapter_available": item_metric_collector_available(),
        "collection_enabled": bool(
            state.get("enabled")
            and int(state.get("canary_success_count") or 0) >= 3
        ),
    }


async def collect_item_metrics_once(
    db: Any,
    *,
    user_id: int,
    cookie_id: str,
    cookie_string: str,
    canary: bool = False,
) -> Dict[str, Any]:
    """Run a registered, verified adapter and persist only explicit metric fields."""
    if _collector is None:
        return {
            "success": False,
            "error_code": "metric_adapter_unavailable",
            "message": "真实卖家后台指标适配器尚未通过金丝雀验收",
            "inserted": 0,
        }
    collector = _collector
    lock = _account_collection_lock(cookie_id)
    async with lock:
        try:
            rows = await asyncio.wait_for(
                _invoke_collector(
                    collector,
                    cookie_id=cookie_id,
                    cookie_string=cookie_string,
                ),
                timeout=ITEM_METRIC_ADAPTER_TIMEOUT_SECONDS,
            )
            if not rows:
                raise ValueError("商品指标适配器没有返回已验证快照")
            batch_result = db.record_item_metric_snapshots(
                user_id=user_id,
                cookie_id=cookie_id,
                rows=rows,
            )
            state = (
                db.record_item_metric_canary_result(
                    user_id=user_id,
                    cookie_id=cookie_id,
                    success=True,
                    observed_at=batch_result.get("newest_inserted_observed_at"),
                )
                if canary
                else db.get_item_metric_collection_state(
                    user_id=user_id,
                    cookie_id=cookie_id,
                )
            )
            return {
                "success": True,
                **batch_result,
                "canary_successes": int(state.get("canary_success_count") or 0),
                "collection_enabled": bool(state.get("enabled")),
                "canary_advanced": bool(state.get("canary_advanced")),
            }
        except Exception as exc:
            if isinstance(exc, asyncio.TimeoutError):
                error_code = "metric_adapter_timeout"
            elif isinstance(exc, ItemMetricCollectorContractError):
                error_code = "metric_adapter_must_be_async"
            elif isinstance(exc, ItemMetricCollectorResultError):
                error_code = "metric_adapter_result_not_bounded"
            else:
                error_code = "metric_collection_failed"
            if canary:
                db.record_item_metric_canary_result(
                    user_id=user_id,
                    cookie_id=cookie_id,
                    success=False,
                    error_code=error_code,
                )
            return {
                "success": False,
                "error_code": error_code,
                "message": "商品指标采集失败，请检查账号状态或适配器日志",
                "error_type": type(exc).__name__,
                "inserted": 0,
            }
