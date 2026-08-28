"""发货链路阶段耗时观测（P0 观测埋点，不改变任何发货行为）。

以订单为单位记录 paid_detected → gate_passed → bridge_handoff → wof_confirmation
→ wof_fulfillment → shipped 各阶段的墙钟时间：每个阶段输出一条 DELIVERY_STAGE
结构化日志（含距上一阶段/距付款识别的毫秒差），终态 shipped 额外输出一条
DELIVERY_STAGE_SUMMARY 汇总各段耗时，用于量化"付款到发货"慢在哪一段
（2026-08-28 发货耗时观测需求：区分本系统门禁耗时与 wo-f 邀请服务内部耗时）。

登记表仅存内存：进程重启会丢失在途订单的起点，此后阶段日志的差值标 na，
但每条日志自带墙钟时间戳，可人工还原，属 P0 可接受的观测损失。
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Dict, Optional

from loguru import logger

STAGE_PAID = "paid_detected"
STAGE_GATE = "gate_passed"
STAGE_HANDOFF = "bridge_handoff"
STAGE_CONFIRMATION = "wof_confirmation"
STAGE_FULFILLMENT = "wof_fulfillment"
STAGE_SHIPPED = "shipped"

STAGE_SEQUENCE = (
    STAGE_PAID,
    STAGE_GATE,
    STAGE_HANDOFF,
    STAGE_CONFIRMATION,
    STAGE_FULFILLMENT,
    STAGE_SHIPPED,
)

_MAX_TRACKED_ORDERS = 1000

_lock = threading.Lock()
_orders: "OrderedDict[str, Dict[str, float]]" = OrderedDict()


def _ref(value: object) -> str:
    """与全仓日志口径一致的脱敏引用（sha256 前 12 位）。"""
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]


def _format_ms(value: Optional[float]) -> str:
    return f"{value:.0f}" if value is not None else "na"


def record_stage(
    order_id: object,
    cookie_id: object,
    stage: str,
    *,
    now: Optional[float] = None,
) -> None:
    """记录订单发货链路的一个阶段。

    同一订单同一阶段只记首次（重试/重复回调天然幂等）；任何异常都不外泄，
    观测埋点绝不影响发货主链路。
    """
    try:
        key = str(order_id or "").strip()
        if not key or stage not in STAGE_SEQUENCE:
            return
        moment = float(now if now is not None else time.time())
        with _lock:
            stages = _orders.get(key)
            if stages is None:
                stages = {}
                _orders[key] = stages
                while len(_orders) > _MAX_TRACKED_ORDERS:
                    _orders.popitem(last=False)
            if stage in stages:
                return
            stages[stage] = moment
            snapshot = dict(stages)
            if stage == STAGE_SHIPPED:
                _orders.pop(key, None)

        earlier = [
            snapshot[name]
            for name in STAGE_SEQUENCE
            if name != stage and name in snapshot and snapshot[name] <= moment
        ]
        since_prev_ms = (moment - max(earlier)) * 1000 if earlier else None
        since_paid_ms = (
            (moment - snapshot[STAGE_PAID]) * 1000
            if STAGE_PAID in snapshot and stage != STAGE_PAID
            else (0.0 if stage == STAGE_PAID else None)
        )
        logger.info(
            "DELIVERY_STAGE order_ref={} account_ref={} stage={} since_prev_ms={} since_paid_ms={}",
            _ref(order_id),
            _ref(cookie_id),
            stage,
            _format_ms(since_prev_ms),
            _format_ms(since_paid_ms),
        )
        if stage == STAGE_SHIPPED:
            parts = []
            previous_moment: Optional[float] = None
            first_moment: Optional[float] = None
            for name in STAGE_SEQUENCE:
                stage_moment = snapshot.get(name)
                if stage_moment is None:
                    parts.append(f"{name}=na")
                    continue
                if previous_moment is None:
                    first_moment = stage_moment
                    parts.append(f"{name}=+0ms")
                else:
                    parts.append(
                        f"{name}=+{(stage_moment - previous_moment) * 1000:.0f}ms"
                    )
                previous_moment = stage_moment
            total_ms = (
                (moment - first_moment) * 1000 if first_moment is not None else None
            )
            logger.info(
                "DELIVERY_STAGE_SUMMARY order_ref={} account_ref={} {} total_ms={}",
                _ref(order_id),
                _ref(cookie_id),
                " ".join(parts),
                _format_ms(total_ms),
            )
    except Exception as exc:
        try:
            logger.debug("DELIVERY_STAGE 记录失败: {}", type(exc).__name__)
        except Exception:
            pass
