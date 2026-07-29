"""Owner-thread browser interaction queue for authenticated login sessions.

FastAPI request handlers only validate and enqueue bounded actions. The
Playwright-owning thread is the only thread that drains the queue, captures
frames, or touches a Page object.
"""

from __future__ import annotations

import math
import queue
import threading
import time
import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional


ALLOWED_KEYS = frozenset({"Enter", "Backspace", "Tab", "Escape"})
ALLOWED_KINDS = frozenset({"gesture", "text", "key", "wheel"})
MAX_POINTS = 80
MAX_TEXT_LENGTH = 128
MAX_DURATION_MS = 5000
MAX_WHEEL_DELTA = 2000.0
MAX_FRAME_BYTES = 8 * 1024 * 1024
RECENT_FRAME_TTL_SECONDS = 6.0
RECENT_FRAME_LIMIT = 8


class BrowserInteractionError(RuntimeError):
    """Base class for safe, public interaction failures."""


class InteractionUnavailable(BrowserInteractionError):
    pass


class InteractionValidationError(BrowserInteractionError):
    pass


class StaleFrameRevision(BrowserInteractionError):
    pass


class InteractionRateLimited(BrowserInteractionError):
    pass


class InteractionQueueFull(BrowserInteractionError):
    pass


@dataclass(frozen=True)
class _QueuedInteraction:
    kind: str
    frame_revision: int
    points: tuple[tuple[float, float], ...] = ()
    duration_ms: int = 0
    text: str = field(default="", repr=False)
    key: str = ""
    delta_x: float = 0.0
    delta_y: float = 0.0


class BrowserInteractionChannel:
    """Bounded in-memory bridge between HTTP handlers and one Playwright thread."""

    def __init__(
        self,
        *,
        max_queue: int = 32,
        rate_per_second: float = 10.0,
        burst: int = 20,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._queue: queue.Queue[_QueuedInteraction] = queue.Queue(
            maxsize=max(1, int(max_queue))
        )
        self._lock = threading.RLock()
        self._clock = clock
        self._rate_per_second = max(0.1, float(rate_per_second))
        self._burst = max(1, int(burst))
        self._tokens = float(self._burst)
        self._last_refill = float(clock())
        self._frame: Optional[bytes] = None
        self._frame_revision = 0
        self._viewport_width = 0
        self._viewport_height = 0
        self._surface_key_hash = ""
        self._revision_history: OrderedDict[
            int,
            tuple[str, float, int, int],
        ] = OrderedDict()
        self._closed = False

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    def publish_frame(
        self,
        png_bytes: bytes,
        *,
        viewport_width: int,
        viewport_height: int,
        surface_key: str = "",
    ) -> int:
        frame = bytes(png_bytes or b"")
        width = int(viewport_width)
        height = int(viewport_height)
        if not frame or len(frame) > MAX_FRAME_BYTES:
            raise InteractionValidationError("浏览器画面不可用")
        if not (1 <= width <= 4096 and 1 <= height <= 4096):
            raise InteractionValidationError("浏览器视口尺寸无效")
        surface_hash = hashlib.sha256(
            str(surface_key or "").encode("utf-8"),
        ).hexdigest()
        with self._lock:
            self._closed = False
            if (
                self._frame == frame
                and self._viewport_width == width
                and self._viewport_height == height
                and self._surface_key_hash == surface_hash
            ):
                return self._frame_revision
            self._frame = frame
            self._viewport_width = width
            self._viewport_height = height
            self._surface_key_hash = surface_hash
            self._frame_revision += 1
            now = float(self._clock())
            self._revision_history[self._frame_revision] = (
                surface_hash,
                now,
                width,
                height,
            )
            while len(self._revision_history) > RECENT_FRAME_LIMIT:
                self._revision_history.popitem(last=False)
            for revision, (_key, captured_at, _width, _height) in list(
                self._revision_history.items()
            ):
                if now - captured_at > RECENT_FRAME_TTL_SECONDS:
                    self._revision_history.pop(revision, None)
            return self._frame_revision

    def capture(self, page: Any) -> int:
        viewport = getattr(page, "viewport_size", None)
        if callable(viewport):
            viewport = viewport()
        if not isinstance(viewport, Mapping):
            evaluate = getattr(page, "evaluate", None)
            viewport = (
                evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")
                if callable(evaluate)
                else None
            )
        width = int((viewport or {}).get("width") or 0)
        height = int((viewport or {}).get("height") or 0)
        try:
            surface_key = str(getattr(page, "url", "") or "")
        except Exception:
            surface_key = ""
        frame = page.screenshot(type="png", full_page=False)
        return self.publish_frame(
            frame,
            viewport_width=width,
            viewport_height=height,
            surface_key=surface_key,
        )

    def latest_frame(self) -> Optional[tuple[bytes, int]]:
        with self._lock:
            if self._closed or self._frame is None:
                return None
            return self._frame, self._frame_revision

    def snapshot(self) -> dict[str, int | bool]:
        with self._lock:
            supported = not self._closed and self._frame is not None
            return {
                "interaction_supported": supported,
                "frame_revision": self._frame_revision if supported else 0,
                "viewport_width": self._viewport_width if supported else 0,
                "viewport_height": self._viewport_height if supported else 0,
            }

    def submit(self, payload: Mapping[str, Any]) -> int:
        action = self._normalize(payload)
        with self._lock:
            if self._closed or self._frame is None:
                raise InteractionUnavailable("浏览器交互尚未就绪")
            if action.frame_revision != self._frame_revision:
                recent = self._revision_history.get(action.frame_revision)
                now = float(self._clock())
                if (
                    recent is None
                    or now - recent[1] > RECENT_FRAME_TTL_SECONDS
                    or recent[0] != self._surface_key_hash
                    or recent[2] != self._viewport_width
                    or recent[3] != self._viewport_height
                ):
                    raise StaleFrameRevision("浏览器画面已更新，请在最新画面上重试")
            self._consume_rate_token()
            try:
                self._queue.put_nowait(action)
            except queue.Full as exc:
                raise InteractionQueueFull("浏览器操作队列已满，请稍后重试") from exc
            return self._queue.qsize()

    def drain(self, page: Any) -> int:
        executed = 0
        while True:
            try:
                action = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._execute(page, action)
                executed += 1
            finally:
                self._queue.task_done()
        return executed

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._frame = None
            self._frame_revision = 0
            self._viewport_width = 0
            self._viewport_height = 0
            self._surface_key_hash = ""
            self._revision_history.clear()
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
                else:
                    self._queue.task_done()

    def _consume_rate_token(self) -> None:
        now = float(self._clock())
        elapsed = max(0.0, now - self._last_refill)
        self._tokens = min(
            float(self._burst),
            self._tokens + elapsed * self._rate_per_second,
        )
        self._last_refill = now
        if self._tokens < 1.0:
            raise InteractionRateLimited("浏览器操作过于频繁，请稍后重试")
        self._tokens -= 1.0

    @staticmethod
    def _finite_number(value: Any, field_name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InteractionValidationError(f"{field_name} 格式无效")
        number = float(value)
        if not math.isfinite(number):
            raise InteractionValidationError(f"{field_name} 格式无效")
        return number

    def _normalize(self, payload: Mapping[str, Any]) -> _QueuedInteraction:
        if not isinstance(payload, Mapping):
            raise InteractionValidationError("浏览器操作格式无效")
        kind = str(payload.get("kind") or "").strip()
        if kind not in ALLOWED_KINDS:
            raise InteractionValidationError("浏览器操作类型无效")
        frame_revision = payload.get("frame_revision")
        if isinstance(frame_revision, bool) or not isinstance(frame_revision, int):
            raise InteractionValidationError("浏览器画面版本无效")

        if kind == "gesture":
            raw_points = payload.get("points")
            if not isinstance(raw_points, list) or not (1 <= len(raw_points) <= MAX_POINTS):
                raise InteractionValidationError("手势轨迹点数量无效")
            points: list[tuple[float, float]] = []
            for point in raw_points:
                if not isinstance(point, Mapping):
                    raise InteractionValidationError("手势轨迹格式无效")
                x = self._finite_number(point.get("x"), "x")
                y = self._finite_number(point.get("y"), "y")
                if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                    raise InteractionValidationError("手势坐标超出画面")
                points.append((x, y))
            duration_ms = payload.get("duration_ms", 0)
            if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
                raise InteractionValidationError("手势时长无效")
            if not 0 <= duration_ms <= MAX_DURATION_MS:
                raise InteractionValidationError("手势时长超出限制")
            return _QueuedInteraction(
                kind=kind,
                frame_revision=frame_revision,
                points=tuple(points),
                duration_ms=duration_ms,
            )

        if kind == "text":
            value = payload.get("text")
            if not isinstance(value, str) or not (1 <= len(value) <= MAX_TEXT_LENGTH):
                raise InteractionValidationError("输入文本长度无效")
            return _QueuedInteraction(
                kind=kind,
                frame_revision=frame_revision,
                text=value,
            )

        if kind == "key":
            key = str(payload.get("key") or "")
            if key not in ALLOWED_KEYS:
                raise InteractionValidationError("按键不在允许范围内")
            return _QueuedInteraction(
                kind=kind,
                frame_revision=frame_revision,
                key=key,
            )

        delta_x = self._finite_number(payload.get("delta_x", 0), "delta_x")
        delta_y = self._finite_number(payload.get("delta_y", 0), "delta_y")
        if (
            abs(delta_x) > MAX_WHEEL_DELTA
            or abs(delta_y) > MAX_WHEEL_DELTA
            or (delta_x == 0 and delta_y == 0)
        ):
            raise InteractionValidationError("滚动距离无效")
        return _QueuedInteraction(
            kind=kind,
            frame_revision=frame_revision,
            delta_x=delta_x,
            delta_y=delta_y,
        )

    def _execute(self, page: Any, action: _QueuedInteraction) -> None:
        with self._lock:
            width = self._viewport_width
            height = self._viewport_height
        if action.kind == "gesture":
            coordinates = [
                (x * width, y * height)
                for x, y in action.points
            ]
            first_x, first_y = coordinates[0]
            page.mouse.move(first_x, first_y)
            page.mouse.down()
            if len(coordinates) > 1:
                delay = action.duration_ms / max(1, len(coordinates) - 1)
                for x, y in coordinates[1:]:
                    page.mouse.move(x, y, steps=1)
                    if delay > 0:
                        page.wait_for_timeout(delay)
            page.mouse.up()
            return
        if action.kind == "text":
            page.keyboard.insert_text(action.text)
            return
        if action.kind == "key":
            page.keyboard.press(action.key)
            return
        page.mouse.wheel(action.delta_x, action.delta_y)


__all__ = [
    "ALLOWED_KEYS",
    "BrowserInteractionChannel",
    "BrowserInteractionError",
    "InteractionQueueFull",
    "InteractionRateLimited",
    "InteractionUnavailable",
    "InteractionValidationError",
    "StaleFrameRevision",
]
