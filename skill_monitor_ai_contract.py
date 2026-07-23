"""Strict structured-output contract for optional monitor AI filtering."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable, Dict


class SkillMonitorAIError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code or "ai_contract_error")[:80]
        self.safe_message = str(message or "AI 筛选失败")[:300]
        super().__init__(self.safe_message)


@dataclass(frozen=True)
class SkillMonitorAIDecision:
    recommended: bool
    score: int
    reason: str

    def public_dict(self) -> Dict[str, object]:
        return asdict(self)


def parse_skill_monitor_ai_decision(raw: str) -> SkillMonitorAIDecision:
    text = str(raw or "").strip()
    if not text or len(text.encode("utf-8")) > 8192:
        raise SkillMonitorAIError("ai_contract_invalid", "AI 返回为空或超过安全上限")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SkillMonitorAIError("ai_non_json", "AI 未返回严格 JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"recommended", "score", "reason"}:
        raise SkillMonitorAIError("ai_schema_invalid", "AI JSON 字段不符合约定")
    if not isinstance(payload["recommended"], bool):
        raise SkillMonitorAIError("ai_schema_invalid", "AI recommended 必须是布尔值")
    score_value = payload["score"]
    if isinstance(score_value, bool) or not isinstance(score_value, (int, float)):
        raise SkillMonitorAIError("ai_schema_invalid", "AI score 必须是数字")
    score = int(score_value)
    if float(score_value) != float(score) or not 0 <= score <= 100:
        raise SkillMonitorAIError("ai_schema_invalid", "AI score 必须是 0-100 的整数")
    reason = payload["reason"]
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 300:
        raise SkillMonitorAIError("ai_schema_invalid", "AI reason 长度必须为 1-300")
    return SkillMonitorAIDecision(
        recommended=payload["recommended"],
        score=score,
        reason=reason.strip(),
    )


async def evaluate_skill_monitor_ai_decision(
    provider_call: Callable[[], Awaitable[str]],
    lease_is_current: Callable[[], bool],
    *,
    timeout_seconds: float = 20.0,
) -> SkillMonitorAIDecision:
    """Check the durable lease before and after a bounded provider call."""
    if not lease_is_current():
        raise SkillMonitorAIError("ai_lease_lost", "AI 调用前运行租约已失效")
    try:
        raw = await asyncio.wait_for(
            provider_call(),
            timeout=max(0.01, min(float(timeout_seconds), 60.0)),
        )
    except asyncio.TimeoutError as exc:
        raise SkillMonitorAIError("ai_timeout", "AI Provider 调用超时") from exc
    except SkillMonitorAIError:
        raise
    except Exception as exc:
        raise SkillMonitorAIError("ai_provider_error", "AI Provider 调用失败") from exc
    if not lease_is_current():
        raise SkillMonitorAIError("ai_lease_lost", "AI 返回后运行租约已失效")
    return parse_skill_monitor_ai_decision(raw)


__all__ = [
    "SkillMonitorAIDecision",
    "SkillMonitorAIError",
    "evaluate_skill_monitor_ai_decision",
    "parse_skill_monitor_ai_decision",
]
