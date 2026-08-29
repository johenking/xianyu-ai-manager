import asyncio
import hashlib
import json
import math
import re
import time
from html import escape as html_escape
import base64
import os
import random
from enum import Enum
from urllib.parse import quote, urlparse, urlunparse
from loguru import logger
import websockets
from utils.xianyu_utils import (
    decrypt, generate_mid, generate_uuid, trans_cookies,
    generate_device_id, generate_sign
)
from config import (
    WEBSOCKET_URL, HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT,
    TOKEN_REFRESH_INTERVAL, TOKEN_RETRY_INTERVAL, COOKIES_STR,
    LOG_CONFIG, AUTO_REPLY, DEFAULT_HEADERS, WEBSOCKET_HEADERS,
    APP_CONFIG, API_ENDPOINTS,
    L3_KEEPALIVE_ENABLED, L3_KEEPALIVE_INTERVAL,
)
from config import config as cfg  # 导入config实例（不是模块），使用别名避免冲突
import sys
import aiohttp
from collections import defaultdict
from db_manager import FULFILLMENT_API_PROTOCOL, db_manager
from delivery_stage_metrics import (
    STAGE_GATE,
    STAGE_PAID,
    record_stage as record_delivery_stage,
)
from account_session_refresh import (
    RETRYABLE_SESSION_ERROR_CODES,
    is_retryable_session_error_code,
)
from session_registry import sanitize_log_record, sanitize_runtime_error
from utils.xianyu_session_probe import (
    PROBE_EXPIRED,
    PROBE_RETRYABLE_ERROR,
    PROBE_VERIFICATION_REQUIRED,
    SessionProbeResult,
    cookies_to_string as probe_cookies_to_string,
    detect_default_browser_user_agent,
    probe_message_session_async,
)
from utils.outbound_http import (
    OutboundRequestError,
    outbound_target_label,
    request_public_http,
)
from utils.outbound_smtp import open_public_smtp
from utils.xianyu_message import (
    IMAGE_PLACEHOLDER,
    extract_inbound_content,
    message_has_content,
    normalize_operation_message,
)

AUTO_DELIVERY_SOURCE_PAID_NOTICE = "paid_notice"
AUTO_DELIVERY_SOURCE_BARGAIN_FREESHIPPING = "bargain_freeshipping"

# WebSocket 超时护栏：没有这些上限时，半开连接会让账号监听长期僵死却不报错，
# 表现为漏消息、漏订单，而进程看起来一切正常。
WS_OPEN_TIMEOUT = 20      # 建连握手上限
WS_CLOSE_TIMEOUT = 10     # 关闭握手上限，避免退出时卡住
WS_PING_INTERVAL = 20     # 协议层 ping，与业务心跳互为兜底
WS_PING_TIMEOUT = 20      # 协议层 pong 超时即判定连接不可用
WS_SEND_TIMEOUT = 10      # 单次业务帧发送上限，避免 await send 永久挂起

# 发货前付款核验的翻页上限。该核验持有账号订单同步锁，页数直接决定锁占用时长。
DELIVERY_VERIFY_MAX_PAGES = 5
FULFILLMENT_API_MAX_ATTEMPTS = 4

# 付款核验的短退避重试间隔（秒）。平台订单详情/列表相对付款消息有秒级同步
# 延迟：实测正常单 2~10 秒可见，但个别单在核验段空耗 23~49 秒才被 30 秒兜底
# 轮询捞回。
INVITE_VERIFY_RETRY_DELAYS_SECONDS = (2.0, 4.0, 8.0)

# 只有「平台数据尚未就绪」的两种结果值得重试：订单还没出现在平台列表/详情里，
# 或者出现了但业务类型标记还没传播完（此时分类器只能失败关闭判 unknown）。
# 登录失效、身份不符、lead 单等确定性失败一律立即放弃，不拖住热路径。
INVITE_VERIFY_RETRYABLE_ERROR_CODES = frozenset({
    "order_not_observed",
    "order_business_type_unconfirmed",
})

# Shadow 默认开启，仍可通过环境变量立即关闭。
AI_REPLY_SHADOW_ENABLED = os.getenv("AI_REPLY_SHADOW_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on",
}
AI_REPLY_SHADOW_TIMEOUT_SECONDS = 8
_AI_REPLY_SHADOW_SEMAPHORE = None
_AI_REPLY_SHADOW_LOOP = None


def _get_ai_reply_shadow_semaphore():
    """单 worker 事件循环内共享一个 Shadow 模型并发槽。"""
    global _AI_REPLY_SHADOW_SEMAPHORE, _AI_REPLY_SHADOW_LOOP
    loop = asyncio.get_running_loop()
    if _AI_REPLY_SHADOW_SEMAPHORE is None or _AI_REPLY_SHADOW_LOOP is not loop:
        _AI_REPLY_SHADOW_SEMAPHORE = asyncio.Semaphore(1)
        _AI_REPLY_SHADOW_LOOP = loop
    return _AI_REPLY_SHADOW_SEMAPHORE

# 只能由人工完成认证才可离开的会话状态。任何自动路径把它们改写成 failed，
# 都会让监听器闸门失灵：账号一边无限重连，一边不再对外显示"需人工重新登录"。
HUMAN_ACTION_SESSION_STATES = frozenset({
    "manual_reauth_required",
    "action_required",
    "verification_required",
})

# L3 主动保活发现记忆不可用时，允许趁会话仍有效原地重建档案的错误码。
# 只收录「档案本身坏了/没了」这类重建必有收益的情形；风控与身份类错误
# （human_verification_required、account_mismatch 等）重建也无济于事，不碰。
L3_KEEPALIVE_RESEED_CODES = frozenset({
    "fast_entry_unavailable",
    "profile_missing",
    "profile_corrupt",
})

# 连续失败久拖不决时的退避下限（阈值, 秒）。登录态失效与平台风控不会因为每 30 秒
# 重连一次而自愈，高频重连只是持续打平台。阈值从高到低匹配。
PROLONGED_FAILURE_BACKOFF_TIERS = ((50, 1800.0), (20, 300.0))

# 邮件正文里 "键: 值" 行的识别模式（键不超过 12 字，支持中英文冒号），命中的行
# 会渲染成加粗的信息表格，例如 "账号ID: 123" / "异常时间: ..."。
_EMAIL_FACT_LINE_PATTERN = re.compile(r"^([^:：]{1,12})[:：]\s*(.+)$")


def render_notification_email_html(message: str) -> str:
    """把纯文本通知渲染成邮件 HTML：首行作大标题，键值行进表格，其余按段落。

    邮件客户端只认内联样式且不能引用外部资源；正文来自运行时数据（账号、
    平台返回的错误信息），全部转义后再拼进模板。
    """
    lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
    title = lines[0] if lines else "告警通知"
    facts: list[tuple[str, str]] = []
    paragraphs: list[str] = []
    for line in lines[1:]:
        matched = _EMAIL_FACT_LINE_PATTERN.match(line)
        if matched:
            facts.append((matched.group(1).strip(), matched.group(2).strip()))
        else:
            paragraphs.append(line)

    blocks: list[str] = []
    if facts:
        fact_rows = "".join(
            "<tr>"
            '<td style="padding:7px 16px 7px 0;color:#8a8f99;font-size:13px;'
            f'white-space:nowrap;vertical-align:top;">{html_escape(key)}</td>'
            '<td style="padding:7px 0;color:#1f2329;font-size:14px;font-weight:600;'
            f'word-break:break-all;">{html_escape(value)}</td>'
            "</tr>"
            for key, value in facts
        )
        blocks.append(
            '<tr><td style="padding:14px 24px 6px;">'
            '<table role="presentation" cellpadding="0" cellspacing="0" '
            'style="width:100%;background:#f8f9fb;border-radius:8px;'
            f'padding:10px 16px;">{fact_rows}</table></td></tr>'
        )
    blocks.extend(
        '<tr><td style="padding:8px 24px;color:#4a4f59;font-size:14px;'
        f'line-height:1.7;">{html_escape(paragraph)}</td></tr>'
        for paragraph in paragraphs
    )

    sent_at = time.strftime("%Y-%m-%d %H:%M:%S")
    return (
        '<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f4f5f7;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f4f5f7;padding:24px 12px;"><tr><td align="center">'
        '<table role="presentation" cellpadding="0" cellspacing="0" '
        'style="max-width:560px;width:100%;background:#ffffff;border-radius:12px;'
        "overflow:hidden;border:1px solid #e8eaed;font-family:-apple-system,"
        "BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;\">"
        '<tr><td style="background:#ff5000;padding:14px 24px;color:#ffffff;'
        'font-size:15px;font-weight:700;letter-spacing:2px;">闲鱼监控 · 告警通知</td></tr>'
        '<tr><td style="padding:22px 24px 4px;color:#1f2329;font-size:18px;'
        f'font-weight:700;line-height:1.5;">{html_escape(title)}</td></tr>'
        f'{"".join(blocks)}'
        '<tr><td style="padding:18px 24px 20px;color:#a6abb5;font-size:12px;">'
        f"此邮件由闲鱼监控系统自动发送 · {sent_at}</td></tr>"
        "</table></td></tr></table></body></html>"
    )


def summarize_notification_email_subject(message: str) -> str:
    """取首行做邮件主题摘要，让收件箱列表里一眼看出发生了什么。"""
    first_line = next(
        (line.strip() for line in str(message or "").splitlines() if line.strip()),
        "",
    )
    summary = first_line[:40] if first_line else "告警通知"
    return f"【闲鱼监控】{summary}"


class DirectMessageNotSubmitted(RuntimeError):
    """The direct conversation was not created, so no message write occurred."""


def _invite_bridge_owns_item(cookie_id: str, item_id: str) -> bool:
    enabled = os.getenv("XIANYU_INVITE_BRIDGE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return False
    return db_manager.is_invite_auto_fulfillment_enabled(cookie_id, item_id)


def _delivery_identity_is_confirmed(
    expected_item_id: str,
    expected_buyer_id: str,
    returned_item_id: str,
    returned_buyer_id: str,
) -> bool:
    """Require exact, non-placeholder identities before an automatic fulfillment."""
    expected_item = str(expected_item_id or "").strip()
    expected_buyer = str(expected_buyer_id or "").strip()
    returned_item = str(returned_item_id or "").strip()
    returned_buyer = str(returned_buyer_id or "").strip()
    invalid_values = {
        "",
        "unknown",
        "unknown_user",
        "unknown_buyer",
        "none",
        "null",
        "未知商品",
        "未知用户",
        "未知买家",
    }
    return (
        expected_item.lower() not in invalid_values
        and expected_buyer.lower() not in invalid_values
        and returned_item.lower() not in invalid_values
        and returned_buyer.lower() not in invalid_values
        and expected_item == returned_item
        and expected_buyer == returned_buyer
    )


def _upsert_realtime_customer_profile(
    database,
    cookie_id,
    sender_user_id,
    sender_nickname,
    observed_at,
) -> bool:
    """只用实时消息里的 sender 身份维护客户档案，不接触收货字段。"""
    account_id = str(cookie_id or '').strip()
    buyer_id = str(sender_user_id or '').strip()
    nickname = str(sender_nickname or '').strip()
    try:
        moment = float(observed_at)
    except (TypeError, ValueError):
        return False
    if (
        not account_id
        or not buyer_id
        or buyer_id.lower() in {'unknown', 'unknown_user', 'none', 'null'}
        or not nickname
        or nickname in {'未知用户', '未知买家'}
        or not math.isfinite(moment)
        or moment <= 0
    ):
        return False
    try:
        return bool(database.upsert_customer_observation(
            cookie_id=account_id,
            buyer_id=buyer_id,
            display_name=nickname,
            avatar_url='',
            source='realtime_message',
            observed_at=moment,
        ))
    except Exception as exc:
        logger.warning(f"实时客户身份记录失败: {type(exc).__name__}")
        return False


# 滑块验证补丁已废弃，使用集成的 Playwright 登录方法
# 不再需要猴子补丁，所有功能已集成到 XianyuSliderStealth 类中

class ConnectionState(Enum):
    """WebSocket连接状态枚举"""
    DISCONNECTED = "disconnected"  # 未连接
    CONNECTING = "connecting"  # 连接中
    CONNECTED = "connected"  # 已连接
    RECONNECTING = "reconnecting"  # 重连中
    FAILED = "failed"  # 连接失败
    CLOSED = "closed"  # 已关闭


_H5_API_HOST_OK_CACHE = {}


def _is_h5_api_host_reachable(host: str, timeout: float = 3.0) -> bool:
    """Probe H5 API TLS reachability once per host for this process."""
    cached = _H5_API_HOST_OK_CACHE.get(host)
    if cached is not None:
        return cached

    import socket
    import ssl

    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host):
                pass
        _H5_API_HOST_OK_CACHE[host] = True
        return True
    except Exception as exc:
        logger.warning(f"H5 API Host TLS 探测失败: {host} -> {exc}")
        _H5_API_HOST_OK_CACHE[host] = False
        return False


def _resolve_h5_api_url(api_url: str) -> str:
    """Use Taobao H5 API host when Goofish H5 API TLS is blocked locally."""
    if not api_url:
        return api_url

    preferred_host = os.getenv("XIANYU_H5_API_HOST", "").strip()
    parsed = urlparse(api_url)
    if preferred_host:
        return urlunparse(parsed._replace(netloc=preferred_host))

    if parsed.netloc == "h5api.m.goofish.com" and not _is_h5_api_host_reachable(parsed.netloc):
        fallback_host = "h5api.m.taobao.com"
        logger.warning(f"H5 API Host 自动回退: {parsed.netloc} -> {fallback_host}")
        return urlunparse(parsed._replace(netloc=fallback_host))

    return api_url


def normalize_catalog_image_url(value) -> str:
    """Return a browser-safe absolute image URL without fetching the asset."""
    url = str(value or '').strip()
    if url.startswith('//'):
        url = f'https:{url}'
    elif url.startswith('http://'):
        url = f"https://{url[7:]}"
    if not url.startswith(('https://', 'http://')):
        return ''
    return url


def extract_catalog_image_url(card_data: dict) -> str:
    """Extract the primary seller-catalog image across known payload variants."""
    if not isinstance(card_data, dict):
        return ''
    pic_info = card_data.get('picInfo') if isinstance(card_data.get('picInfo'), dict) else {}
    detail_params = card_data.get('detailParams') if isinstance(card_data.get('detailParams'), dict) else {}
    candidates = [pic_info.get('picUrl'), detail_params.get('picUrl')]
    image_infos = detail_params.get('imageInfos')
    if isinstance(image_infos, str):
        try:
            image_infos = json.loads(image_infos)
        except (TypeError, ValueError, json.JSONDecodeError):
            image_infos = []
    if isinstance(image_infos, list):
        image_rows = [row for row in image_infos if isinstance(row, dict)]
        primary = next((row for row in image_rows if row.get('major') is True), None)
        if primary:
            candidates.append(primary.get('url'))
        candidates.extend(row.get('url') for row in image_rows)
    for candidate in candidates:
        normalized = normalize_catalog_image_url(candidate)
        if normalized:
            return normalized
    return ''


class AutoReplyPauseManager:
    """自动回复暂停管理器"""
    def __init__(self):
        # 暂停必须按账号和会话共同隔离，避免多个卖家账号使用同一 chat_id 时串台。
        self.paused_chats = {}

    def pause_chat(self, chat_id: str, cookie_id: str):
        """暂停指定chat_id的自动回复，使用账号特定的暂停时间"""
        # 获取账号特定的暂停时间
        try:
            from db_manager import db_manager
            pause_minutes = db_manager.get_cookie_pause_duration(cookie_id)
        except Exception as e:
            logger.error(f"获取账号 {cookie_id} 暂停时间失败: {e}，使用默认10分钟")
            pause_minutes = 10

        # 如果暂停时间为0，表示不暂停
        if pause_minutes == 0:
            logger.info(f"【{cookie_id}】检测到手动发出消息，但暂停时间设置为0，不暂停自动回复")
            return

        pause_duration_seconds = pause_minutes * 60
        pause_until = time.time() + pause_duration_seconds
        self.paused_chats[(cookie_id, chat_id)] = pause_until

        # 计算暂停结束时间
        end_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(pause_until))
        logger.info(f"【{cookie_id}】检测到手动发出消息，自动回复暂停{pause_minutes}分钟，恢复时间: {end_time}")

    def is_chat_paused(self, chat_id: str, cookie_id: str) -> bool:
        """检查指定账号的chat_id是否处于暂停状态"""
        pause_key = (cookie_id, chat_id)
        if pause_key not in self.paused_chats:
            return False

        current_time = time.time()
        pause_until = self.paused_chats[pause_key]

        if current_time >= pause_until:
            # 暂停时间已过，移除记录
            del self.paused_chats[pause_key]
            return False

        return True

    def get_remaining_pause_time(self, chat_id: str, cookie_id: str) -> int:
        """获取指定账号的chat_id剩余暂停时间（秒）"""
        pause_key = (cookie_id, chat_id)
        if pause_key not in self.paused_chats:
            return 0

        current_time = time.time()
        pause_until = self.paused_chats[pause_key]
        remaining = max(0, int(pause_until - current_time))

        return remaining

    def cleanup_expired_pauses(self):
        """清理已过期的暂停记录"""
        current_time = time.time()
        expired_keys = [pause_key for pause_key, pause_until in self.paused_chats.items()
                        if current_time >= pause_until]

        for pause_key in expired_keys:
            del self.paused_chats[pause_key]


# 全局暂停管理器实例
pause_manager = AutoReplyPauseManager()

def log_captcha_event(cookie_id: str, event_type: str, success: bool = None, details: str = ""):
    """
    简单记录滑块验证事件到txt文件

    Args:
        cookie_id: 账号ID
        event_type: 事件类型 (检测到/开始处理/成功/失败)
        success: 是否成功 (None表示进行中)
        details: 详细信息
    """
    try:
        log_dir = 'logs'
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, 'captcha_verification.txt')

        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        status = "成功" if success is True else "失败" if success is False else "进行中"

        account_ref = hashlib.sha256(str(cookie_id).encode("utf-8")).hexdigest()[:10]
        log_entry = f"[{timestamp}] 【account_{account_ref}】{event_type} - {status}"
        if details:
            log_entry += f" - {details}"
        log_entry += "\n"

        fd = os.open(log_file, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        os.chmod(log_file, 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as f:
            f.write(log_entry)

    except Exception as e:
        logger.error(f"记录滑块验证日志失败: {e}")

# 日志配置
log_dir = 'logs'
os.makedirs(log_dir, exist_ok=True)
log_path = os.path.join(log_dir, f"xianyu_{time.strftime('%Y-%m-%d')}.log")


def _secure_log_opener(path, flags):
    return os.open(path, flags, 0o600)


def _mask_account_ids_in_log(record):
    sanitize_log_record(record)


logger.configure(patcher=_mask_account_ids_in_log)
logger.add(
    log_path,
    rotation=LOG_CONFIG.get('rotation', '1 day'),
    retention=LOG_CONFIG.get('retention', '7 days'),
    compression=LOG_CONFIG.get('compression', 'zip'),
    level=LOG_CONFIG.get('level', 'DEBUG'),
    format=LOG_CONFIG.get('format', '<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>'),
    encoding='utf-8',
    enqueue=True,
    opener=_secure_log_opener,
)

class XianyuLive:
    # 类级别的锁字典，为每个order_id维护一个锁（用于自动发货）
    _order_locks = defaultdict(lambda: asyncio.Lock())
    # 记录锁的最后使用时间，用于清理
    _lock_usage_times = {}
    # 记录锁的持有状态和释放时间 {lock_key: {'locked': bool, 'release_time': float, 'task': asyncio.Task}}
    _lock_hold_info = {}

    # 商品详情缓存（24小时有效）
    _item_detail_cache = {}  # {item_id: {'detail': str, 'timestamp': float, 'access_time': float}}
    _item_detail_cache_lock = asyncio.Lock()
    _item_detail_cache_max_size = 1000  # 最大缓存1000个商品
    _item_detail_cache_ttl = 24 * 60 * 60  # 24小时TTL

    # 类级别的实例管理字典，用于API调用
    _instances = {}  # {cookie_id: XianyuLive实例}
    _instances_lock = asyncio.Lock()

    # 类级别的密码登录时间记录，用于防止重复登录
    _last_password_login_time = {}  # {cookie_id: timestamp}
    _password_login_cooldown = 60  # 密码登录冷却时间：60秒
    _last_l3_refresh_time = {}
    _l3_refresh_cooldown = 60

    def _safe_str(self, e):
        """安全地将异常转换为字符串"""
        try:
            return sanitize_runtime_error(e)
        except Exception:
            return type(e).__name__

    @staticmethod
    def _log_websocket_connection_failure(
        cookie_id: str,
        *,
        error_type: str,
        error_message: str,
        failure_count: int,
        max_failures: int,
    ) -> bool:
        safe_message = sanitize_runtime_error(error_message)
        is_expected_disconnect = (
            "ConnectionClosed" in error_type
            or "IncompleteReadError" in error_type
            or "no close frame received or sent" in safe_message
        )
        if is_expected_disconnect:
            logger.warning(
                f"【{cookie_id}】WebSocket连接已关闭 "
                f"({failure_count}/{max_failures})"
            )
            logger.warning(f"【{cookie_id}】关闭原因: {safe_message}")
            return True

        logger.error(
            f"【{cookie_id}】WebSocket连接异常 "
            f"({failure_count}/{max_failures})"
        )
        logger.error(f"【{cookie_id}】异常类型: {error_type}")
        logger.error(f"【{cookie_id}】异常信息: {safe_message}")
        return False

    def _set_connection_state(self, new_state: ConnectionState, reason: str = ""):
        """设置连接状态并记录日志"""
        if self.connection_state != new_state:
            old_state = self.connection_state
            self.connection_state = new_state
            self.last_state_change_time = time.time()

            # 记录状态转换
            state_msg = f"【{self.cookie_id}】连接状态: {old_state.value} → {new_state.value}"
            if reason:
                state_msg += f" ({reason})"

            # 根据状态严重程度选择日志级别
            if new_state == ConnectionState.FAILED:
                logger.error(state_msg)
            elif new_state == ConnectionState.RECONNECTING:
                logger.warning(state_msg)
            elif new_state == ConnectionState.CONNECTED:
                logger.success(state_msg)
            else:
                logger.info(state_msg)

    async def _interruptible_sleep(self, duration: float):
        """可中断的sleep，将长时间sleep拆分成多个短时间sleep，以便及时响应取消信号

        Args:
            duration: 总睡眠时间（秒）
        """
        # 将长时间sleep拆分成多个1秒的短sleep，这样可以及时响应取消信号
        chunk_size = 1.0  # 每次sleep 1秒
        remaining = duration

        while remaining > 0:
            sleep_time = min(chunk_size, remaining)
            try:
                await asyncio.sleep(sleep_time)
                remaining -= sleep_time
            except asyncio.CancelledError:
                # 如果收到取消信号，立即抛出
                raise

    def _reset_background_tasks(self):
        """直接重置后台任务引用，不等待取消（用于快速重连）

        注意：只重置心跳任务，因为只有心跳任务依赖WebSocket连接。
        其他任务（Token刷新、清理、Cookie刷新）不依赖WebSocket，可以继续运行。
        """
        logger.info(f"【{self.cookie_id}】准备重置后台任务引用（仅重置依赖WebSocket的任务）...")

        # 只处理心跳任务（依赖WebSocket，需要重启）
        if self.heartbeat_task:
            status = "已完成" if self.heartbeat_task.done() else "运行中"
            logger.info(f"【{self.cookie_id}】发现心跳任务（状态: {status}），需要重置（因为依赖WebSocket连接）")
            # 尝试取消心跳任务（但不等待）
            if not self.heartbeat_task.done():
                try:
                    self.heartbeat_task.cancel()
                    logger.debug(f"【{self.cookie_id}】已发送取消信号给心跳任务（不等待响应）")
                except Exception as e:
                    logger.warning(f"【{self.cookie_id}】取消心跳任务失败: {e}")
            # 重置心跳任务引用
            self.heartbeat_task = None
            logger.info(f"【{self.cookie_id}】心跳任务引用已重置")
        else:
            logger.info(f"【{self.cookie_id}】没有心跳任务需要重置")

        # 检查其他任务的状态（这些任务不依赖WebSocket，不需要重启）
        other_tasks_status = []
        if self.token_refresh_task:
            status = "已完成" if self.token_refresh_task.done() else "运行中"
            other_tasks_status.append(f"Token刷新任务({status})")
        if self.cleanup_task:
            status = "已完成" if self.cleanup_task.done() else "运行中"
            other_tasks_status.append(f"清理任务({status})")
        if self.cookie_refresh_task:
            status = "已完成" if self.cookie_refresh_task.done() else "运行中"
            other_tasks_status.append(f"Cookie刷新任务({status})")
        if self.item_sync_task:
            status = "已完成" if self.item_sync_task.done() else "运行中"
            other_tasks_status.append(f"商品同步任务({status})")

        if other_tasks_status:
            logger.info(f"【{self.cookie_id}】其他任务继续运行（不依赖WebSocket）: {', '.join(other_tasks_status)}")
        else:
            logger.info(f"【{self.cookie_id}】没有其他任务在运行")

        logger.info(f"【{self.cookie_id}】任务重置完成，可以立即创建新的心跳任务")

    async def _cancel_background_tasks(self):
        """取消并清理所有后台任务（保留此方法用于程序退出时的完整清理）"""
        try:
            tasks_to_cancel = []

            # 收集所有需要取消的任务（只收集未完成的任务）
            if self.heartbeat_task:
                if not self.heartbeat_task.done():
                    tasks_to_cancel.append(("心跳任务", self.heartbeat_task))
                else:
                    logger.debug(f"【{self.cookie_id}】心跳任务已完成，跳过")

            if self.token_refresh_task:
                if not self.token_refresh_task.done():
                    tasks_to_cancel.append(("Token刷新任务", self.token_refresh_task))
                else:
                    logger.debug(f"【{self.cookie_id}】Token刷新任务已完成，跳过")

            if self.cleanup_task:
                if not self.cleanup_task.done():
                    tasks_to_cancel.append(("清理任务", self.cleanup_task))
                else:
                    logger.debug(f"【{self.cookie_id}】清理任务已完成，跳过")

            if self.cookie_refresh_task:
                if not self.cookie_refresh_task.done():
                    tasks_to_cancel.append(("Cookie刷新任务", self.cookie_refresh_task))
                else:
                    logger.debug(f"【{self.cookie_id}】Cookie刷新任务已完成，跳过")

            if self.item_sync_task:
                if not self.item_sync_task.done():
                    tasks_to_cancel.append(("商品同步任务", self.item_sync_task))
                else:
                    logger.debug(f"【{self.cookie_id}】商品同步任务已完成，跳过")

            if not tasks_to_cancel:
                logger.info(f"【{self.cookie_id}】没有后台任务需要取消（所有任务已完成或不存在）")
                # 立即重置任务引用
                self.heartbeat_task = None
                self.token_refresh_task = None
                self.cleanup_task = None
                self.cookie_refresh_task = None
                self.item_sync_task = None
                return

            logger.info(f"【{self.cookie_id}】开始取消 {len(tasks_to_cancel)} 个未完成的后台任务...")

            # 取消所有任务
            for task_name, task in tasks_to_cancel:
                try:
                    if task.done():
                        logger.info(f"【{self.cookie_id}】任务已完成，跳过取消: {task_name}")
                    else:
                        task.cancel()
                        logger.info(f"【{self.cookie_id}】已发送取消信号: {task_name}")
                except Exception as e:
                    logger.warning(f"【{self.cookie_id}】取消任务失败 {task_name}: {e}")

            # 等待所有任务完成取消，使用合理的超时时间
            # 现在任务中已经添加了 await asyncio.sleep(0) 来让出控制权，应该能够响应取消信号
            tasks = [task for _, task in tasks_to_cancel]
            logger.info(f"【{self.cookie_id}】等待 {len(tasks)} 个任务响应取消信号...")

            wait_timeout = 5.0  # 增加超时时间到5秒，给任务更多时间响应取消信号

            start_time = time.time()
            try:
                # 只等待未完成的任务
                pending_tasks_list = [task for task in tasks if not task.done()]

                # 记录每个任务的状态
                for task_name, task in tasks_to_cancel:
                    status = "已完成" if task.done() else "运行中"
                    logger.info(f"【{self.cookie_id}】任务状态: {task_name} - {status}")

                if not pending_tasks_list:
                    logger.info(f"【{self.cookie_id}】所有任务已完成，无需等待")
                else:
                    logger.info(f"【{self.cookie_id}】等待 {len(pending_tasks_list)} 个未完成任务响应（超时时间: {wait_timeout}秒）...")
                    try:
                        # 使用 wait 等待任务完成，设置超时
                        logger.debug(f"【{self.cookie_id}】开始调用 asyncio.wait()...")
                        done, pending = await asyncio.wait(
                            pending_tasks_list,
                            timeout=wait_timeout,
                            return_when=asyncio.ALL_COMPLETED
                        )
                        elapsed = time.time() - start_time
                        logger.info(f"【{self.cookie_id}】asyncio.wait() 返回，耗时 {elapsed:.3f}秒，已完成: {len(done)}，未完成: {len(pending)}")

                        # 检查已完成的任务，并记录详细信息
                        for task_name, task in tasks_to_cancel:
                            if task in done:
                                try:
                                    task.result()
                                    logger.warning(f"【{self.cookie_id}】⚠️ 任务正常完成（非取消）: {task_name}")
                                except asyncio.CancelledError:
                                    logger.info(f"【{self.cookie_id}】✅ 任务已成功取消: {task_name}")
                                except Exception as e:
                                    logger.warning(f"【{self.cookie_id}】⚠️ 任务取消时出现异常 {task_name}: {e}")

                        if pending:
                            # 找出未完成的任务名称和详细信息
                            pending_names = []
                            for task_name, task in tasks_to_cancel:
                                if task in pending:
                                    pending_names.append(task_name)
                                    # 记录未完成任务的状态
                                    if task.done():
                                        try:
                                            task.result()
                                            logger.warning(f"【{self.cookie_id}】任务在等待期间完成: {task_name}")
                                        except asyncio.CancelledError:
                                            logger.info(f"【{self.cookie_id}】任务在等待期间被取消: {task_name}")
                                        except Exception as e:
                                            logger.warning(f"【{self.cookie_id}】任务在等待期间异常 {task_name}: {e}")
                                    else:
                                        logger.warning(f"【{self.cookie_id}】任务仍未完成: {task_name} (done={task.done()})")

                            logger.warning(f"【{self.cookie_id}】等待超时 ({elapsed:.3f}秒)，以下任务可能仍在运行: {', '.join(pending_names)}")

                            # 强制取消所有未完成的任务（再次尝试）
                            for task_name, task in tasks_to_cancel:
                                if task in pending and not task.done():
                                    try:
                                        task.cancel()
                                        logger.warning(f"【{self.cookie_id}】强制取消任务: {task_name}")
                                    except Exception as e:
                                        logger.warning(f"【{self.cookie_id}】强制取消任务失败 {task_name}: {e}")

                            # 再等待一小段时间，看是否有任务响应
                            if pending:
                                try:
                                    done2, pending2 = await asyncio.wait(pending, timeout=1.0, return_when=asyncio.ALL_COMPLETED)
                                    for task_name, task in tasks_to_cancel:
                                        if task in done2:
                                            try:
                                                task.result()
                                            except asyncio.CancelledError:
                                                logger.info(f"【{self.cookie_id}】任务在二次等待期间被取消: {task_name}")
                                            except Exception as e:
                                                logger.warning(f"【{self.cookie_id}】任务在二次等待期间异常 {task_name}: {e}")
                                except Exception as e:
                                    logger.warning(f"【{self.cookie_id}】二次等待任务时出错: {e}")

                            logger.warning(f"【{self.cookie_id}】强制继续重连流程，未完成的任务将在后台继续运行（但已标记为取消）")
                        else:
                            logger.info(f"【{self.cookie_id}】所有后台任务已取消 (耗时 {elapsed:.3f}秒)")

                    except Exception as e:
                        elapsed = time.time() - start_time
                        logger.warning(f"【{self.cookie_id}】等待任务时出错 (耗时 {elapsed:.3f}秒): {e}")
                        import traceback
                        logger.warning(f"【{self.cookie_id}】等待任务异常堆栈:\n{traceback.format_exc()}")

            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"【{self.cookie_id}】等待任务取消时出错 (耗时 {elapsed:.3f}秒): {e}")
                import traceback
                logger.error(f"【{self.cookie_id}】等待任务取消异常堆栈:\n{traceback.format_exc()}")

            logger.info(f"【{self.cookie_id}】任务取消流程完成，继续重连流程")

            # 最后检查一次所有任务的状态
            for task_name, task in tasks_to_cancel:
                if task and not task.done():
                    logger.warning(f"【{self.cookie_id}】⚠️ 任务取消流程完成后，任务仍未完成: {task_name} (done={task.done()})")
                elif task and task.done():
                    logger.debug(f"【{self.cookie_id}】✅ 任务已完成: {task_name}")

        finally:
            # 使用 finally 确保无论发生什么情况都会重置任务引用
            # 这样可以保证下次重连时所有任务都会被重新创建
            self.heartbeat_task = None
            self.token_refresh_task = None
            self.cleanup_task = None
            self.cookie_refresh_task = None
            self.item_sync_task = None
            logger.info(f"【{self.cookie_id}】后台任务引用已全部重置")

    def _calculate_retry_delay(self, error_msg: str) -> float:
        """根据错误类型和失败次数计算重试延迟。

        延迟必须带随机抖动：平台侧抖动往往同时打断多个账号，若各账号都按同一条
        确定性曲线重连，会在同一时刻集体回冲，反而更容易触发限流。
        """
        # WebSocket意外断开 - 短延迟
        if "no close frame received or sent" in error_msg:
            base_delay = min(3 * self.connection_failures, 15)

        # 网络连接问题 - 长延迟
        elif "Connection refused" in error_msg or "timeout" in error_msg.lower():
            base_delay = min(10 * self.connection_failures, 60)

        # 其他未知错误 - 中等延迟
        else:
            base_delay = min(5 * self.connection_failures, 30)

        return self._apply_jitter(self._escalate_for_prolonged_failure(base_delay))

    def _escalate_for_prolonged_failure(self, base_delay: float) -> float:
        """久拖不决的连续失败抬高退避下限，避免长时间高频重连打平台。"""
        for threshold, floor_delay in PROLONGED_FAILURE_BACKOFF_TIERS:
            if self.connection_failures >= threshold:
                return max(base_delay, floor_delay)
        return base_delay

    @staticmethod
    def _apply_jitter(base_delay: float, ratio: float = 0.3) -> float:
        """给延迟叠加 ±ratio 的随机抖动，避免多账号同步回冲。"""
        if base_delay <= 0:
            return 0.0
        jittered = base_delay * random.uniform(1 - ratio, 1 + ratio)
        return round(max(0.5, jittered), 2)

    def _cleanup_instance_caches(self):
        """清理实例级别的缓存，防止内存泄漏"""
        try:
            current_time = time.time()
            cleaned_total = 0

            # 清理过期的通知记录（保留30分钟内的，从1小时优化）
            max_notification_age = 1800  # 30分钟（从3600优化）
            expired_notifications = [
                key for key, last_time in self.last_notification_time.items()
                if current_time - last_time > max_notification_age
            ]
            for key in expired_notifications:
                del self.last_notification_time[key]
            if expired_notifications:
                cleaned_total += len(expired_notifications)
                logger.warning(f"【{self.cookie_id}】清理了 {len(expired_notifications)} 个过期通知记录")

            # 清理过期的发货记录（保留30分钟内的）
            max_delivery_age = 1800  # 30分钟
            expired_deliveries = [
                order_id for order_id, last_time in self.last_delivery_time.items()
                if current_time - last_time > max_delivery_age
            ]
            for order_id in expired_deliveries:
                del self.last_delivery_time[order_id]
            if expired_deliveries:
                cleaned_total += len(expired_deliveries)
                logger.warning(f"【{self.cookie_id}】清理了 {len(expired_deliveries)} 个过期发货记录")

            # 清理过期的订单确认记录（保留30分钟内的）
            max_confirm_age = 1800  # 30分钟
            expired_confirms = [
                order_id for order_id, last_time in self.confirmed_orders.items()
                if current_time - last_time > max_confirm_age
            ]
            for order_id in expired_confirms:
                del self.confirmed_orders[order_id]
            if expired_confirms:
                cleaned_total += len(expired_confirms)
                logger.warning(f"【{self.cookie_id}】清理了 {len(expired_confirms)} 个过期订单确认记录")

            # 只有实际清理了内容才记录总数日志
            if cleaned_total > 0:
                logger.info(f"【{self.cookie_id}】实例缓存清理完成，共清理 {cleaned_total} 条记录")
                logger.warning(f"【{self.cookie_id}】当前缓存数量 - 通知: {len(self.last_notification_time)}, 发货: {len(self.last_delivery_time)}, 确认: {len(self.confirmed_orders)}")

        except Exception as e:
            logger.error(f"【{self.cookie_id}】清理实例缓存时出错: {self._safe_str(e)}")

    async def _cleanup_playwright_cache(self):
        """清理Playwright浏览器临时文件和缓存（Docker环境专用）"""
        try:
            import shutil
            import glob

            # 定义需要清理的临时目录路径
            temp_paths = [
                '/tmp/playwright-*',  # Playwright临时会话
                '/tmp/chromium-*',    # Chromium临时文件
                '/ms-playwright/chromium-*/Default/Cache',  # 浏览器缓存
                '/ms-playwright/chromium-*/Default/Code Cache',  # 代码缓存
                '/ms-playwright/chromium-*/Default/GPUCache',  # GPU缓存
            ]

            total_cleaned = 0
            total_size_mb = 0

            for pattern in temp_paths:
                try:
                    matching_paths = glob.glob(pattern)
                    for path in matching_paths:
                        try:
                            if os.path.exists(path):
                                # 计算大小
                                if os.path.isdir(path):
                                    size = sum(
                                        os.path.getsize(os.path.join(dirpath, filename))
                                        for dirpath, _, filenames in os.walk(path)
                                        for filename in filenames
                                    )
                                    shutil.rmtree(path, ignore_errors=True)
                                else:
                                    size = os.path.getsize(path)
                                    os.remove(path)

                                total_size_mb += size / (1024 * 1024)
                                total_cleaned += 1
                        except Exception as e:
                            logger.warning(f"清理路径 {path} 时出错: {e}")
                except Exception as e:
                    logger.warning(f"匹配路径 {pattern} 时出错: {e}")

            if total_cleaned > 0:
                logger.info(f"【{self.cookie_id}】Playwright缓存清理完成: 删除了 {total_cleaned} 个文件/目录，释放 {total_size_mb:.2f} MB")
            else:
                logger.warning(f"【{self.cookie_id}】Playwright缓存清理: 没有需要清理的临时文件")

        except Exception as e:
            logger.warning(f"【{self.cookie_id}】清理Playwright缓存时出错: {self._safe_str(e)}")

    async def _cleanup_old_logs(self, retention_days: int = 7):
        """清理过期的日志文件

        Args:
            retention_days: 保留的天数，默认7天

        Returns:
            清理的文件数量
        """
        try:
            import glob
            from datetime import datetime, timedelta

            logs_dir = "logs"
            if not os.path.exists(logs_dir):
                logger.warning(f"【{self.cookie_id}】日志目录不存在: {logs_dir}")
                return 0

            # 计算过期时间点
            cutoff_time = datetime.now() - timedelta(days=retention_days)

            # 查找所有日志文件（包括.log和.log.zip）
            log_patterns = [
                os.path.join(logs_dir, "xianyu_*.log"),
                os.path.join(logs_dir, "xianyu_*.log.zip"),
                os.path.join(logs_dir, "app_*.log"),
                os.path.join(logs_dir, "app_*.log.zip"),
            ]

            total_cleaned = 0
            total_size_mb = 0

            for pattern in log_patterns:
                log_files = glob.glob(pattern)
                for log_file in log_files:
                    try:
                        # 获取文件修改时间
                        file_mtime = datetime.fromtimestamp(os.path.getmtime(log_file))

                        # 如果文件早于保留期限，则删除
                        if file_mtime < cutoff_time:
                            file_size = os.path.getsize(log_file)
                            os.remove(log_file)
                            total_size_mb += file_size / (1024 * 1024)
                            total_cleaned += 1
                            logger.debug(f"【{self.cookie_id}】删除过期日志文件: {log_file} (修改时间: {file_mtime})")
                    except Exception as e:
                        logger.warning(f"【{self.cookie_id}】删除日志文件失败 {log_file}: {self._safe_str(e)}")

            if total_cleaned > 0:
                logger.info(f"【{self.cookie_id}】日志清理完成: 删除了 {total_cleaned} 个日志文件，释放 {total_size_mb:.2f} MB (保留 {retention_days} 天内的日志)")
            else:
                logger.debug(f"【{self.cookie_id}】日志清理: 没有需要清理的过期日志文件 (保留 {retention_days} 天)")

            return total_cleaned

        except Exception as e:
            logger.error(f"【{self.cookie_id}】清理日志文件时出错: {self._safe_str(e)}")
            return 0

    def __init__(
        self,
        cookies_str=None,
        cookie_id: str = "default",
        user_id: int = None,
        runtime_state: dict = None,
        register_instance: bool = True,
    ):
        """初始化闲鱼直播类"""
        logger.info(f"【{cookie_id}】开始初始化XianyuLive...")

        if not cookies_str:
            cookies_str = COOKIES_STR
        if not cookies_str:
            raise ValueError("未提供cookies，请在global_config.yml中配置COOKIES_STR或通过参数传入")

        logger.info(f"【{cookie_id}】解析cookies...")
        self.cookies = trans_cookies(cookies_str)
        logger.info(f"【{cookie_id}】cookies解析完成，包含字段: {list(self.cookies.keys())}")

        self.cookie_id = cookie_id  # 唯一账号标识
        self.cookies_str = cookies_str  # 保存原始cookie字符串
        self.user_id = user_id  # 保存用户ID，用于token刷新时保持正确的所有者关系
        self.base_url = WEBSOCKET_URL

        if 'unb' not in self.cookies:
            raise ValueError(f"【{cookie_id}】Cookie中缺少必需的'unb'字段，当前字段: {list(self.cookies.keys())}")

        self.myid = self.cookies['unb']
        self._account_profile_synced = False
        logger.info(f"【{cookie_id}】用户ID: {self.myid}")
        self.device_id = generate_device_id(self.myid)

        # 心跳相关配置
        self.heartbeat_interval = HEARTBEAT_INTERVAL
        self.heartbeat_timeout = HEARTBEAT_TIMEOUT
        self.last_heartbeat_time = 0
        self.last_heartbeat_response = 0
        self.heartbeat_task = None
        self.ws = None

        # Token刷新相关配置
        self.token_refresh_interval = TOKEN_REFRESH_INTERVAL
        self.token_retry_interval = TOKEN_RETRY_INTERVAL
        account_details = db_manager.get_cookie_details(self.cookie_id) or {}
        self.browser_user_agent = str(
            (runtime_state or {}).get('browser_user_agent')
            or account_details.get('browser_user_agent')
            or detect_default_browser_user_agent()
        ).strip()
        self.last_token_refresh_time = float(
            (runtime_state or {}).get('last_token_refresh_time') or 0
        )
        self.current_token = (runtime_state or {}).get('current_token') or None
        self.pending_verification_url = ""
        self.token_refresh_task = None
        self.connection_restart_flag = False  # 连接重启标志

        # 通知防重复机制
        self.last_notification_time = {}  # 记录每种通知类型的最后发送时间
        self.notification_cooldown = 300  # 5分钟内不重复发送相同类型的通知
        self.token_refresh_notification_cooldown = 18000  # Token刷新异常通知冷却时间：3小时
        self.notification_lock = asyncio.Lock()  # 通知防重复机制的异步锁

        # 自动发货防重复机制
        self.last_delivery_time = {}  # 记录每个商品的最后发货时间
        self.delivery_cooldown = 600  # 10分钟内不重复发货

        # 自动确认发货防重复机制
        self.confirmed_orders = {}  # 记录已确认发货的订单，防止重复确认
        self.order_confirm_cooldown = 600  # 10分钟内不重复确认同一订单

        self.session = None  # 用于API调用的aiohttp session

        # 启动定期清理过期暂停记录的任务
        self.cleanup_task = None

        # Cookie刷新定时任务
        self.cookie_refresh_task = None
        cookie_refresh_settings = self._load_cookie_refresh_settings()
        self.cookie_refresh_interval = cookie_refresh_settings['interval_minutes'] * 60
        try:
            from account_session_refresh import resolve_refresh_schedule_anchor

            persisted_refresh_status = db_manager.get_account_session_refresh(self.cookie_id)
            self.last_cookie_refresh_time = resolve_refresh_schedule_anchor(
                persisted_refresh_status,
            )
        except Exception as refresh_anchor_error:
            logger.warning(
                f"【{self.cookie_id}】恢复Cookie刷新调度时间失败，改从当前时间计算: "
                f"{self._safe_str(refresh_anchor_error)}"
            )
            self.last_cookie_refresh_time = time.time()
        self.cookie_refresh_lock = asyncio.Lock()  # 使用Lock防止重复执行Cookie刷新
        self.cookie_refresh_enabled = cookie_refresh_settings['enabled']  # 是否启用Cookie刷新功能

        # L3 主动保活：趁会话仍有效时用「快速进入」免密续签，避免"死后才续必失败"。
        # 默认关闭（config.L3_KEEPALIVE_ENABLED），需代理灰度验证后再开；失败只跳过、绝不动账号状态。
        self.l3_keepalive_interval = max(300, int(L3_KEEPALIVE_INTERVAL or 0))
        self.last_l3_keepalive_time = float(
            (runtime_state or {}).get('last_l3_keepalive_time') or 0
        )
        self.l3_keepalive_lock = asyncio.Lock()

        # 商品同步定时任务
        self.item_sync_task = None
        self.item_sync_enabled = cfg.get('ITEM_SYNC', {}).get('enabled', True)
        self.item_sync_interval = cfg.get('ITEM_SYNC', {}).get('interval', 3600)  # 默认1小时
        self.item_sync_max_pages = cfg.get('ITEM_SYNC', {}).get('max_pages', 5)
        self.last_item_sync_time = 0
        self.item_sync_lock = asyncio.Lock()  # 使用Lock防止重复执行商品同步

        if runtime_state:
            self.last_cookie_refresh_time = max(
                self.last_cookie_refresh_time,
                float(runtime_state.get('cookie_refresh_anchor') or 0),
            )
            self.last_item_sync_time = float(
                runtime_state.get('item_sync_anchor') or self.last_item_sync_time
            )
        self.next_cookie_refresh_time = (
            self.last_cookie_refresh_time + self._next_cookie_refresh_delay()
        )

        # 消息接收标识 - 用于控制Cookie刷新
        self.last_message_received_time = 0  # 记录上次收到消息的时间
        self.message_cookie_refresh_cooldown = 300  # 收到消息后5分钟内不执行Cookie刷新
        self.connected_at = 0.0
        self.last_inbound_at = 0.0
        self.last_inbound_kind = ""
        self.last_ai_attempt_at = 0.0
        self.last_ai_result = "never"
        self.message_ack_error_count = 0
        self.direct_send_init_error_count = 0
        self.direct_message_lock = asyncio.Lock()
        self._direct_conversation_waiters = {}
        self._websocket_bootstrap_active = False
        self._websocket_bootstrap_error = None
        self._websocket_bootstrap_sync_event = None


        # 滑块验证相关
        self.captcha_verification_count = 0  # 滑块验证次数计数器
        self.max_captcha_verification_count = 3  # 最大滑块验证次数，防止无限递归

        # WebSocket连接监控
        self.connection_state = ConnectionState.DISCONNECTED  # 连接状态
        self.connection_failures = 0  # 连续连接失败次数
        self.max_connection_failures = 5  # 最大连续失败次数
        self.last_successful_connection = 0  # 上次成功连接时间
        self.last_state_change_time = time.time()  # 上次状态变化时间

        # 后台任务追踪（用于清理未等待的任务）
        self.background_tasks = set()  # 追踪所有后台任务

        # 消息处理并发控制（防止内存泄漏）
        self.message_semaphore = asyncio.Semaphore(100)  # 最多100个并发消息处理任务
        self.active_message_tasks = 0  # 当前活跃的消息处理任务数

        # 消息防抖管理器：用于处理用户连续发送消息的情况
        # {chat_id: {'task': asyncio.Task, 'last_message': dict, 'timer': float}}
        self.message_debounce_tasks = {}  # 存储每个chat_id的防抖任务
        self.message_debounce_delay = 1  # 防抖延迟时间（秒）：用户停止发送消息1秒后才回复
        self.message_debounce_lock = asyncio.Lock()  # 防抖任务管理的锁

        # 消息去重机制：防止同一条消息被处理多次
        self.processed_message_ids = {}  # 存储已处理的消息ID和时间戳 {message_id: timestamp}
        self.processed_message_ids_lock = asyncio.Lock()  # 消息ID去重的锁
        self.processed_message_ids_max_size = 10000  # 最大保存10000个消息ID，防止内存泄漏
        self.message_expire_time = 3600  # 消息过期时间（秒），默认1小时后可以重复回复

        # 初始化订单状态处理器
        self._init_order_status_handler()

        # 注册实例到类级别字典（用于API调用）；商品同步等临时实例不得覆盖在线监听器。
        if register_instance:
            self._register_instance()

    def _init_order_status_handler(self):
        """初始化订单状态处理器"""
        try:
            # 直接导入订单状态处理器
            from order_status_handler import order_status_handler
            self.order_status_handler = order_status_handler
            logger.info(f"【{self.cookie_id}】订单状态处理器已启用")
        except Exception as e:
            logger.error(f"【{self.cookie_id}】初始化订单状态处理器失败: {self._safe_str(e)}")
            self.order_status_handler = None

    def _register_instance(self):
        """注册当前实例到类级别字典"""
        try:
            # 使用同步方式注册，避免在__init__中使用async
            XianyuLive._instances[self.cookie_id] = self
            logger.warning(f"【{self.cookie_id}】实例已注册到全局字典")
        except Exception as e:
            logger.error(f"【{self.cookie_id}】注册实例失败: {self._safe_str(e)}")

    def _unregister_instance(self):
        """从类级别字典中注销当前实例"""
        try:
            if self.cookie_id in XianyuLive._instances:
                del XianyuLive._instances[self.cookie_id]
                logger.warning(f"【{self.cookie_id}】实例已从全局字典中注销")
        except Exception as e:
            logger.error(f"【{self.cookie_id}】注销实例失败: {self._safe_str(e)}")

    @classmethod
    def get_instance(cls, cookie_id: str):
        """获取指定cookie_id的XianyuLive实例"""
        return cls._instances.get(cookie_id)

    @classmethod
    def get_all_instances(cls):
        """获取所有活跃的XianyuLive实例"""
        return dict(cls._instances)

    @classmethod
    def get_instance_count(cls):
        """获取当前活跃实例数量"""
        return len(cls._instances)

    def _load_cookie_refresh_settings(self):
        """从数据库读取账号级定时Cookie刷新设置，失败时使用保守默认值"""
        try:
            settings = db_manager.get_cookie_refresh_settings(self.cookie_id)
            return {
                'enabled': bool(settings.get('enabled')),
                'interval_minutes': int(settings.get('interval_minutes', 1440)),
            }
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】读取Cookie定时刷新设置失败，默认关闭: {self._safe_str(e)}")
            return {'enabled': False, 'interval_minutes': 1440}

    def _format_cookie_refresh_interval(self) -> str:
        minutes = max(1, int(self.cookie_refresh_interval // 60))
        if minutes % 1440 == 0:
            return f"{minutes // 1440}天"
        if minutes % 60 == 0:
            return f"{minutes // 60}小时"
        return f"{minutes}分钟"

    def _next_cookie_refresh_delay(self) -> float:
        """Spread enabled preventive refreshes with a small random jitter."""
        interval = max(60.0, float(getattr(self, 'cookie_refresh_interval', 86400)))
        return max(60.0, interval * random.uniform(0.9, 1.1))

    def configure_cookie_refresh(self, enabled: bool, interval_minutes: int):
        """更新运行中的定时Cookie刷新设置"""
        interval_seconds = max(60, int(interval_minutes) * 60)
        previous = (self.cookie_refresh_enabled, self.cookie_refresh_interval)
        was_enabled = self.cookie_refresh_enabled
        self.cookie_refresh_enabled = bool(enabled)
        self.cookie_refresh_interval = interval_seconds
        if self.cookie_refresh_enabled and not was_enabled:
            self.last_cookie_refresh_time = time.time()
            self.next_cookie_refresh_time = (
                self.last_cookie_refresh_time + self._next_cookie_refresh_delay()
            )
        elif previous != (self.cookie_refresh_enabled, self.cookie_refresh_interval):
            self.next_cookie_refresh_time = time.time() + self._next_cookie_refresh_delay()
        current = (self.cookie_refresh_enabled, self.cookie_refresh_interval)
        if previous != current:
            status = "开启" if self.cookie_refresh_enabled else "关闭"
            logger.info(
                f"【{self.cookie_id}】Cookie定时刷新设置已更新: {status}, 间隔 {self._format_cookie_refresh_interval()}"
            )

    def refresh_cookie_refresh_settings_from_db(self):
        """刷新运行时定时Cookie刷新设置，便于后台循环无需重启生效"""
        settings = self._load_cookie_refresh_settings()
        self.configure_cookie_refresh(settings['enabled'], settings['interval_minutes'])

    def _create_tracked_task(self, coro):
        """创建并追踪后台任务，确保异常不会被静默忽略"""
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task

    def is_auto_confirm_enabled(self) -> bool:
        """检查当前账号是否启用自动确认发货"""
        try:
            from db_manager import db_manager
            return db_manager.get_auto_confirm(self.cookie_id)
        except Exception as e:
            logger.error(f"【{self.cookie_id}】获取自动确认发货设置失败: {self._safe_str(e)}")
            return False



    def can_auto_delivery(self, order_id: str) -> bool:
        """检查是否可以进行自动发货（防重复发货）- 基于订单ID"""
        if not str(order_id or "").strip():
            logger.warning("自动发货缺少订单标识，已停止处理")
            return False

        current_time = time.time()
        last_delivery = self.last_delivery_time.get(order_id, 0)

        if current_time - last_delivery < self.delivery_cooldown:
            logger.info(f"【{self.cookie_id}】订单 {order_id} 在冷却期内，跳过自动发货")
            return False

        return True

    def mark_delivery_sent(self, order_id: str):
        """标记订单已发货"""
        self.last_delivery_time[order_id] = time.time()
        logger.info(f"【{self.cookie_id}】订单 {order_id} 已标记为发货")

        # 更新订单状态为已发货
        logger.info(f"【{self.cookie_id}】检查自动发货订单状态处理器: handler_exists={self.order_status_handler is not None}")
        if self.order_status_handler:
            logger.info(f"【{self.cookie_id}】准备调用订单状态处理器.handle_auto_delivery_order_status: {order_id}")
            try:
                success = self.order_status_handler.handle_auto_delivery_order_status(
                    order_id=order_id,
                    cookie_id=self.cookie_id,
                    context="自动发货完成"
                )
                logger.info(f"【{self.cookie_id}】订单状态处理器.handle_auto_delivery_order_status返回结果: {success}")
                if success:
                    logger.info(f"【{self.cookie_id}】订单 {order_id} 状态已更新为已发货")
                else:
                    logger.warning(f"【{self.cookie_id}】订单 {order_id} 状态更新为已发货失败")
            except Exception as e:
                logger.error(f"【{self.cookie_id}】订单状态更新失败: {self._safe_str(e)}")
                logger.error("自动发货状态写入失败: error_type={}", type(e).__name__)
        else:
            logger.warning(f"【{self.cookie_id}】订单状态处理器为None，跳过自动发货状态更新: {order_id}")

    async def _delayed_lock_release(self, lock_key: str, delay_minutes: int = 10):
        """
        延迟释放锁的异步任务

        Args:
            lock_key: 锁的键
            delay_minutes: 延迟时间（分钟），默认10分钟
        """
        try:
            delay_seconds = delay_minutes * 60
            logger.info(f"【{self.cookie_id}】订单锁 {lock_key} 将在 {delay_minutes} 分钟后释放")

            # 等待指定时间
            await asyncio.sleep(delay_seconds)

            # 检查锁是否仍然存在且需要释放
            if lock_key in self._lock_hold_info:
                lock_info = self._lock_hold_info[lock_key]
                if lock_info.get('locked', False):
                    # 释放锁
                    lock_info['locked'] = False
                    lock_info['release_time'] = time.time()
                    logger.info(f"【{self.cookie_id}】订单锁 {lock_key} 延迟释放完成")

                    # 清理锁信息（可选，也可以保留用于统计）
                    # del self._lock_hold_info[lock_key]

        except asyncio.CancelledError:
            logger.info(f"【{self.cookie_id}】订单锁 {lock_key} 延迟释放任务被取消")
            raise
        except Exception as e:
            logger.error(f"【{self.cookie_id}】订单锁 {lock_key} 延迟释放失败: {self._safe_str(e)}")

    def is_lock_held(self, lock_key: str) -> bool:
        """
        检查指定的锁是否仍在持有状态

        Args:
            lock_key: 锁的键

        Returns:
            bool: True表示锁仍在持有，False表示锁已释放或不存在
        """
        if lock_key not in self._lock_hold_info:
            return False

        lock_info = self._lock_hold_info[lock_key]
        return lock_info.get('locked', False)

    def cleanup_expired_locks(self, max_age_hours: int = 24):
        """
        清理过期的锁（包括自动发货锁和订单详情锁）

        Args:
            max_age_hours: 锁的最大保留时间（小时），默认24小时
        """
        try:
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600

            # 清理自动发货锁
            expired_delivery_locks = []
            for order_id, last_used in self._lock_usage_times.items():
                if current_time - last_used > max_age_seconds:
                    expired_delivery_locks.append(order_id)

            # 清理过期的自动发货锁
            for order_id in expired_delivery_locks:
                if order_id in self._order_locks:
                    del self._order_locks[order_id]
                if order_id in self._lock_usage_times:
                    del self._lock_usage_times[order_id]
                # 清理锁持有信息
                if order_id in self._lock_hold_info:
                    lock_info = self._lock_hold_info[order_id]
                    # 取消延迟释放任务
                    if 'task' in lock_info and lock_info['task']:
                        lock_info['task'].cancel()
                    del self._lock_hold_info[order_id]

            if expired_delivery_locks:
                logger.info(
                    f"【{self.cookie_id}】清理了 {len(expired_delivery_locks)} 个过期发货锁"
                )
                logger.warning(
                    f"【{self.cookie_id}】当前发货锁数量: {len(self._order_locks)}"
                )

        except Exception as e:
            logger.error(f"【{self.cookie_id}】清理过期锁时发生错误: {self._safe_str(e)}")



    def _is_auto_delivery_trigger(self, message: str) -> bool:
        """检查消息是否为自动发货触发关键字"""
        # 定义所有自动发货触发关键字
        auto_delivery_keywords = [
            # 系统消息
            '[我已付款，等待你发货]',
            '[已付款，待发货]',
            '我已付款，等待你发货',
            '[记得及时发货]',
        ]

        # 检查消息是否包含任何触发关键字
        for keyword in auto_delivery_keywords:
            if keyword in message:
                return True

        return False

    def _extract_order_id(self, message: dict) -> str:
        """从消息中提取订单ID"""
        try:
            order_id = None

            logger.debug("订单事件解析开始: payload_type={}", type(message).__name__)

            # 检查message['1']的结构，处理可能是列表、字典或字符串的情况
            message_1 = message.get('1', {})
            content_json_str = ''

            if isinstance(message_1, dict):
                logger.debug("订单事件字段 1 的类型为 dict")

                # 检查message['1']['6']的结构
                message_1_6 = message_1.get('6', {})
                if isinstance(message_1_6, dict):
                    logger.debug("订单事件嵌套字段的类型为 dict")
                    # 方法1: 从button的targetUrl中提取orderId
                    content_json_str = message_1_6.get('3', {}).get('5', '') if isinstance(message_1_6.get('3', {}), dict) else ''
                else:
                    logger.debug("订单事件嵌套字段类型不匹配: {}", type(message_1_6).__name__)

            elif isinstance(message_1, list):
                logger.debug("订单事件字段 1 的类型为 list")
                # 如果message['1']是列表，跳过这种提取方式

            elif isinstance(message_1, str):
                logger.debug("订单事件字段 1 的类型为 str")
                # 如果message['1']是字符串，跳过这种提取方式

            else:
                logger.debug("订单事件字段 1 的类型不受支持: {}", type(message_1).__name__)
                # 其他类型，跳过这种提取方式

            if content_json_str:
                try:
                    content_data = json.loads(content_json_str)

                    # 方法1a: 从button的targetUrl中提取orderId
                    target_url = content_data.get('dxCard', {}).get('item', {}).get('main', {}).get('exContent', {}).get('button', {}).get('targetUrl', '')
                    if target_url:
                        # 从URL中提取orderId参数
                        order_match = re.search(r'orderId=(\d+)', target_url)
                        if order_match:
                            order_id = order_match.group(1)
                            logger.info(f'【{self.cookie_id}】✅ 从button提取到订单ID: {order_id}')

                    # 方法1b: 从main的targetUrl中提取order_detail的id
                    if not order_id:
                        main_target_url = content_data.get('dxCard', {}).get('item', {}).get('main', {}).get('targetUrl', '')
                        if main_target_url:
                            order_match = re.search(r'order_detail\?id=(\d+)', main_target_url)
                            if order_match:
                                order_id = order_match.group(1)
                                logger.info(f'【{self.cookie_id}】✅ 从main targetUrl提取到订单ID: {order_id}')

                except Exception as parse_e:
                    logger.warning(f"解析内容JSON失败: {parse_e}")

            # 方法2: 从dynamicOperation中的order_detail URL提取orderId
            if not order_id and content_json_str:
                try:
                    content_data = json.loads(content_json_str)
                    dynamic_target_url = content_data.get('dynamicOperation', {}).get('changeContent', {}).get('dxCard', {}).get('item', {}).get('main', {}).get('exContent', {}).get('button', {}).get('targetUrl', '')
                    if dynamic_target_url:
                        # 从order_detail URL中提取id参数
                        order_match = re.search(r'order_detail\?id=(\d+)', dynamic_target_url)
                        if order_match:
                            order_id = order_match.group(1)
                            logger.info(f'【{self.cookie_id}】✅ 从order_detail提取到订单ID: {order_id}')
                except Exception as parse_e:
                    logger.warning(f"解析dynamicOperation JSON失败: {parse_e}")

            # 方法3: 如果前面的方法都失败，尝试在整个消息中搜索订单ID模式
            if not order_id:
                try:
                    # 将整个消息转换为字符串进行搜索
                    message_str = str(message)

                    # 搜索各种可能的订单ID模式
                    patterns = [
                        r'orderId[=:](\d{10,})',  # orderId=123456789 或 orderId:123456789
                        r'order_detail\?id=(\d{10,})',  # order_detail?id=123456789
                        r'"id"\s*:\s*"?(\d{10,})"?',  # "id":"123456789" 或 "id":123456789
                        r'bizOrderId[=:](\d{10,})',  # bizOrderId=123456789
                    ]

                    for pattern in patterns:
                        matches = re.findall(pattern, message_str)
                        if matches:
                            # 取第一个匹配的订单ID
                            order_id = matches[0]
                            logger.info(f'【{self.cookie_id}】✅ 从消息字符串中提取到订单ID: {order_id} (模式: {pattern})')
                            break

                except Exception as search_e:
                    logger.warning(f"在消息字符串中搜索订单ID失败: {search_e}")

            if order_id:
                logger.info(f'【{self.cookie_id}】🎯 最终提取到订单ID: {order_id}')
            else:
                logger.warning(f'【{self.cookie_id}】❌ 未能从消息中提取到订单ID')

            return order_id

        except Exception as e:
            logger.error(f"【{self.cookie_id}】提取订单ID失败: {self._safe_str(e)}")
            return None

    async def _verify_paid_order_for_delivery(
        self,
        order_id: str,
        item_id: str = None,
        buyer_id: str = None,
    ) -> dict:
        """实时确认普通订单已付款；任何不确定状态都按未通过处理。"""
        from order_sync_service import (
            XianyuOrderListClient,
            fetch_xianyu_order_detail,
            get_order_sync_lock,
            normalize_order_status,
            ORDER_BUSINESS_ORDINARY,
            parse_order_detail_payload,
            parse_trusted_order_quantity,
        )

        expected_item_id = str(item_id or "").strip()
        expected_buyer_id = str(buyer_id or "").strip()
        order_sync_lock_wait_ms = 0.0
        order_sync_lock_hold_ms = 0.0
        # 已知订单号优先查单订单详情，避免刚付款事件排在批量订单同步之后。
        # 详情不可用时再回退到有页数上限的订单列表。
        client = XianyuOrderListClient(max_pages=DELIVERY_VERIFY_MAX_PAGES)
        try:
            discovery = {}
            if str(order_id).isdigit():
                discovery = parse_order_detail_payload(
                    await fetch_xianyu_order_detail(
                        cookie_id=self.cookie_id,
                        cookie_string=self.cookies_str,
                        order_id=str(order_id),
                        user_agent=self.browser_user_agent,
                    ),
                    self.cookie_id,
                )
            if not discovery.get("success"):
                lock_wait_started = time.perf_counter()
                async with get_order_sync_lock(self.cookie_id):
                    lock_acquired_at = time.perf_counter()
                    order_sync_lock_wait_ms = (lock_acquired_at - lock_wait_started) * 1000
                    try:
                        discovery = await client.discover(
                            cookie_id=self.cookie_id,
                            cookie_string=self.cookies_str,
                            days=365,
                            user_agent=self.browser_user_agent,
                            target_order_id=str(order_id),
                        )
                    finally:
                        order_sync_lock_hold_ms = (time.perf_counter() - lock_acquired_at) * 1000
        except Exception as exc:
            logger.info(
                "invite_delivery_lock_latency order_ref={} account_ref={} order_sync_lock_wait_ms={:.1f} order_sync_lock_hold_ms={:.1f}",
                hashlib.sha256(str(order_id).encode("utf-8")).hexdigest()[:12],
                hashlib.sha256(str(self.cookie_id).encode("utf-8")).hexdigest()[:12],
                order_sync_lock_wait_ms,
                order_sync_lock_hold_ms,
            )
            reason = sanitize_runtime_error(
                f"实时订单状态查询异常: {type(exc).__name__}"
            )
            return {
                "allowed": False,
                "status": "unknown",
                "reason": reason,
                "attempts": 1,
            }

        logger.info(
            "invite_delivery_lock_latency order_ref={} account_ref={} order_sync_lock_wait_ms={:.1f} order_sync_lock_hold_ms={:.1f}",
            hashlib.sha256(str(order_id).encode("utf-8")).hexdigest()[:12],
            hashlib.sha256(str(self.cookie_id).encode("utf-8")).hexdigest()[:12],
            order_sync_lock_wait_ms,
            order_sync_lock_hold_ms,
        )

        if not discovery.get("success"):
            return {
                "allowed": False,
                "status": "unknown",
                "reason": discovery.get("error") or "实时订单状态查询失败",
                "error_code": discovery.get("error_code") or "platform_error",
                "requires_login": bool(discovery.get("requires_login")),
                "attempts": 1,
            }

        result = next(
            (
                row
                for row in discovery.get("orders") or []
                if str(row.get("order_id") or "") == str(order_id)
            ),
            None,
        )
        if not result:
            return {
                "allowed": False,
                "status": "unknown",
                "reason": "本轮平台订单列表未返回该订单",
                "error_code": "order_not_observed",
                "attempts": 1,
            }

        returned_item_id = str(result.get("item_id") or "").strip()
        returned_buyer_id = str(result.get("buyer_id") or "").strip()
        if not _delivery_identity_is_confirmed(
            expected_item_id,
            expected_buyer_id,
            returned_item_id,
            returned_buyer_id,
        ):
            return {
                "allowed": False,
                "status": "identity_unconfirmed",
                "reason": "订单商品或买家身份缺失或与当前会话不一致",
                "attempts": 1,
            }

        order_business_type = str(
            result.get("order_business_type") or "unknown"
        ).strip().lower()
        order_status = normalize_order_status(
            result.get("order_status"),
            str(result.get("platform_status_text") or ""),
        )
        if order_business_type != ORDER_BUSINESS_ORDINARY:
            error_code = (
                "lead_order_not_fulfillable"
                if order_business_type == "lead"
                else "order_business_type_unconfirmed"
            )
            logger.warning(
                "【{}】订单 {} 业务类型={}，跳过自动发货",
                self.cookie_id,
                order_id,
                order_business_type,
            )
            return {
                "allowed": False,
                "status": order_status,
                "business_type": order_business_type,
                "error_code": error_code,
                "reason": "订单不是可自动发货的普通实物订单",
                "attempts": 1,
            }
        if order_status == "pending_ship":
            logger.info(f"【{self.cookie_id}】订单 {order_id} 实时付款状态校验通过")
            return {
                "allowed": True,
                "status": order_status,
                "business_type": order_business_type,
                "reason": "买家已付款，等待卖家发货",
                "attempts": 1,
                "quantity": parse_trusted_order_quantity(result.get("quantity")),
                "amount": result.get("amount"),
                "item_title": result.get("item_title"),
                "created_at": result.get("created_at"),
                "is_bargain": bool(result.get("is_bargain")),
            }
        return {
            "allowed": False,
            "status": order_status,
            "reason": f"订单实时状态为 {order_status}，未确认处于待发货",
            "attempts": 1,
        }

    async def _execute_fulfillment_attempt(
        self,
        *,
        websocket,
        order_id: str,
        item_id: str,
        buyer_id: str,
        chat_id: str,
        expected_quantity: int,
        delivery_source: str = AUTO_DELIVERY_SOURCE_PAID_NOTICE,
        confirm_platform: bool = True,
        item_title: str = "待获取商品信息",
        database=None,
    ) -> dict:
        """Run one durable fulfillment attempt across every irreversible action."""
        if database is None:
            from db_manager import db_manager as database

        try:
            expected_quantity = int(expected_quantity)
        except (TypeError, ValueError):
            expected_quantity = 0
        if expected_quantity < 1:
            return {
                "success": False,
                "partial": False,
                "error_code": "invalid_fulfillment_quantity",
                "attempt_id": None,
                "expected_count": expected_quantity,
                "sent_count": 0,
                "manual_review": False,
            }

        attempt = database.begin_fulfillment_attempt(
            order_id=order_id,
            cookie_id=self.cookie_id,
            expected_quantity=expected_quantity,
        )
        attempt_outcome = str(attempt.get("outcome") or "manual_review")
        attempt_id = attempt.get("attempt_id")
        if attempt_outcome == "already_completed" and attempt_id:
            return {
                "success": True,
                "partial": False,
                "error_code": None,
                "attempt_id": int(attempt_id),
                "expected_count": expected_quantity,
                "sent_count": expected_quantity,
                "manual_review": False,
                "already_completed": True,
            }
        if attempt_outcome != "acquired" or not attempt_id:
            return {
                "success": False,
                "partial": attempt_outcome == "manual_review",
                "error_code": attempt.get("error_code") or f"fulfillment_{attempt_outcome}",
                "attempt_id": int(attempt_id) if attempt_id else None,
                "expected_count": expected_quantity,
                "sent_count": int(attempt.get("sent_count") or 0),
                "manual_review": attempt_outcome == "manual_review",
            }

        attempt_id = int(attempt_id)
        sending_started = False
        sent_count = 0
        phase = "delivery_content"

        def quarantine(reason_code: str) -> None:
            database.mark_fulfillment_manual_review(
                attempt_id,
                reason_code,
                sent_count=sent_count,
            )

        def release_or_quarantine(reason_code: str) -> bool:
            released = database.release_fulfillment_attempt(attempt_id, reason_code)
            if not released:
                quarantine(f"{reason_code}_state_uncertain")
            return released

        try:
            delivery_contents = []
            for delivery_index in range(expected_quantity):
                delivery_content = await self._auto_delivery(
                    item_id,
                    item_title,
                    order_id,
                    buyer_id,
                    fulfillment_attempt_id=attempt_id,
                    delivery_index=delivery_index,
                    expected_quantity=expected_quantity,
                    database=database,
                )
                if not delivery_content:
                    error_code = (
                        "delivery_content_unavailable"
                        if not delivery_contents
                        else "delivery_content_incomplete"
                    )
                    released = release_or_quarantine(error_code)
                    return {
                        "success": False,
                        "partial": not released,
                        "error_code": error_code,
                        "attempt_id": attempt_id,
                        "expected_count": expected_quantity,
                        "sent_count": 0,
                        "manual_review": not released,
                    }
                delivery_contents.append(delivery_content)

            # This durable transition must precede platform mutation and buyer messages.
            if not database.mark_fulfillment_sending(attempt_id):
                released = release_or_quarantine("sending_transition_failed")
                return {
                    "success": False,
                    "partial": not released,
                    "error_code": "sending_transition_failed",
                    "attempt_id": attempt_id,
                    "expected_count": expected_quantity,
                    "sent_count": 0,
                    "manual_review": not released,
                }
            sending_started = True

            phase = "platform_confirmation"
            if delivery_source == AUTO_DELIVERY_SOURCE_BARGAIN_FREESHIPPING:
                platform_result = await self.auto_freeshipping(
                    order_id,
                    item_id,
                    buyer_id,
                )
            elif confirm_platform:
                platform_result = await self.auto_confirm(order_id, item_id)
            else:
                platform_result = {"success": True}

            if not isinstance(platform_result, dict) or platform_result.get("success") is not True:
                quarantine("platform_confirmation_unconfirmed")
                return {
                    "success": False,
                    "partial": True,
                    "error_code": "platform_confirmation_unconfirmed",
                    "attempt_id": attempt_id,
                    "expected_count": expected_quantity,
                    "sent_count": 0,
                    "manual_review": True,
                }
            if confirm_platform and delivery_source != AUTO_DELIVERY_SOURCE_BARGAIN_FREESHIPPING:
                self.confirmed_orders[order_id] = time.time()

            phase = "buyer_delivery"
            for delivery_index, delivery_content in enumerate(delivery_contents):
                if delivery_content.startswith("__IMAGE_SEND__"):
                    image_data = delivery_content.replace("__IMAGE_SEND__", "", 1)
                    card_id = None
                    if "|" in image_data:
                        card_id_str, image_url = image_data.split("|", 1)
                        try:
                            card_id = int(card_id_str)
                        except ValueError:
                            card_id = None
                    else:
                        image_url = image_data
                    await self.send_image_msg(
                        websocket,
                        chat_id,
                        buyer_id,
                        image_url,
                        card_id=card_id,
                    )
                else:
                    await self.send_msg(
                        websocket,
                        chat_id,
                        buyer_id,
                        delivery_content,
                    )
                sent_count += 1
                if delivery_index < len(delivery_contents) - 1:
                    await asyncio.sleep(1)

            phase = "fulfillment_commit"
            if sent_count != expected_quantity or not database.commit_fulfillment_attempt(
                attempt_id,
                delivered_count=sent_count,
            ):
                quarantine("fulfillment_commit_failed")
                return {
                    "success": False,
                    "partial": True,
                    "error_code": "fulfillment_commit_failed",
                    "attempt_id": attempt_id,
                    "expected_count": expected_quantity,
                    "sent_count": sent_count,
                    "manual_review": True,
                }

            try:
                self.mark_delivery_sent(order_id)
            except Exception as exc:
                logger.error(
                    "履约已提交但运行时状态刷新失败: error_type={}",
                    type(exc).__name__,
                )
            return {
                "success": True,
                "partial": False,
                "error_code": None,
                "attempt_id": attempt_id,
                "expected_count": expected_quantity,
                "sent_count": sent_count,
                "manual_review": False,
                "already_completed": False,
            }
        except asyncio.CancelledError:
            cancel_reason = {
                "delivery_content": "delivery_content_cancelled",
                "platform_confirmation": "platform_confirmation_cancelled",
                "buyer_delivery": "delivery_task_cancelled",
                "fulfillment_commit": "fulfillment_commit_cancelled",
            }.get(phase, f"{phase}_cancelled")
            if sending_started:
                quarantine(cancel_reason)
            else:
                release_or_quarantine(cancel_reason)
            raise
        except Exception as exc:
            error_reason = {
                "delivery_content": "delivery_content_error",
                "platform_confirmation": "platform_confirmation_error",
                "buyer_delivery": "buyer_message_failed",
                "fulfillment_commit": "fulfillment_commit_error",
            }.get(phase, f"{phase}_error")
            if sending_started:
                quarantine(error_reason)
                manual_review = True
            else:
                manual_review = not release_or_quarantine(error_reason)
            logger.error(
                "履约执行异常: phase={}, error_type={}",
                phase,
                type(exc).__name__,
            )
            return {
                "success": False,
                "partial": manual_review,
                "error_code": error_reason,
                "attempt_id": attempt_id,
                "expected_count": expected_quantity,
                "sent_count": sent_count,
                "manual_review": manual_review,
            }

    async def _handle_auto_delivery(self, websocket, message: dict, send_user_name: str, send_user_id: str,
                                   item_id: str, chat_id: str, msg_time: str,
                                   delivery_source: str = AUTO_DELIVERY_SOURCE_PAID_NOTICE):
        """统一处理自动发货逻辑"""
        try:
            # 检查商品是否属于当前cookies
            if item_id and item_id != "未知商品":
                try:
                    from db_manager import db_manager
                    item_info = db_manager.get_item_info(self.cookie_id, item_id)
                    if not item_info:
                        logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 商品 {item_id} 不属于当前账号，跳过自动发货')
                        return
                    logger.warning(f'[{msg_time}] 【{self.cookie_id}】✅ 商品 {item_id} 归属验证通过')
                except Exception as e:
                    logger.error(f'[{msg_time}] 【{self.cookie_id}】检查商品归属失败: {self._safe_str(e)}，跳过自动发货')
                    return

            # 提取订单ID
            order_id = self._extract_order_id(message)

            # 如果order_id不存在，直接返回
            if not order_id:
                logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 未能提取到订单ID，跳过自动发货')
                return

            # 订单ID已提取，将在自动发货时进行确认发货处理
            logger.info(f'[{msg_time}] 【{self.cookie_id}】提取到订单ID: {order_id}，将在自动发货时处理确认发货')

            if _invite_bridge_owns_item(self.cookie_id, item_id):
                record_delivery_stage(order_id, self.cookie_id, STAGE_PAID)
                hot_path_started = time.perf_counter()
                payment_started = time.perf_counter()
                payment_check = await self._verify_paid_order_for_delivery(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=send_user_id,
                )
                # 平台订单接口相对付款消息有秒级同步延迟：仅当结果属于
                # INVITE_VERIFY_RETRYABLE_ERROR_CODES（平台数据尚未就绪）时短
                # 退避重试，吃掉大部分延迟；其它失败保持立即放弃，不放宽门禁。
                for retry_delay in INVITE_VERIFY_RETRY_DELAYS_SECONDS:
                    if payment_check.get("allowed") or str(
                        payment_check.get("error_code") or ""
                    ) not in INVITE_VERIFY_RETRYABLE_ERROR_CODES:
                        break
                    await asyncio.sleep(retry_delay)
                    payment_check = await self._verify_paid_order_for_delivery(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                    )
                payment_verify_ms = (time.perf_counter() - payment_started) * 1000
                if not payment_check.get("allowed"):
                    logger.warning(
                        "邀请商品付款状态未通过，等待主动订单发现重试: status={}",
                        payment_check.get("status") or "unknown",
                    )
                    return
                record_delivery_stage(order_id, self.cookie_id, STAGE_GATE)
                from invite_bridge_poller import invite_bridge_poller

                stage_started = time.perf_counter()
                staged = invite_bridge_poller.stage_order(
                    cookie_id=self.cookie_id,
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=send_user_id,
                    amount=payment_check.get("amount"),
                    quantity=payment_check.get("quantity") or 1,
                    item_title=str(payment_check.get("item_title") or ""),
                    created_at=payment_check.get("created_at"),
                    chat_id=chat_id,
                    order_business_type=payment_check.get("business_type"),
                    is_bargain=(
                        delivery_source == AUTO_DELIVERY_SOURCE_BARGAIN_FREESHIPPING
                        or bool(payment_check.get("is_bargain"))
                    ),
                )
                stage_order_ms = (time.perf_counter() - stage_started) * 1000
                if not staged:
                    logger.warning("邀请商品订单上下文保存失败或订单已完成")
                    return
                logger.info(
                    "邀请商品订单已保存并提交桥接扫描: delivery_source={}",
                    delivery_source,
                )
                bridge_started = time.perf_counter()
                await invite_bridge_poller.scan_trusted_order(
                    cookie_id=self.cookie_id,
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=send_user_id,
                    chat_id=chat_id,
                    payment_check=payment_check,
                )
                bridge_event_ms = (time.perf_counter() - bridge_started) * 1000
                # 同买家 fan-out：连拍多单时第 2、3 笔常无独立付款消息，
                # 借本笔热路径立即定向补发现，不再等 30 秒兜底轮询。
                fanout_started = time.perf_counter()
                fanout_sent = 0
                try:
                    fanout_sent = await invite_bridge_poller.scan_buyer_orders(
                        cookie_id=self.cookie_id,
                        buyer_id=send_user_id,
                        chat_id=chat_id,
                        exclude_order_ids={order_id},
                    )
                except Exception as exc:
                    logger.warning(
                        "邀请桥同买家定向发现失败，交还兜底轮询: error_type={}",
                        type(exc).__name__,
                    )
                buyer_fanout_ms = (time.perf_counter() - fanout_started) * 1000
                logger.info(
                    "invite_delivery_latency order_ref={} account_ref={} payment_verify_ms={:.1f} stage_order_ms={:.1f} bridge_event_ms={:.1f} buyer_fanout_ms={:.1f} buyer_fanout_sent={} hot_path_total_ms={:.1f}",
                    hashlib.sha256(str(order_id).encode("utf-8")).hexdigest()[:12],
                    hashlib.sha256(str(self.cookie_id).encode("utf-8")).hexdigest()[:12],
                    payment_verify_ms,
                    stage_order_ms,
                    bridge_event_ms,
                    buyer_fanout_ms,
                    fanout_sent,
                    (time.perf_counter() - hot_path_started) * 1000,
                )
                return

            # 使用订单ID作为锁的键
            lock_key = order_id

            # 第一重检查：延迟锁状态（在获取锁之前检查，避免不必要的等待）
            if self.is_lock_held(lock_key):
                logger.info(f'[{msg_time}] 【{self.cookie_id}】🔒【提前检查】订单 {lock_key} 延迟锁仍在持有状态，跳过发货')
                return

            # 第二重检查：基于时间的冷却机制
            if not self.can_auto_delivery(order_id):
                logger.info(f'[{msg_time}] 【{self.cookie_id}】订单 {order_id} 在冷却期内，跳过发货')
                return

            # 获取或创建该订单的锁
            order_lock = self._order_locks[lock_key]

            # 更新锁的使用时间
            self._lock_usage_times[lock_key] = time.time()

            # 使用异步锁防止同一订单的并发处理
            async with order_lock:
                logger.info(f'[{msg_time}] 【{self.cookie_id}】获取订单锁成功: {lock_key}，开始处理自动发货')

                # 第三重检查：获取锁后再次检查延迟锁状态（双重检查，防止在等待锁期间状态发生变化）
                if self.is_lock_held(lock_key):
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】订单 {lock_key} 在获取锁后检查发现延迟锁仍持有，跳过发货')
                    return

                # 第四重检查：获取锁后再次检查冷却状态
                if not self.can_auto_delivery(order_id):
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】订单 {order_id} 在获取锁后检查发现仍在冷却期，跳过发货')
                    return

                payment_check = await self._verify_paid_order_for_delivery(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=send_user_id,
                )
                if not payment_check.get("allowed"):
                    reason = payment_check.get("reason") or "未确认买家已付款"
                    logger.warning(
                        f'[{msg_time}] 【{self.cookie_id}】订单 {order_id} 未通过付款状态校验，跳过自动发货: {reason}'
                    )
                    await self.send_delivery_failure_notification(
                        send_user_name,
                        send_user_id,
                        item_id,
                        f"已拦截自动发货：{reason}",
                        chat_id,
                    )
                    return

                # 自动发货逻辑
                try:
                    # 设置默认标题（将通过API获取真实商品信息）
                    item_title = "待获取商品信息"

                    logger.info(f"【{self.cookie_id}】准备自动发货: item_id={item_id}, item_title={item_title}")

                    # 检查是否需要多数量发货
                    from db_manager import db_manager
                    quantity_to_send = 1

                    # 检查商品是否开启了多数量发货
                    multi_quantity_delivery = db_manager.get_item_multi_quantity_delivery_status(self.cookie_id, item_id)

                    if multi_quantity_delivery and order_id:
                        verified_quantity = payment_check.get("quantity")
                        if verified_quantity is None:
                            reason = "平台订单列表未返回可信购买数量"
                            logger.warning(
                                f"【{self.cookie_id}】多数量订单缺少可信数量，跳过自动发货"
                            )
                            await self.send_delivery_failure_notification(
                                send_user_name,
                                send_user_id,
                                item_id,
                                f"已拦截自动发货：{reason}",
                                chat_id,
                            )
                            return
                        quantity_to_send = verified_quantity
                        logger.info(
                            f"【{self.cookie_id}】使用订单列表确认的购买数量: {quantity_to_send}"
                        )
                    elif not multi_quantity_delivery:
                        logger.info(f"商品 {item_id} 未开启多数量发货，发送单个卡券")

                    # The database attempt is the restart-safe authority.  It
                    # also refuses unknown/foreign order records before any
                    # card value or platform action can occur.
                    existing_order = db_manager.get_order_by_id(order_id)
                    if existing_order:
                        if str(existing_order.get("cookie_id") or "") != str(self.cookie_id):
                            logger.warning("自动发货订单账号归属不匹配，已停止处理")
                            return
                    elif not db_manager.insert_or_update_order(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        quantity=str(payment_check.get("quantity") or quantity_to_send),
                        amount=payment_check.get("amount"),
                        order_status="pending_ship",
                        cookie_id=self.cookie_id,
                        created_at=payment_check.get("created_at"),
                        chat_id=chat_id,
                    ):
                        logger.error("自动发货订单上下文保存失败，已停止处理")
                        return

                    # 付款核验已经拿到可信快照；写入现有订单同步字段，避免普通订单
                    # 只能等后续批量同步才进入仪表盘金额与时段统计。
                    from order_sync_service import parse_amount_fen, parse_order_time_utc

                    snapshot_status = str(
                        (existing_order or {}).get("order_status") or "pending_ship"
                    )
                    db_manager.apply_order_sync_update(
                        order_id=order_id,
                        cookie_id=self.cookie_id,
                        incoming_status=snapshot_status,
                        status_source="realtime_message",
                        ordered_at=parse_order_time_utc(payment_check.get("created_at")),
                        paid_amount_fen=parse_amount_fen(payment_check.get("amount")),
                        quantity=str(payment_check.get("quantity") or quantity_to_send),
                        chat_id=chat_id,
                    )
                    db_manager.reconcile_order_status_events(
                        cookie_id=self.cookie_id,
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        chat_id=chat_id,
                    )

                    fulfillment = await self._execute_fulfillment_attempt(
                        websocket=websocket,
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        chat_id=chat_id,
                        expected_quantity=quantity_to_send,
                        delivery_source=delivery_source,
                        confirm_platform=self.is_auto_confirm_enabled(),
                        item_title=item_title,
                        database=db_manager,
                    )
                    if not fulfillment.get("success"):
                        error_code = fulfillment.get("error_code") or "fulfillment_failed"
                        logger.warning(
                            "自动发货未完成: error_code={}, manual_review={}",
                            error_code,
                            bool(fulfillment.get("manual_review")),
                        )
                        failure_message = (
                            "自动发货状态不确定，已转人工复核"
                            if fulfillment.get("manual_review")
                            else "未找到匹配的发货规则或发货内容不完整"
                        )
                        await self.send_delivery_failure_notification(
                            send_user_name,
                            send_user_id,
                            item_id,
                            failure_message,
                            chat_id,
                        )
                        return
                    if fulfillment.get("already_completed"):
                        logger.info("订单已有已提交的履约记录，跳过重复发送")
                        return

                    # Keep a short in-process lock only as a fast duplicate guard.
                    self._lock_hold_info[lock_key] = {
                        'locked': True,
                        'lock_time': time.time(),
                        'release_time': None,
                        'task': None
                    }
                    delay_task = asyncio.create_task(
                        self._delayed_lock_release(lock_key, delay_minutes=10)
                    )
                    self._lock_hold_info[lock_key]['task'] = delay_task

                except Exception as e:
                    logger.error(f"自动发货处理异常: {self._safe_str(e)}")
                    # 发送自动发货异常通知
                    await self.send_delivery_failure_notification(send_user_name, send_user_id, item_id, f"自动发货处理异常: {str(e)}", chat_id)

                logger.info(f'[{msg_time}] 【{self.cookie_id}】订单锁释放: {lock_key}，自动发货处理完成')

        except Exception as e:
            logger.error(f"统一自动发货处理异常: {self._safe_str(e)}")



    @staticmethod
    def _session_refresh_blocks_listener(refresh_status: dict) -> bool:
        state = str((refresh_status or {}).get("state") or "")
        error_code = str((refresh_status or {}).get("error_code") or "")
        if state not in {
            "action_required",
            "refreshing",
            "verification_required",
            "manual_reauth_required",
        }:
            return False
        return not (
            state == "action_required"
            and (
                error_code == "connection_failures"
                or is_retryable_session_error_code(error_code)
            )
        )

    async def refresh_token(self, captcha_retry_count: int = 0):
        """Probe the real message token without starting an official browser."""
        from db_manager import db_manager

        del captcha_retry_count
        try:
            refresh_status = db_manager.get_account_session_refresh(self.cookie_id) or {}
            if self._session_refresh_blocks_listener(refresh_status):
                self.last_token_refresh_status = refresh_status.get("state")
                logger.info(
                    f"【{self.cookie_id}】账号会话正在等待人工处理，暂停消息 Token 探测"
                )
                return None

            account_info = await asyncio.to_thread(
                db_manager.get_cookie_details,
                self.cookie_id,
            )
            account_user_id = int((account_info or {}).get("user_id") or 0)
            account_unb = str((account_info or {}).get("xianyu_unb") or "").strip()
            db_cookie_value = str((account_info or {}).get("value") or "")
            db_cookie_unb = str(trans_cookies(db_cookie_value).get("unb") or "").strip()
            try:
                cookie_revision = int(account_info["cookie_revision"])
            except (KeyError, TypeError, ValueError):
                cookie_revision = -1
            if (
                not account_info
                or account_user_id != int(self.user_id)
                or not account_unb
                or not db_cookie_unb
                or db_cookie_unb != account_unb
                or str(self.myid or "").strip() != account_unb
                or cookie_revision < 0
            ):
                await self._mark_human_verification_required(
                    SessionProbeResult(
                        status=PROBE_EXPIRED,
                        cookies=dict(getattr(self, "cookies", {}) or {}),
                        error_code="account_identity_incomplete",
                        message="账号身份或 Cookie 版本不完整",
                    ),
                    trigger="消息 Token 探测",
                )
                return None

            if db_cookie_value != self.cookies_str:
                self.cookies_str = db_cookie_value
                self.cookies = trans_cookies(self.cookies_str)
            persisted_user_agent = str(
                account_info.get("browser_user_agent") or ""
            ).strip()
            if persisted_user_agent:
                self.browser_user_agent = persisted_user_agent

            logger.info(f"【{self.cookie_id}】开始探测消息 Token")
            device_id = str(getattr(self, "device_id", "") or "").strip()
            account_proxy = db_manager.get_account_proxy_config(self.cookie_id)
            probe = await probe_message_session_async(
                self.cookies_str,
                self.browser_user_agent,
                proxy=account_proxy,
                **({"device_id": device_id} if device_id else {}),
            )
            if not probe.succeeded:
                if probe.status in {PROBE_EXPIRED, PROBE_VERIFICATION_REQUIRED}:
                    trigger = (
                        "平台要求人工验证"
                        if probe.status == PROBE_VERIFICATION_REQUIRED
                        else "令牌/Session过期"
                    )
                    recovered = await self._try_password_login_refresh(trigger)
                    if recovered:
                        return self.current_token
                    if (
                        db_manager.get_account_session_refresh(self.cookie_id).get("state")
                        == "manual_reauth_required"
                    ):
                        self.last_token_refresh_status = "manual_reauth_required"
                        return None
                if probe.status == PROBE_RETRYABLE_ERROR:
                    await self._mark_retryable_token_probe_failure(
                        probe,
                        trigger="消息 Token 探测",
                    )
                    return None
                await self._mark_human_verification_required(
                    probe,
                    trigger="消息 Token 探测",
                )
                return None

            probe_unb = str(probe.cookies.get("unb") or "").strip()
            if not probe_unb or probe_unb != account_unb:
                mismatch = SessionProbeResult(
                    status=PROBE_EXPIRED,
                    cookies=dict(self.cookies),
                    error_code="account_mismatch",
                    message="消息会话账号与当前监听账号不一致",
                )
                await self._mark_human_verification_required(
                    mismatch,
                    trigger="消息 Token 探测",
                )
                return None

            new_cookie_string = probe_cookies_to_string(probe.cookies)
            cas_result = await asyncio.to_thread(
                db_manager.compare_and_swap_cookie_session,
                self.cookie_id,
                user_id=self.user_id,
                expected_xianyu_unb=account_unb,
                expected_revision=cookie_revision,
                cookie_value=new_cookie_string,
                browser_user_agent=self.browser_user_agent,
            )
            if cas_result.get("state") == "revision_conflict":
                self.last_token_refresh_status = "revision_conflict"
                logger.warning(
                    f"【{self.cookie_id}】消息 Token 探测结果因 Cookie 版本变化被丢弃"
                )
                return None
            if cas_result.get("state") not in {"updated", "unchanged"}:
                if cas_result.get("state") in {
                    "action_required",
                    "ownership_mismatch",
                    "not_found",
                }:
                    await self._mark_human_verification_required(
                        SessionProbeResult(
                            status=PROBE_EXPIRED,
                            cookies=dict(self.cookies),
                            error_code=str(
                                cas_result.get("reason")
                                or "account_identity_changed"
                            ),
                            message="账号身份已变化，已丢弃旧刷新结果",
                        ),
                        trigger="消息 Token 探测",
                    )
                    return None
                persistence_failure = SessionProbeResult(
                    status=PROBE_RETRYABLE_ERROR,
                    cookies=dict(self.cookies),
                    error_code="cookie_persist_failed",
                    message="消息 Token 已返回，但 Cookie 保存失败",
                )
                await self._mark_retryable_token_probe_failure(
                    persistence_failure,
                    trigger="消息 Token 探测",
                )
                return None
            if new_cookie_string:
                self.cookies_str = new_cookie_string
                self.cookies = dict(probe.cookies)

            self.current_token = probe.access_token
            self.last_token_refresh_time = time.time()
            self.last_message_received_time = 0
            self.last_token_refresh_status = "success"
            db_manager.update_account_session_refresh(
                self.cookie_id,
                state="success",
                trigger="消息 Token 探测",
                message="消息 Token 已验证",
                error_code="",
            )
            logger.info(f"【{self.cookie_id}】消息 Token 探测成功")
            return probe.access_token
        except Exception as exc:
            logger.error(
                f"【{self.cookie_id}】消息 Token 探测异常: {type(exc).__name__}"
            )
            failure = SessionProbeResult(
                status=PROBE_RETRYABLE_ERROR,
                cookies=dict(getattr(self, "cookies", {}) or {}),
                error_code="token_probe_exception",
                message="消息 Token 探测出现临时异常",
            )
            await self._mark_retryable_token_probe_failure(
                failure,
                trigger="消息 Token 探测",
            )
            return None

    async def _mark_retryable_token_probe_failure(
        self,
        probe: SessionProbeResult = None,
        *,
        trigger: str = "消息会话异常",
    ) -> None:
        """Persist a transient probe failure without pausing for human action."""
        from db_manager import db_manager

        current_state = str(
            (db_manager.get_account_session_refresh(self.cookie_id) or {}).get("state")
            or ""
        )
        if current_state in HUMAN_ACTION_SESSION_STATES:
            self.last_token_refresh_status = current_state
            logger.info(
                f"【{self.cookie_id}】账号仍在等待人工处理（{current_state}），"
                "不降级为可重试失败"
            )
            return

        error_code = str(
            getattr(probe, "error_code", "") or "session_probe_retryable"
        )[:80]
        message = str(
            getattr(probe, "message", "") or "平台状态检查出现临时异常，系统将自动重试"
        )[:240]
        db_manager.update_account_session_refresh(
            self.cookie_id,
            state="failed",
            trigger=trigger,
            message=message,
            error_code=error_code,
        )
        self.last_token_refresh_status = "retryable_error"
        logger.warning(
            f"【{self.cookie_id}】消息 Token 探测暂时失败，保留原会话并自动重试: {error_code}"
        )

    async def _enter_manual_reauth_required(
        self,
        *,
        trigger: str,
        message: str,
    ) -> None:
        """Park the account for human re-login and alert once on the transition."""
        from db_manager import db_manager

        current = db_manager.get_account_session_refresh(self.cookie_id) or {}
        already_required = (
            str(current.get("state") or "") == "manual_reauth_required"
        )
        db_manager.update_account_session_refresh(
            self.cookie_id,
            state="manual_reauth_required",
            trigger=trigger,
            message=message,
            error_code="manual_reauth_required",
        )
        self.last_token_refresh_status = "manual_reauth_required"
        if already_required:
            return
        # 绑定了续期设备时，写入会被落成 refreshing 并派发续期任务，此时不该催人工登录。
        landed = db_manager.get_account_session_refresh(self.cookie_id) or {}
        if str(landed.get("state") or "") != "manual_reauth_required":
            return
        await self.send_token_refresh_notification(
            f"{message}。账号【{self.cookie_id}】已暂停接单，请重新登录后恢复。",
            "manual_reauth_required",
        )

    async def _mark_human_verification_required(
        self,
        probe: SessionProbeResult = None,
        *,
        trigger: str = "消息会话异常",
    ) -> None:
        """Enter a passive state; only an explicit action may open a browser."""
        from db_manager import db_manager

        current = db_manager.get_account_session_refresh(self.cookie_id) or {}
        if current.get("state") == "manual_reauth_required":
            self.last_token_refresh_status = "manual_reauth_required"
            return
        already_waiting = current.get("state") == "action_required"
        probe_status = getattr(probe, "status", "")
        if probe_status == PROBE_EXPIRED:
            message = "闲鱼登录状态已过期，请手动开始一次验证"
        elif probe_status == PROBE_VERIFICATION_REQUIRED:
            message = "需要完成人工验证，请手动开始一次验证"
        else:
            message = "消息 Token 未验证通过，请手动开始一次验证"
        error_code = (
            getattr(probe, "error_code", "")
            or "message_session_action_required"
        )
        verification_url = str(getattr(probe, "verification_url", "") or "")
        if verification_url:
            self.pending_verification_url = verification_url

        db_manager.update_account_session_refresh(
            self.cookie_id,
            state="action_required",
            trigger=trigger,
            message=message,
            error_code=error_code,
        )
        self.last_token_refresh_status = "action_required"
        log_captcha_event(
            self.cookie_id,
            "需要人工验证",
            None,
            f"触发场景: {trigger}；官方验证地址未记录",
        )
        if not already_waiting:
            await self.send_token_refresh_notification(
                message,
                "human_verification_required",
            )

    async def _update_cookies_and_restart(
        self,
        new_cookies_str: str,
        *,
        browser_user_agent: str = "",
        access_token: str = "",
        expected_revision: int = None,
        expected_xianyu_unb: str = "",
    ) -> bool:
        """Atomically persist a validated identity and install one new listener."""
        from cookie_manager import manager as cookie_manager
        from db_manager import db_manager

        if not new_cookies_str or not new_cookies_str.strip() or cookie_manager is None:
            return False

        account_info = await asyncio.to_thread(
            db_manager.get_cookie_details,
            self.cookie_id,
        )
        if not account_info:
            return False
        try:
            stored_user_id = int(account_info.get("user_id"))
            stored_revision = int(account_info["cookie_revision"])
        except (KeyError, TypeError, ValueError):
            stored_user_id = 0
            stored_revision = -1
        stored_unb = str(account_info.get("xianyu_unb") or "").strip()
        old_cookies_str = str(account_info.get("value") or "")
        old_cookies = trans_cookies(old_cookies_str)
        old_cookie_unb = str(old_cookies.get("unb") or "").strip()
        cas_revision = stored_revision if expected_revision is None else int(expected_revision)
        cas_unb = str(expected_xianyu_unb or stored_unb).strip()
        if (
            stored_user_id != int(self.user_id)
            or stored_revision < 0
            or stored_revision != cas_revision
            or not stored_unb
            or not old_cookie_unb
            or stored_unb != old_cookie_unb
            or cas_unb != stored_unb
            or str(self.myid or "").strip() != stored_unb
        ):
            await asyncio.to_thread(
                db_manager.update_account_session_refresh,
                self.cookie_id,
                state="action_required",
                message="账号身份或 Cookie 版本已变化，旧刷新结果已丢弃",
                error_code="cookie_identity_or_revision_changed",
            )
            return False
        old_user_agent = str((account_info or {}).get("browser_user_agent") or "")
        effective_user_agent = str(
            browser_user_agent
            or self.browser_user_agent
            or old_user_agent
            or detect_default_browser_user_agent()
        ).strip()

        try:
            incoming = trans_cookies(new_cookies_str)
            if not incoming:
                return False
            merged = dict(old_cookies)
            merged.update(incoming)
            expected_unb = stored_unb
            merged_unb = str(merged.get("unb") or "").strip()
            if not merged_unb or merged_unb != expected_unb:
                logger.error(f"【{self.cookie_id}】刷新结果账号不匹配，保留旧监听")
                await asyncio.to_thread(
                    db_manager.update_account_session_refresh,
                    self.cookie_id,
                    state="action_required",
                    message="刷新结果账号身份不匹配，旧结果已丢弃",
                    error_code="account_identity_changed",
                )
                return False
            merged_cookie_string = probe_cookies_to_string(merged)
        except Exception as exc:
            logger.error(
                f"【{self.cookie_id}】合并刷新 Cookie 失败: {type(exc).__name__}"
            )
            return False

        cas_result = await asyncio.to_thread(
            db_manager.compare_and_swap_cookie_session,
            self.cookie_id,
            user_id=self.user_id,
            expected_xianyu_unb=stored_unb,
            expected_revision=cas_revision,
            cookie_value=merged_cookie_string,
            browser_user_agent=effective_user_agent,
        )
        if cas_result.get("state") not in {"updated", "unchanged"}:
            if cas_result.get("state") in {
                "action_required",
                "ownership_mismatch",
                "not_found",
            }:
                await asyncio.to_thread(
                    db_manager.update_account_session_refresh,
                    self.cookie_id,
                    state="action_required",
                    message="账号身份已变化，旧刷新结果已丢弃",
                    error_code=str(
                        cas_result.get("reason") or "account_identity_changed"
                    ),
                )
            elif cas_result.get("state") == "revision_conflict":
                logger.warning(
                    f"【{self.cookie_id}】刷新结果因 Cookie 版本冲突被丢弃"
                )
            return False
        committed_revision = int(cas_result["cookie_revision"])

        handoff_time = time.time()
        runtime_state = {
            "current_token": access_token or self.current_token,
            "last_token_refresh_time": handoff_time if access_token else self.last_token_refresh_time,
            "browser_user_agent": effective_user_agent,
            "cookie_refresh_anchor": handoff_time,
            "item_sync_anchor": handoff_time,
            # L3 保活节奏跨交接延续：保活自己触发的重启不得把周期清零
            "last_l3_keepalive_time": getattr(self, "last_l3_keepalive_time", 0),
        }
        try:
            replacement = await cookie_manager.replace_cookie(
                self.cookie_id,
                merged_cookie_string,
                save_to_db=False,
                runtime_state=runtime_state,
                expected_cookie_revision=committed_revision,
                expected_cookie_value=merged_cookie_string,
            )
            if replacement.get("status") != "restarted":
                raise RuntimeError("listener replacement was superseded")
        except Exception as exc:
            logger.error(
                f"【{self.cookie_id}】安装刷新后的监听失败: {type(exc).__name__}"
            )
            rollback = await asyncio.to_thread(
                db_manager.compare_and_swap_cookie_session,
                self.cookie_id,
                user_id=self.user_id,
                expected_xianyu_unb=stored_unb,
                expected_revision=committed_revision,
                cookie_value=old_cookies_str,
                browser_user_agent=old_user_agent,
            )
            if rollback.get("state") not in {"updated", "unchanged"}:
                logger.warning(
                    f"【{self.cookie_id}】监听安装失败后的 Cookie 回滚被更新版本阻止"
                )
            self.cookies_str = old_cookies_str
            self.cookies = old_cookies
            return False

        self.cookies_str = merged_cookie_string
        self.cookies = merged
        self.browser_user_agent = effective_user_agent
        if access_token:
            self.current_token = access_token
            self.last_token_refresh_time = handoff_time
        logger.info(f"【{self.cookie_id}】Cookie、浏览器标识和监听已完成单次交接")
        return True

    async def update_config_cookies(self):
        """更新数据库中的cookies（不会覆盖账号密码等其他字段）"""
        try:
            from db_manager import db_manager

            # 更新数据库中的Cookie
            if hasattr(self, 'cookie_id') and self.cookie_id:
                try:
                    # 获取当前Cookie的用户ID，避免在刷新时改变所有者
                    current_user_id = None
                    if hasattr(self, 'user_id') and self.user_id:
                        current_user_id = self.user_id

                    # 使用 update_cookie_account_info 避免覆盖其他字段（如 username, password, pause_duration, remark 等）
                    # 这个方法会自动处理新账号和现有账号的情况，不会覆盖账号密码
                    success = db_manager.update_cookie_account_info(
                        self.cookie_id,
                        cookie_value=self.cookies_str,
                        user_id=current_user_id  # 如果是新账号，需要提供user_id
                    )
                    if not success:
                        # 如果更新失败，记录错误但不使用 save_cookie（避免覆盖账号密码）
                        logger.warning(f"更新Cookie到数据库失败: {self.cookie_id}，但不使用save_cookie避免覆盖账号密码")
                    else:
                        logger.warning(f"已更新Cookie到数据库: {self.cookie_id}")
                except Exception as e:
                    logger.error(f"更新数据库Cookie失败: {self._safe_str(e)}")
                    # 发送数据库更新失败通知
                    await self.send_token_refresh_notification(f"数据库Cookie更新失败: {str(e)}", "db_update_failed")
            else:
                logger.warning("Cookie ID不存在，无法更新数据库")
                # 发送Cookie ID缺失通知
                await self.send_token_refresh_notification("Cookie ID不存在，无法更新数据库", "cookie_id_missing")

        except Exception as e:
            logger.error(f"更新Cookie失败: {self._safe_str(e)}")
            # 发送Cookie更新失败通知
            await self.send_token_refresh_notification(f"Cookie更新失败: {str(e)}", "cookie_update_failed")

    async def _recover_via_slider_password_login(
        self,
        username: str,
        password: str,
        trigger_reason: str = "",
    ) -> str:
        """后台用账密 + 滑块隐身自动重登，返回新的 Cookie 字符串（失败返回空串）。

        复用移植自上游的 XianyuSliderStealth（隐身启动参数 + 人性化滑块轨迹），以同步
        Playwright 跑在独立线程里。带 60s 冷却，避免过期风暴时反复拉起浏览器。能否真正
        过闲鱼当前滑块由运行时决定；未过或需人脸验证时返回空串，交由调用方回落兜底。
        """
        del trigger_reason
        now = time.time()
        last = XianyuLive._last_password_login_time.get(self.cookie_id, 0)
        if now - last < XianyuLive._password_login_cooldown:
            remaining = int(XianyuLive._password_login_cooldown - (now - last))
            logger.info(f"【{self.cookie_id}】密码自愈重登冷却中（{remaining}s），跳过本次")
            return ""
        XianyuLive._last_password_login_time[self.cookie_id] = now

        def _run() -> dict:
            from utils.xianyu_slider_stealth import XianyuSliderStealth
            slider = XianyuSliderStealth(user_id=self.cookie_id, headless=True)
            result = slider.login_with_password_playwright(
                username,
                password,
                show_browser=False,
            )
            return result or {}

        try:
            cookies = await asyncio.to_thread(_run)
        except Exception as exc:
            logger.error(f"【{self.cookie_id}】滑块密码自愈重登异常: {type(exc).__name__}")
            return ""
        if not cookies or not str(cookies.get("unb") or "").strip():
            logger.warning(
                f"【{self.cookie_id}】滑块密码自愈重登未拿到有效 Cookie（可能未过滑块或需人脸验证）"
            )
            return ""
        return "; ".join(f"{key}={value}" for key, value in cookies.items() if key and value)

    async def _proxy_preflight_ok(self, purpose: str) -> bool:
        """代理健康门禁：账号配了代理但不可用时拒绝放行，绝不退回机房 IP 直连。

        未配置代理（get_account_proxy_config 返回 None）→ 直接放行，与接入代理前
        字节级一致（无代理账号零行为变化，不做任何多余探测）。配了代理才校验：
        协议不支持（SOCKS5 等 Chromium 用不了的）或出口探测不通 → 记录状态 + 告警
        日志 + 拒绝放行，让上层跳过该号，而不是带着坏代理去打 passport/滑块——那会从
        机房 IP 直出、在风控下可能伤号。探测结果落库 proxy_last_status 供前端/观察脚本可见。
        """
        from db_manager import db_manager
        from utils.browser_runtime import probe_proxy_egress, proxy_config_status

        account_proxy = await asyncio.to_thread(
            db_manager.get_account_proxy_config, self.cookie_id
        )
        if not account_proxy:
            return True
        scheme_status = proxy_config_status(account_proxy)
        if scheme_status["status"] == "unsupported_scheme":
            bad = scheme_status["scheme"].upper()
            await asyncio.to_thread(
                db_manager.record_proxy_probe,
                self.cookie_id,
                ip="",
                status="unsupported_scheme",
            )
            logger.warning(
                f"【{self.cookie_id}】{purpose}前代理协议不支持"
                f"（{bad}，Chromium 不认其账密认证），跳过该号不走直连——请改用 HTTP/HTTPS 代理"
            )
            return False
        probe = await asyncio.to_thread(probe_proxy_egress, account_proxy)
        await asyncio.to_thread(
            db_manager.record_proxy_probe,
            self.cookie_id,
            ip=str(probe.get("ip") or ""),
            status=str(probe.get("status") or ""),
        )
        if not probe.get("ok"):
            logger.warning(
                f"【{self.cookie_id}】{purpose}前代理出口不通"
                f"（status={probe.get('status')}），跳过该号不退回机房 IP 直连"
            )
            return False
        return True

    async def _recover_via_passwordless_refresh(
        self,
        profile_unb: str,
        current_cookie: str,
        trigger_reason: str = "",
    ) -> str:
        """用持久浏览器记忆免密续签，返回新 Cookie 字符串（失败返回空串）。"""
        del trigger_reason
        self._last_l3_error_code = ""
        now = time.time()
        last = XianyuLive._last_l3_refresh_time.get(self.cookie_id, 0)
        if now - last < XianyuLive._l3_refresh_cooldown:
            remaining = int(XianyuLive._l3_refresh_cooldown - (now - last))
            logger.info(f"【{self.cookie_id}】免密续签冷却中（{remaining}s），跳过本次")
            self._last_l3_error_code = "session_probe_retryable"
            return ""
        XianyuLive._last_l3_refresh_time[self.cookie_id] = now

        def _run():
            from utils.xianyu_l3_memory import passwordless_refresh

            account_proxy = db_manager.get_account_proxy_config(self.cookie_id)
            return passwordless_refresh(profile_unb, current_cookie, proxy=account_proxy)

        try:
            result = await asyncio.to_thread(_run)
        except Exception as exc:
            logger.error(f"【{self.cookie_id}】免密续签异常: {type(exc).__name__}")
            self._last_l3_error_code = "browser_error"
            return ""
        self._last_l3_error_code = str(getattr(result, "error_code", "") or "")
        if not getattr(result, "succeeded", False):
            logger.warning(
                f"【{self.cookie_id}】免密续签未成功: "
                f"{self._last_l3_error_code or 'unknown'}"
            )
            return ""
        from utils.xianyu_l3_memory import cookies_to_string as l3_cookies_to_string

        return l3_cookies_to_string(result.cookies)

    async def _recover_via_cdp(
        self,
        profile_unb: str,
        trigger_reason: str = "",
    ) -> str:
        """可选：接管本机已开调试端口的真实 Chrome，失败关闭。"""
        del trigger_reason
        from utils.xianyu_l3_memory import default_cdp_endpoint, import_from_cdp

        if not default_cdp_endpoint():
            return ""

        def _run():
            return import_from_cdp(expected_unb=profile_unb, persist_profile=True)

        try:
            result = await asyncio.to_thread(_run)
        except Exception as exc:
            logger.error(f"【{self.cookie_id}】CDP 接管异常: {type(exc).__name__}")
            return ""
        # CDP 的目标是拿到有效登录态；L3 建档只是附带产物，可能因为档案被占用
        # 等原因失败。按 status 判定成功，has_l3_memory 如实回写，不虚标记忆。
        if getattr(result, "status", "") != "success" or not getattr(result, "cookies", None):
            logger.warning(
                f"【{self.cookie_id}】CDP 接管未成功: "
                f"{getattr(result, 'error_code', '') or 'unknown'}"
            )
            return ""
        from db_manager import db_manager
        from utils.xianyu_l3_memory import cookies_to_string as l3_cookies_to_string

        await asyncio.to_thread(
            db_manager.mark_l3_memory,
            self.cookie_id,
            ready=bool(getattr(result, "has_l3_memory", False)),
        )
        return l3_cookies_to_string(result.cookies)

    async def _try_password_login_refresh(
        self,
        trigger_reason: str = "令牌/Session过期",
        reuse_active_registration: bool = False,
    ):
        """Probe lightly, then recover through the dedicated official profile."""
        from account_session_refresh import (
            active_refresh_registry,
            is_valid_account_login_username,
            normalize_login_method,
            official_login_error_message,
            password_refresh_requires_manual_reauth,
            reauth_message_for,
            remove_verification_image,
            supports_automatic_refresh,
        )
        from db_manager import db_manager
        from utils.xianyu_official_login import (
            OfficialLoginWorker,
            XianyuOfficialLoginService,
        )

        account_info = await asyncio.to_thread(
            db_manager.get_cookie_details,
            self.cookie_id,
        )
        if not account_info:
            db_manager.update_account_session_refresh(
                self.cookie_id,
                state="failed",
                trigger=trigger_reason,
                message="无法读取账号信息",
                error_code="account_missing",
            )
            return False

        existing_refresh_status = await asyncio.to_thread(
            db_manager.get_account_session_refresh,
            self.cookie_id,
        )
        if existing_refresh_status.get("state") == "manual_reauth_required":
            logger.info(
                f"【{self.cookie_id}】账号已要求人工重新登录，未重复启动官方浏览器"
            )
            return False

        scheduled_refresh = trigger_reason.startswith("定时 Cookie 刷新")
        if scheduled_refresh and not bool(getattr(self, "cookie_refresh_enabled", False)):
            db_manager.update_account_session_refresh(
                self.cookie_id,
                state="failed",
                trigger=trigger_reason,
                message="预防性 Cookie 刷新已关闭，未启动官方浏览器",
                error_code="automatic_refresh_disabled",
            )
            return False

        if not supports_automatic_refresh(
            account_info.get("login_method"),
            account_info.get("username"),
            bool(account_info.get("password")),
            bool(account_info.get("has_l3_memory")),
        ):
            login_method = normalize_login_method(account_info.get("login_method"))
            message = reauth_message_for(login_method)
            await asyncio.to_thread(db_manager.mark_cookie_expired, self.cookie_id)
            await self._enter_manual_reauth_required(
                trigger=trigger_reason,
                message=message,
            )
            logger.info(
                f"【{self.cookie_id}】当前登录方式需要人工重新登录，未启动官方浏览器"
            )
            return False

        db_cookie_value = str(account_info.get("value") or self.cookies_str)
        profile_unb = str(account_info.get("xianyu_unb") or "").strip()
        db_cookie_unb = str(trans_cookies(db_cookie_value).get("unb") or "").strip()
        try:
            refresh_revision = int(account_info["cookie_revision"])
            refresh_user_id = int(account_info.get("user_id"))
        except (KeyError, TypeError, ValueError):
            refresh_revision = -1
            refresh_user_id = 0
        if (
            refresh_user_id != int(self.user_id)
            or refresh_revision < 0
            or not profile_unb
            or db_cookie_unb != profile_unb
        ):
            await asyncio.to_thread(db_manager.mark_cookie_expired, self.cookie_id)
            await self._enter_manual_reauth_required(
                trigger=trigger_reason,
                message=reauth_message_for("password"),
            )
            return False

        browser_user_agent = str(account_info.get("browser_user_agent") or "").strip()
        device_id = str(getattr(self, "device_id", "") or "").strip()
        if not await self._proxy_preflight_ok("会话续签"):
            db_manager.update_account_session_refresh(
                self.cookie_id,
                state="failed",
                trigger=trigger_reason,
                message="账号代理不可用，已跳过续签（未退回机房 IP 直连，待代理恢复自动重试）",
                error_code="proxy_unhealthy",
            )
            return False
        account_proxy = db_manager.get_account_proxy_config(self.cookie_id)
        probe_result = await probe_message_session_async(
            db_cookie_value,
            browser_user_agent or detect_default_browser_user_agent(),
            proxy=account_proxy,
            **({"device_id": device_id} if device_id else {}),
        )
        if probe_result.succeeded:
            refreshed_cookie = probe_cookies_to_string(probe_result.cookies)
            if refreshed_cookie and refreshed_cookie != self.cookies_str:
                updated = await self._update_cookies_and_restart(
                    refreshed_cookie,
                    browser_user_agent=browser_user_agent or detect_default_browser_user_agent(),
                    access_token=probe_result.access_token,
                    expected_revision=refresh_revision,
                    expected_xianyu_unb=profile_unb,
                )
                if not updated:
                    db_manager.update_account_session_refresh(
                        self.cookie_id,
                        state="failed",
                        trigger=trigger_reason,
                        message="平台会话有效，但 Cookie 合并后监听交接失败",
                        error_code="listener_handoff_failed",
                    )
                    return False
            db_manager.update_account_session_refresh(
                self.cookie_id,
                state="success",
                trigger=trigger_reason,
                message="平台会话仍然有效，已完成轻量续期",
            )
            db_manager.mark_cookie_validated(self.cookie_id)
            logger.info(f"【{self.cookie_id}】平台轻量状态检查通过，未启动官方浏览器")
            return True
        if probe_result.status == PROBE_RETRYABLE_ERROR:
            db_manager.update_account_session_refresh(
                self.cookie_id,
                state="failed",
                trigger=trigger_reason,
                message="平台状态检查出现临时异常，已保留原 Cookie",
                error_code="session_probe_retryable",
            )
            logger.warning(f"【{self.cookie_id}】平台状态检查为临时异常，保留原 Cookie 并进入退避")
            return False
        if probe_result.status == PROBE_VERIFICATION_REQUIRED:
            await asyncio.to_thread(db_manager.mark_cookie_expired, self.cookie_id)
            await self._enter_manual_reauth_required(
                trigger=trigger_reason,
                message=probe_result.message or reauth_message_for(
                    account_info.get("login_method")
                ),
            )
            db_manager.update_account_session_refresh(
                self.cookie_id,
                state="manual_reauth_required",
                trigger=trigger_reason,
                message=probe_result.message or "平台要求人工验证，已停止自动续签",
                error_code=probe_result.error_code or "human_verification_required",
            )
            logger.warning(f"【{self.cookie_id}】平台风控要求人工验证，未启动自动续签")
            return False

        # 平台会话确已失效。优先用 L3 浏览器记忆免密续签；失败再回落账密滑块。
        recover_username = str(account_info.get("username") or "").strip()
        recover_password = str(account_info.get("password") or "")
        if bool(account_info.get("has_l3_memory")):
            l3_cookie = await self._recover_via_passwordless_refresh(
                profile_unb,
                db_cookie_value,
                trigger_reason,
            )
            if l3_cookie:
                updated = await self._update_cookies_and_restart(
                    l3_cookie,
                    browser_user_agent=browser_user_agent or detect_default_browser_user_agent(),
                    expected_revision=refresh_revision,
                    expected_xianyu_unb=profile_unb,
                )
                if updated:
                    db_manager.update_account_session_refresh(
                        self.cookie_id,
                        state="success",
                        trigger=trigger_reason,
                        message="浏览器记忆免密续签成功",
                    )
                    db_manager.mark_cookie_validated(self.cookie_id)
                    logger.info(f"【{self.cookie_id}】免密续签成功，已交接新监听")
                    return True
                db_manager.update_account_session_refresh(
                    self.cookie_id,
                    state="failed",
                    trigger=trigger_reason,
                    message="免密续签已拿到 Cookie，但监听交接失败",
                    error_code="listener_handoff_failed",
                )
                return False
            l3_error = str(getattr(self, "_last_l3_error_code", "") or "")
            if password_refresh_requires_manual_reauth(l3_error):
                await asyncio.to_thread(db_manager.mark_l3_memory, self.cookie_id, ready=False)
                await asyncio.to_thread(db_manager.mark_cookie_expired, self.cookie_id)
                await self._enter_manual_reauth_required(
                    trigger=trigger_reason,
                    message=official_login_error_message(
                        l3_error,
                        fallback="浏览器免密记忆已失效，请重新扫码",
                    ),
                )
                return False
            if not (is_valid_account_login_username(recover_username) and recover_password):
                db_manager.update_account_session_refresh(
                    self.cookie_id,
                    state="failed",
                    trigger=trigger_reason,
                    message="免密续签暂时失败，将稍后重试",
                    error_code=l3_error or "session_probe_retryable",
                )
                return False

        if is_valid_account_login_username(recover_username) and recover_password:
            slider_cookie = await self._recover_via_slider_password_login(
                recover_username,
                recover_password,
                trigger_reason,
            )
            if slider_cookie:
                updated = await self._update_cookies_and_restart(
                    slider_cookie,
                    browser_user_agent=browser_user_agent or detect_default_browser_user_agent(),
                    expected_revision=refresh_revision,
                    expected_xianyu_unb=profile_unb,
                )
                if updated:
                    db_manager.update_account_session_refresh(
                        self.cookie_id,
                        state="success",
                        trigger=trigger_reason,
                        message="账密 + 滑块隐身后台自动重登成功",
                    )
                    db_manager.mark_cookie_validated(self.cookie_id)
                    logger.info(f"【{self.cookie_id}】滑块隐身密码自愈重登成功，已交接新监听")
                    return True
                logger.warning(
                    f"【{self.cookie_id}】滑块登录拿到 Cookie 但监听交接失败，回落官方验证会话"
                )

        cdp_cookie = await self._recover_via_cdp(profile_unb, trigger_reason)
        if cdp_cookie:
            updated = await self._update_cookies_and_restart(
                cdp_cookie,
                browser_user_agent=browser_user_agent or detect_default_browser_user_agent(),
                expected_revision=refresh_revision,
                expected_xianyu_unb=profile_unb,
            )
            if updated:
                db_manager.update_account_session_refresh(
                    self.cookie_id,
                    state="success",
                    trigger=trigger_reason,
                    message="已从本机 Chrome 接管闲鱼登录态",
                )
                db_manager.mark_cookie_validated(self.cookie_id)
                logger.info(f"【{self.cookie_id}】CDP 接管成功，已交接新监听")
                return True

        worker = OfficialLoginWorker()
        if reuse_active_registration:
            if not active_refresh_registry.is_active(self.cookie_id):
                active_refresh_registry.register(self.cookie_id, worker)
            elif not active_refresh_registry.set_worker(self.cookie_id, worker):
                return False
        elif not active_refresh_registry.register(self.cookie_id, worker):
            logger.info(f"【{self.cookie_id}】已有刷新或验证会话，跳过重复启动")
            return False

        previous_status = db_manager.get_account_session_refresh(self.cookie_id)
        previous_image_url = previous_status.get("verification_image_url") or ""
        previous_image_path = previous_image_url.lstrip("/")
        db_manager.update_account_session_refresh(
            self.cookie_id,
            state="refreshing",
            trigger=trigger_reason,
            message="正在启动一次闲鱼官方验证会话",
            expires_at=time.time() + 900,
        )

        result = None
        handoff_completed = False
        try:
            owner_loop = asyncio.get_running_loop()
            verification_tasks = []
            verification_notice_sent = False

            async def apply_verification_status(snapshot):
                nonlocal verification_notice_sent
                safe_message, image_path = snapshot
                db_manager.update_account_session_refresh(
                    self.cookie_id,
                    state="verification_required",
                    trigger=trigger_reason,
                    message=safe_message,
                    verification_image_path=image_path,
                    expires_at=time.time() + 900,
                )
                if verification_notice_sent:
                    return
                verification_notice_sent = True
                await self.send_token_refresh_notification(
                    error_message=safe_message,
                    notification_type="token_refresh",
                    chat_id=None,
                    attachment_path=image_path or None,
                    verification_url=None,
                )

            def schedule_verification_status(snapshot):
                verification_tasks.append(
                    asyncio.create_task(apply_verification_status(snapshot))
                )

            def notification_callback(status_result):
                if status_result.status != "verification_required":
                    return
                snapshot = (
                    status_result.message
                    or "需要完成闲鱼身份验证，后台正在自动检测",
                    str(status_result.verification_image_path or ""),
                )
                owner_loop.call_soon_threadsafe(
                    schedule_verification_status,
                    snapshot,
                )

            async def commit_validated_result(validated_result):
                updated = await self._update_cookies_and_restart(
                    XianyuOfficialLoginService.cookies_to_string(
                        validated_result.cookies
                    ),
                    browser_user_agent=validated_result.browser_user_agent,
                    access_token=validated_result.access_token,
                    expected_revision=refresh_revision,
                    expected_xianyu_unb=profile_unb,
                )
                if not updated:
                    db_manager.update_account_session_refresh(
                        self.cookie_id,
                        state="failed",
                        trigger=trigger_reason,
                        message="Token 已验证，但 Cookie 或监听交接失败",
                        error_code="listener_handoff_failed",
                    )
                    return False
                self.pending_verification_url = ""
                db_manager.update_account_session_refresh(
                    self.cookie_id,
                    state="success",
                    trigger=trigger_reason,
                    message="Cookie 已刷新，消息 Token 已验证，账号监听已恢复",
                )
                db_manager.mark_cookie_validated(self.cookie_id)
                return True

            def validated_callback(validated_result):
                nonlocal handoff_completed
                future = asyncio.run_coroutine_threadsafe(
                    commit_validated_result(validated_result),
                    owner_loop,
                )
                handoff_completed = bool(future.result(timeout=180))
                return handoff_completed

            service = XianyuOfficialLoginService(
                proxy=db_manager.get_account_proxy_config(self.cookie_id)
            )
            username = str(account_info.get("username") or "").strip()
            password = str(account_info.get("password") or "")
            if not is_valid_account_login_username(username):
                username = ""
                password = ""
            result = await asyncio.to_thread(
                service.refresh_session,
                profile_unb=profile_unb,
                current_cookie=db_cookie_value,
                account=username,
                password=password,
                show_browser=bool(account_info.get("show_browser")),
                allow_password=bool(username and password),
                worker=worker,
                on_status=notification_callback,
                on_validated=validated_callback,
                initial_verification_url=getattr(
                    self,
                    "pending_verification_url",
                    "",
                ),
            )
            await asyncio.sleep(0)
            if verification_tasks:
                await asyncio.gather(*verification_tasks)

            if result.succeeded and not handoff_completed:
                handoff_completed = await commit_validated_result(result)
            if result.succeeded and handoff_completed:
                logger.info(f"【{self.cookie_id}】官方验证与监听交接完成")
                return True

            current_status = db_manager.get_account_session_refresh(self.cookie_id)
            if current_status.get("state") != "failed":
                requires_manual_reauth = password_refresh_requires_manual_reauth(
                    result.error_code
                )
                result_state = (
                    "manual_reauth_required"
                    if requires_manual_reauth
                    else result.status
                    if result.status in {"timeout", "cancelled"}
                    else "failed"
                )
                if requires_manual_reauth:
                    db_manager.mark_cookie_expired(self.cookie_id)
                db_manager.update_account_session_refresh(
                    self.cookie_id,
                    state=result_state,
                    trigger=trigger_reason,
                    message=(
                        reauth_message_for("password")
                        if requires_manual_reauth
                        else official_login_error_message(result.error_code)
                    ),
                    error_code=(
                        "manual_reauth_required"
                        if requires_manual_reauth
                        else result.error_code or "login_failed"
                    ),
                )
            return False
        except asyncio.CancelledError:
            worker.close_browser()
            raise
        except Exception as refresh_error:
            logger.error(
                f"【{self.cookie_id}】官方验证会话异常: {type(refresh_error).__name__}"
            )
            db_manager.update_account_session_refresh(
                self.cookie_id,
                state="failed",
                trigger=trigger_reason,
                message="官方验证会话出现异常",
                error_code="refresh_exception",
            )
            return False
        finally:
            if active_refresh_registry.consume_cancelled(self.cookie_id):
                current_state = db_manager.get_account_session_refresh(
                    self.cookie_id
                ).get("state")
                if current_state != "timeout":
                    db_manager.update_account_session_refresh(
                        self.cookie_id,
                        state="cancelled",
                        trigger=trigger_reason,
                        message="Cookie 刷新已取消",
                        error_code="cancelled",
                    )
            active_refresh_registry.unregister(self.cookie_id, worker)
            final_status = db_manager.get_account_session_refresh(self.cookie_id)
            if final_status.get("state") != "verification_required":
                remove_verification_image(previous_image_path)
                remove_verification_image(
                    result.verification_image_path if result is not None else ""
                )

    async def _verify_cookie_validity(self) -> dict:
        """验证Cookie的有效性，通过实际调用API测试

        Returns:
            dict: {
                'valid': bool,  # 总体是否有效
                'confirm_api': bool,  # 确认发货API是否有效
                'image_api': bool,  # 图片上传API是否有效
                'details': str  # 详细信息
            }
        """
        logger.info(f"【{self.cookie_id}】开始验证Cookie有效性（使用真实API调用）...")

        result = {
            'valid': True,
            'confirm_api': None,
            'image_api': None,
            'details': []
        }

        # 2. 测试图片上传API - 创建测试图片并实际上传
        try:
            logger.info(f"【{self.cookie_id}】测试图片上传API（使用测试图片实际上传）...")

            # 创建一个最小的测试图片（1x1像素的PNG）
            import tempfile
            import os
            from PIL import Image

            # 验证图片也必须位于受控上传根，避免为上传器开放任意本地路径。
            from utils.image_utils import image_manager

            temp_file = tempfile.NamedTemporaryFile(
                dir=image_manager.upload_root,
                prefix="cookie_test_",
                suffix=".png",
                delete=False,
            )
            test_image_path = temp_file.name
            temp_file.close()

            try:
                # 创建1x1像素的白色图片
                img = Image.new('RGB', (1, 1), color='white')
                img.save(test_image_path, 'PNG')
                logger.info(f"【{self.cookie_id}】已创建测试图片: {test_image_path}")

                # 创建图片上传实例
                from utils.image_uploader import ImageUploader
                uploader = ImageUploader(cookies_str=self.cookies_str)

                # 创建session
                await uploader.create_session()

                try:
                    # 实际上传测试图片
                    upload_result = await uploader.upload_image(test_image_path)
                finally:
                    # 确保关闭session
                    await uploader.close_session()

                # 分析上传结果
                if upload_result:
                    # 上传成功，Cookie有效
                    logger.info(f"【{self.cookie_id}】✅ 图片上传API验证通过: 上传成功 ({upload_result[:50]}...)")
                    result['image_api'] = True
                    result['details'].append("图片上传API: 通过验证")
                else:
                    # 上传失败，需要进一步判断原因
                    # 如果是Cookie失效，通常会返回HTML登录页面
                    logger.warning(f"【{self.cookie_id}】❌ 图片上传API验证失败: 上传失败（可能是Cookie失效）")
                    result['image_api'] = False
                    result['valid'] = False
                    result['details'].append("图片上传API: 上传失败，可能Cookie已失效")

            finally:
                # 清理测试图片
                if os.path.exists(test_image_path):
                    try:
                        os.remove(test_image_path)
                        logger.debug(f"【{self.cookie_id}】已删除测试图片")
                    except:
                        pass

        except Exception as e:
            error_str = self._safe_str(e)
            logger.error(f"【{self.cookie_id}】图片上传API验证异常: {error_str}")
            # 图片上传异常，标记为失败
            result['image_api'] = False
            result['valid'] = False
            result['details'].append(f"图片上传API: 验证异常 - {error_str[:50]}")

        # 汇总结果
        if result['valid']:
            logger.info(f"【{self.cookie_id}】✅ Cookie验证通过: 所有关键API均可用")
        else:
            logger.warning(f"【{self.cookie_id}】❌ Cookie验证失败:")
            for detail in result['details']:
                logger.warning(f"【{self.cookie_id}】  - {detail}")

        result['details'] = '; '.join(result['details'])
        return result

    async def _restart_instance(self):
        """重启XianyuLive实例

        ⚠️ 注意：此方法会触发当前任务被取消！
        调用此方法后，当前任务会立即被 CookieManager 取消，
        因此不要在此方法后执行任何重要操作。
        """
        try:
            logger.info(f"【{self.cookie_id}】准备重启实例...")

            # 导入CookieManager
            from cookie_manager import manager as cookie_manager

            if cookie_manager:
                # 通过CookieManager重启实例
                logger.info(f"【{self.cookie_id}】通过CookieManager重启实例...")

                # ⚠️ 重要：不要等待重启完成！
                # cookie_manager.update_cookie() 会立即取消当前任务
                # 如果我们等待它完成，会导致 CancelledError 中断等待
                # 正确的做法是：触发重启后立即返回，让任务自然退出

                import threading

                def trigger_restart():
                    """在后台线程中触发重启，不阻塞当前任务"""
                    try:
                        # 给当前任务一点时间完成清理（避免竞态条件）
                        import time
                        time.sleep(0.5)

                        # save_to_db=False 因为 update_config_cookies 已经保存过了
                        restart_anchor = time.time()
                        cookie_manager.update_cookie(
                            self.cookie_id,
                            self.cookies_str,
                            save_to_db=False,
                            runtime_state={
                                'cookie_refresh_anchor': restart_anchor,
                                'item_sync_anchor': restart_anchor,
                            },
                        )
                        logger.info(f"【{self.cookie_id}】实例重启请求已触发")
                    except Exception as e:
                        logger.error(f"【{self.cookie_id}】触发实例重启失败: {e}")
                        import traceback
                        logger.error(f"【{self.cookie_id}】重启失败详情:\n{traceback.format_exc()}")

                # 在后台线程中触发重启
                restart_thread = threading.Thread(target=trigger_restart, daemon=True)
                restart_thread.start()

                logger.info(f"【{self.cookie_id}】实例重启已触发，当前任务即将退出...")
                logger.warning(f"【{self.cookie_id}】注意：重启请求已发送，CookieManager将在0.5秒后取消当前任务并启动新实例")

            else:
                logger.warning(f"【{self.cookie_id}】CookieManager不可用，无法重启实例")

        except Exception as e:
            logger.error(f"【{self.cookie_id}】重启实例失败: {self._safe_str(e)}")
            import traceback
            logger.error(f"【{self.cookie_id}】重启失败堆栈:\n{traceback.format_exc()}")
            # 发送重启失败通知
            try:
                await self.send_token_refresh_notification(f"实例重启失败: {str(e)}", "instance_restart_failed")
            except Exception as notify_e:
                logger.error(f"【{self.cookie_id}】发送重启失败通知时出错: {self._safe_str(notify_e)}")

    async def save_item_info_to_db(self, item_id: str, item_detail: str = None, item_title: str = None):
        """保存商品信息到数据库

        Args:
            item_id: 商品ID
            item_detail: 商品详情内容（可以是任意格式的文本）
            item_title: 商品标题
        """
        try:
            # 跳过以 auto_ 开头的商品ID
            if item_id and item_id.startswith('auto_'):
                logger.warning(f"跳过保存自动生成的商品ID: {item_id}")
                return

            # 验证：如果只有商品ID，没有商品标题和商品详情，则不插入数据库
            if not item_title and not item_detail:
                logger.warning(f"跳过保存商品信息：缺少商品标题和详情 - {item_id}")
                return

            # 如果有商品标题但没有详情，也跳过（根据需求，需要同时有标题和详情）
            if not item_title or not item_detail:
                logger.warning(f"跳过保存商品信息：商品标题或详情不完整 - {item_id}")
                return

            from db_manager import db_manager

            # 直接使用传入的详情内容
            item_data = item_detail

            # 保存到数据库
            success = db_manager.save_item_info(self.cookie_id, item_id, item_data)
            if success:
                logger.info(f"商品信息已保存到数据库: {item_id}")
            else:
                logger.warning(f"保存商品信息到数据库失败: {item_id}")

        except Exception as e:
            logger.error(f"保存商品信息到数据库异常: {self._safe_str(e)}")

    async def save_item_detail_only(self, item_id, item_detail):
        """仅保存商品详情（不影响标题等基本信息）"""
        try:
            from db_manager import db_manager

            # 使用专门的详情更新方法
            success = db_manager.update_item_detail(self.cookie_id, item_id, item_detail)

            if success:
                logger.info(f"商品详情已更新: {item_id}")
            else:
                logger.warning(f"更新商品详情失败: {item_id}")

            return success

        except Exception as e:
            logger.error(f"更新商品详情异常: {self._safe_str(e)}")
            return False

    async def fetch_item_detail_from_api(self, item_id: str) -> str:
        """获取商品详情（使用浏览器获取，支持24小时缓存）

        Args:
            item_id: 商品ID

        Returns:
            str: 商品详情文本，获取失败返回空字符串
        """
        try:
            # 检查是否启用自动获取功能
            from config import config
            auto_fetch_config = config.get('ITEM_DETAIL', {}).get('auto_fetch', {})

            if not auto_fetch_config.get('enabled', True):
                logger.warning(f"自动获取商品详情功能已禁用: {item_id}")
                return ""

            # 1. 首先检查缓存（24小时有效）
            async with self._item_detail_cache_lock:
                if item_id in self._item_detail_cache:
                    cache_data = self._item_detail_cache[item_id]
                    cache_time = cache_data['timestamp']
                    current_time = time.time()

                    # 检查缓存是否在24小时内
                    if current_time - cache_time < self._item_detail_cache_ttl:
                        # 更新访问时间（用于LRU）
                        cache_data['access_time'] = current_time
                        logger.info(f"从缓存获取商品详情: {item_id}")
                        return cache_data['detail']
                    else:
                        # 缓存过期，删除
                        del self._item_detail_cache[item_id]
                        logger.warning(f"缓存已过期，删除: {item_id}")

            # 2. 尝试使用浏览器获取商品详情
            detail_from_browser = await self._fetch_item_detail_from_browser(item_id)
            if detail_from_browser:
                # 保存到缓存（带大小限制）
                await self._add_to_item_cache(item_id, detail_from_browser)
                logger.info(f"成功通过浏览器获取商品详情: {item_id}, 长度: {len(detail_from_browser)}")
                return detail_from_browser

            # 浏览器获取失败
            logger.warning(f"浏览器获取商品详情失败: {item_id}")
            return ""

        except Exception as e:
            logger.error(f"获取商品详情异常: {item_id}, 错误: {self._safe_str(e)}")
            return ""

    async def _add_to_item_cache(self, item_id: str, detail: str):
        """添加商品详情到缓存，实现LRU策略和大小限制

        Args:
            item_id: 商品ID
            detail: 商品详情
        """
        async with self._item_detail_cache_lock:
            current_time = time.time()

            # 检查缓存大小，如果超过限制则清理
            if len(self._item_detail_cache) >= self._item_detail_cache_max_size:
                # 使用LRU策略删除最久未访问的项
                if self._item_detail_cache:
                    # 找到最久未访问的项
                    oldest_item = min(
                        self._item_detail_cache.items(),
                        key=lambda x: x[1].get('access_time', x[1]['timestamp'])
                    )
                    oldest_item_id = oldest_item[0]
                    del self._item_detail_cache[oldest_item_id]
                    logger.warning(f"缓存已满，删除最旧项: {oldest_item_id}")

            # 添加新项到缓存
            self._item_detail_cache[item_id] = {
                'detail': detail,
                'timestamp': current_time,
                'access_time': current_time
            }
            logger.warning(f"添加商品详情到缓存: {item_id}, 当前缓存大小: {len(self._item_detail_cache)}")

    @classmethod
    async def _cleanup_item_cache(cls):
        """清理过期的商品详情缓存"""
        try:
            async with cls._item_detail_cache_lock:
                # 在持有锁时也要能响应取消信号
                await asyncio.sleep(0)

                current_time = time.time()
                expired_items = []

                # 找出所有过期的项
                for item_id, cache_data in cls._item_detail_cache.items():
                    # 在循环中也要能响应取消信号
                    await asyncio.sleep(0)
                    if current_time - cache_data['timestamp'] >= cls._item_detail_cache_ttl:
                        expired_items.append(item_id)

                # 删除过期项
                for item_id in expired_items:
                    await asyncio.sleep(0)  # 让出控制权
                    del cls._item_detail_cache[item_id]

                if expired_items:
                    logger.info(f"清理了 {len(expired_items)} 个过期的商品详情缓存")

                return len(expired_items)
        except asyncio.CancelledError:
            # 如果被取消，确保锁能正确释放
            raise

    async def _fetch_item_detail_from_browser(self, item_id: str) -> str:
        """使用浏览器获取商品详情"""
        playwright = None
        browser = None
        try:
            from playwright.async_api import async_playwright

            logger.info(f"开始使用浏览器获取商品详情: {item_id}")

            playwright = await async_playwright().start()

            # 启动仅用于商品详情补充的短生命周期浏览器。
            browser_args = [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
                '--disable-features=TranslateUI',
                '--disable-ipc-flooding-protection',
                '--disable-extensions',
                '--disable-default-apps',
                '--disable-sync',
                '--disable-translate',
                '--hide-scrollbars',
                '--mute-audio',
                '--no-default-browser-check',
                '--no-pings'
            ]

            # 在Docker环境中添加额外参数
            if os.getenv('DOCKER_ENV'):
                browser_args.extend([
                    # '--single-process',  # 注释掉，避免多用户并发时的进程冲突和资源泄漏
                    '--disable-background-networking',
                    '--disable-client-side-phishing-detection',
                    '--disable-hang-monitor',
                    '--disable-popup-blocking',
                    '--disable-prompt-on-repost',
                    '--disable-web-resources',
                    '--metrics-recording-only',
                    '--safebrowsing-disable-auto-update',
                    '--enable-automation',
                    '--password-store=basic',
                    '--use-mock-keychain'
                ])

            browser = await playwright.chromium.launch(
                headless=True,
                args=browser_args
            )

            # 创建浏览器上下文
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
            )

            # 设置Cookie
            cookies = []
            for cookie_pair in self.cookies_str.split('; '):
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.goofish.com',
                        'path': '/'
                    })

            await context.add_cookies(cookies)
            logger.warning(f"已设置 {len(cookies)} 个Cookie")

            # 创建页面
            page = await context.new_page()

            # 构造商品详情页面URL
            item_url = f"https://www.goofish.com/item?id={item_id}"
            logger.info(f"访问商品页面: {item_url}")

            # 访问页面
            await page.goto(item_url, wait_until='networkidle', timeout=30000)

            # 等待页面完全加载
            await asyncio.sleep(3)

            # 获取商品详情内容
            detail_text = ""
            try:
                # 等待目标元素出现
                await page.wait_for_selector('.desc--GaIUKUQY', timeout=10000)

                # 获取商品详情文本
                detail_element = await page.query_selector('.desc--GaIUKUQY')
                if detail_element:
                    detail_text = await detail_element.inner_text()
                    logger.info(f"成功获取商品详情: {item_id}, 长度: {len(detail_text)}")
                    return detail_text.strip()
                else:
                    logger.warning(f"未找到商品详情元素: {item_id}")

            except Exception as e:
                logger.warning(f"获取商品详情元素失败: {item_id}, 错误: {self._safe_str(e)}")

            return ""

        except Exception as e:
            logger.error(f"浏览器获取商品详情异常: {item_id}, 错误: {self._safe_str(e)}")
            return ""
        finally:
            # 确保资源被正确清理
            try:
                if browser:
                    await browser.close()
                    logger.warning(f"Browser已关闭: {item_id}")
            except Exception as e:
                logger.warning(f"关闭browser时出错: {self._safe_str(e)}")

            try:
                if playwright:
                    await playwright.stop()
                    logger.warning(f"Playwright已停止: {item_id}")
            except Exception as e:
                logger.warning(f"停止playwright时出错: {self._safe_str(e)}")


    async def save_items_list_to_db(self, items_list, reconcile=False):
        """批量保存商品列表信息到数据库（并发安全）

        Args:
            items_list: 从get_item_list_info获取的商品列表
        """
        try:
            from db_manager import db_manager

            # 准备在售目录数据，商品详情文本与平台元数据分开保存。
            batch_data = []
            items_need_detail = []  # 需要获取详情的商品列表

            for item in items_list:
                item_id = item.get('id')
                if not item_id or item_id.startswith('auto_'):
                    continue

                try:
                    platform_status = int(item.get('item_status'))
                except (TypeError, ValueError):
                    continue
                if platform_status != 0:
                    continue

                # 构造商品详情数据
                item_detail = {
                    'title': item.get('title', ''),
                    'price': item.get('price', ''),
                    'price_text': item.get('price_text', ''),
                    'category_id': item.get('category_id', ''),
                    'auction_type': item.get('auction_type', ''),
                    'item_status': item.get('item_status', 0),
                    'detail_url': item.get('detail_url', ''),
                    'web_url': item.get('web_url', ''),  # Web可访问URL
                    'pic_info': item.get('pic_info', {}),
                    'detail_params': item.get('detail_params', {}),
                    'track_params': item.get('track_params', {}),
                    'item_label_data': item.get('item_label_data', {}),
                    'card_type': item.get('card_type', 0)
                }

                # 检查数据库中是否已有详情
                existing_item = db_manager.get_item_info(self.cookie_id, item_id)
                has_detail = existing_item and existing_item.get('item_detail') and existing_item['item_detail'].strip()

                batch_data.append({
                    'cookie_id': self.cookie_id,
                    'item_id': item_id,
                    'item_title': item.get('title', ''),
                    'item_description': '',  # 暂时为空
                    'item_category': str(item.get('category_id', '')),
                    'item_price': item.get('price_text', ''),
                    'item_image': item.get('item_image', ''),
                    'platform_item_status': platform_status,
                    'catalog_metadata': item_detail,
                })

                # 如果没有详情，添加到需要获取详情的列表
                if not has_detail:
                    items_need_detail.append({
                        'item_id': item_id,
                        'item_title': item.get('title', '')
                    })

            sync_summary = db_manager.reconcile_catalog_items(
                self.cookie_id,
                batch_data,
                reconcile=bool(reconcile),
            )
            logger.info(
                "在售商品目录保存完成: active={}, hidden={}, images_updated={}, failed={}",
                sync_summary.get('active_count', 0),
                sync_summary.get('hidden_count', 0),
                sync_summary.get('images_updated', 0),
                sync_summary.get('failed_count', 0),
            )

            # 异步获取缺失的商品详情
            if items_need_detail:
                from config import config
                auto_fetch_config = config.get('ITEM_DETAIL', {}).get('auto_fetch', {})

                if auto_fetch_config.get('enabled', True):
                    logger.info(f"发现 {len(items_need_detail)} 个商品缺少详情，开始获取...")
                    detail_success_count = await self._fetch_missing_item_details(items_need_detail)
                    logger.info(f"成功获取 {detail_success_count}/{len(items_need_detail)} 个商品的详情")
                else:
                    logger.info(f"发现 {len(items_need_detail)} 个商品缺少详情，但自动获取功能已禁用")

            return sync_summary

        except Exception as e:
            logger.error(f"批量保存商品信息异常: {self._safe_str(e)}")
            return {
                'saved_count': 0,
                'active_count': 0,
                'hidden_count': 0,
                'images_updated': 0,
                'failed_count': len(items_list or []),
            }

    async def _fetch_missing_item_details(self, items_need_detail):
        """批量获取缺失的商品详情

        Args:
            items_need_detail: 需要获取详情的商品列表

        Returns:
            int: 成功获取详情的商品数量
        """
        success_count = 0

        try:
            from db_manager import db_manager
            from config import config

            # 从配置获取并发数量和延迟时间
            auto_fetch_config = config.get('ITEM_DETAIL', {}).get('auto_fetch', {})
            max_concurrent = auto_fetch_config.get('max_concurrent', 3)
            retry_delay = auto_fetch_config.get('retry_delay', 0.5)

            # 限制并发数量，避免对API服务器造成压力
            semaphore = asyncio.Semaphore(max_concurrent)

            async def fetch_single_item_detail(item_info):
                async with semaphore:
                    try:
                        item_id = item_info['item_id']
                        item_title = item_info['item_title']

                        # 获取商品详情
                        item_detail_text = await self.fetch_item_detail_from_api(item_id)

                        if item_detail_text:
                            # 保存详情到数据库
                            success = await self.save_item_detail_only(item_id, item_detail_text)
                            if success:
                                logger.info(f"✅ 成功获取并保存商品详情: {item_id} - {item_title}")
                                return 1
                            else:
                                logger.warning(f"❌ 获取详情成功但保存失败: {item_id}")
                        else:
                            logger.warning(f"❌ 未能获取商品详情: {item_id} - {item_title}")

                        # 添加延迟，避免请求过于频繁
                        await asyncio.sleep(retry_delay)
                        return 0

                    except Exception as e:
                        logger.error(f"获取单个商品详情异常: {item_info.get('item_id', 'unknown')}, 错误: {self._safe_str(e)}")
                        return 0

            # 并发获取所有商品详情
            tasks = [fetch_single_item_detail(item_info) for item_info in items_need_detail]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 统计成功数量
            for result in results:
                if isinstance(result, int):
                    success_count += result
                elif isinstance(result, Exception):
                    logger.error(f"获取商品详情任务异常: {type(result).__name__}")

            return success_count

        except Exception as e:
            logger.error(f"批量获取商品详情异常: {self._safe_str(e)}")
            return success_count

    async def get_item_info(self, item_id, retry_count=0):
        """获取商品信息，自动处理token失效的情况"""
        if retry_count >= 4:  # 最多重试3次
            logger.error("获取商品信息失败，重试次数过多")
            return {"error": "获取商品信息失败，重试次数过多"}

        # 确保session已创建
        if not self.session:
            await self.create_session()

        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': str(int(time.time()) * 1000),
            'sign': '',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': 'mtop.taobao.idle.pc.detail',
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
        }

        data_val = '{"itemId":"' + item_id + '"}'
        data = {
            'data': data_val,
        }

        # 始终从最新的 Cookie 中取签名令牌，日志只记录状态与长度。
        token_source = trans_cookies(self.cookies_str).get('_m_h5_tk', '')
        token = token_source.split('_', 1)[0] if token_source else ''
        logger.debug(
            "商品详情签名令牌状态: ready={}, length={}",
            bool(token),
            len(token),
        )

        from utils.xianyu_utils import generate_sign
        sign = generate_sign(params['t'], token, data_val)
        params['sign'] = sign

        try:
            async with self.session.post(
                _resolve_h5_api_url('https://h5api.m.goofish.com/h5/mtop.taobao.idle.pc.detail/1.0/'),
                params=params,
                data=data
            ) as response:
                res_json = await response.json()

                # 检查并更新Cookie
                if 'set-cookie' in response.headers:
                    new_cookies = {}
                    for cookie in response.headers.getall('set-cookie', []):
                        if '=' in cookie:
                            name, value = cookie.split(';')[0].split('=', 1)
                            new_cookies[name.strip()] = value.strip()

                    # 更新cookies
                    if new_cookies:
                        self.cookies.update(new_cookies)
                        # 生成新的cookie字符串
                        self.cookies_str = '; '.join([f"{k}={v}" for k, v in self.cookies.items()])
                        # 更新数据库中的Cookie
                        await self.update_config_cookies()
                        logger.warning("已更新Cookie到数据库")

                # 检查返回状态
                if isinstance(res_json, dict):
                    ret_value = res_json.get('ret', [])
                    ret_value = ret_value if isinstance(ret_value, list) else []
                    response_codes = [
                        sanitize_runtime_error(str(value)).split('::', 1)[0][:80]
                        for value in ret_value[:3]
                    ]
                    logger.info(
                        "商品详情响应摘要: item_id={}, status={}, has_data={}",
                        item_id,
                        response_codes or ['unknown'],
                        isinstance(res_json.get('data'), dict),
                    )
                    # 检查ret是否包含成功信息
                    if not any('SUCCESS::调用成功' in ret for ret in ret_value):
                        logger.warning(
                            "商品详情API调用失败: {}",
                            response_codes or ['unknown'],
                        )

                        await asyncio.sleep(0.5)
                        return await self.get_item_info(item_id, retry_count + 1)
                    else:
                        logger.warning(f"商品信息获取成功: {item_id}")
                        return res_json
                else:
                    logger.error(
                        "商品详情API返回格式异常: type={}",
                        type(res_json).__name__,
                    )
                    return await self.get_item_info(item_id, retry_count + 1)

        except Exception as e:
            logger.error(f"商品信息API请求异常: {self._safe_str(e)}")
            await asyncio.sleep(0.5)
            return await self.get_item_info(item_id, retry_count + 1)

    def extract_item_id_from_message(self, message):
        """从消息中提取商品ID的辅助方法"""
        try:
            # 方法1: 从message["1"]中提取（如果是字符串格式）
            message_1 = message.get('1')
            if isinstance(message_1, str):
                # 尝试从字符串中提取数字ID
                id_match = re.search(r'(\d{10,})', message_1)
                if id_match:
                    logger.info(f"从message[1]字符串中提取商品ID: {id_match.group(1)}")
                    return id_match.group(1)

            # 方法2: 从message["3"]中提取
            message_3 = message.get('3', {})
            if isinstance(message_3, dict):

                # 从extension中提取
                if 'extension' in message_3:
                    extension = message_3['extension']
                    if isinstance(extension, dict):
                        item_id = extension.get('itemId') or extension.get('item_id')
                        if item_id:
                            logger.info(f"从extension中提取商品ID: {item_id}")
                            return item_id

                # 从bizData中提取
                if 'bizData' in message_3:
                    biz_data = message_3['bizData']
                    if isinstance(biz_data, dict):
                        item_id = biz_data.get('itemId') or biz_data.get('item_id')
                        if item_id:
                            logger.info(f"从bizData中提取商品ID: {item_id}")
                            return item_id

                # 从其他可能的字段中提取
                for key, value in message_3.items():
                    if isinstance(value, dict):
                        item_id = value.get('itemId') or value.get('item_id')
                        if item_id:
                            logger.info(f"从{key}字段中提取商品ID: {item_id}")
                            return item_id

                # 从消息内容中提取数字ID
                content = message_3.get('content', '')
                if isinstance(content, str) and content:
                    id_match = re.search(r'(\d{10,})', content)
                    if id_match:
                        logger.info(f"【{self.cookie_id}】从消息内容中提取商品ID: {id_match.group(1)}")
                        return id_match.group(1)

            # 方法3: 遍历整个消息结构查找可能的商品ID
            def find_item_id_recursive(obj, path=""):
                if isinstance(obj, dict):
                    # 直接查找itemId字段
                    for key in ['itemId', 'item_id', 'id']:
                        if key in obj and isinstance(obj[key], (str, int)):
                            value = str(obj[key])
                            if len(value) >= 10 and value.isdigit():
                                logger.info(f"从{path}.{key}中提取商品ID: {value}")
                                return value

                    # 递归查找
                    for key, value in obj.items():
                        result = find_item_id_recursive(value, f"{path}.{key}" if path else key)
                        if result:
                            return result

                elif isinstance(obj, str):
                    # 从字符串中提取可能的商品ID
                    id_match = re.search(r'(\d{10,})', obj)
                    if id_match:
                        logger.info(f"从{path}字符串中提取商品ID: {id_match.group(1)}")
                        return id_match.group(1)

                return None

            result = find_item_id_recursive(message)
            if result:
                return result

            logger.warning("所有方法都未能提取到商品ID")
            return None

        except Exception as e:
            logger.error(f"提取商品ID失败: {self._safe_str(e)}")
            return None

    def debug_message_structure(self, message, context=""):
        """调试消息结构的辅助方法"""
        try:
            logger.warning(f"[{context}] 消息结构调试:")
            logger.warning(f"  消息类型: {type(message)}")

            if isinstance(message, dict):
                for key, value in message.items():
                    logger.warning(f"  键 '{key}': {type(value)}")
                    if key in ["1", "3"] and isinstance(value, dict):
                        logger.warning(f"    详细结构 '{key}':")
                        for sub_key, sub_value in value.items():
                            logger.warning(f"      '{sub_key}': {type(sub_value)}")
            else:
                logger.warning("  消息内容不是字典，已省略原始值")

        except Exception as e:
            logger.error(f"调试消息结构时发生错误: {self._safe_str(e)}")

    async def get_default_reply(self, send_user_name: str, send_user_id: str, send_message: str, chat_id: str, item_id: str = None) -> dict:
        """获取默认回复内容，支持指定商品回复、变量替换、只回复一次功能和图片发送

        Returns:
            dict: 包含 'text' (文字回复) 和 'image_url' (图片URL，可选) 的字典
                  或 None (无回复)
                  或 "EMPTY_REPLY" (空回复标记)
        """
        try:
            from db_manager import db_manager

            # 1. 优先检查指定商品回复
            if item_id:
                item_reply = db_manager.get_item_reply(self.cookie_id, item_id)
                if item_reply and item_reply.get('reply_content'):
                    reply_content = item_reply['reply_content']
                    logger.info(f"【{self.cookie_id}】使用指定商品回复: 商品ID={item_id}")

                    # 进行变量替换
                    try:
                        formatted_reply = reply_content.format(
                            send_user_name=send_user_name,
                            send_user_id=send_user_id,
                            send_message=send_message,
                            item_id=item_id
                        )
                        logger.info(
                            f"【{self.cookie_id}】指定商品回复已生成: "
                            f"length={len(formatted_reply)}"
                        )
                        return {'text': formatted_reply, 'image_url': None}
                    except Exception as format_error:
                        logger.error(f"指定商品回复变量替换失败: {self._safe_str(format_error)}")
                        # 如果变量替换失败，返回原始内容
                        return {'text': reply_content, 'image_url': None}
                else:
                    logger.warning(f"【{self.cookie_id}】商品ID {item_id} 没有配置指定回复，使用默认回复")

            # 2. 获取当前账号的默认回复设置
            default_reply_settings = db_manager.get_default_reply(self.cookie_id)

            if not default_reply_settings or not default_reply_settings.get('enabled', False):
                logger.warning(f"账号 {self.cookie_id} 未启用默认回复")
                return None

            # 检查"只回复一次"功能
            if default_reply_settings.get('reply_once', False) and chat_id:
                # 检查是否已经回复过这个chat_id
                if db_manager.has_default_reply_record(self.cookie_id, chat_id):
                    logger.info(f"【{self.cookie_id}】chat_id {chat_id} 已使用过默认回复，跳过（只回复一次）")
                    return None

            reply_content = default_reply_settings.get('reply_content', '')
            reply_image_url = default_reply_settings.get('reply_image_url', '')

            # 如果文字和图片都为空，返回空回复标记
            if (not reply_content or reply_content.strip() == '') and (not reply_image_url or reply_image_url.strip() == ''):
                logger.info(f"账号 {self.cookie_id} 默认回复内容和图片都为空，不进行回复")
                return "EMPTY_REPLY"  # 返回特殊标记表示不回复

            # 进行变量替换
            try:
                formatted_reply = reply_content.format(
                    send_user_name=send_user_name,
                    send_user_id=send_user_id,
                    send_message=send_message
                ) if reply_content else ''

                # 如果开启了"只回复一次"功能，记录这次回复
                if default_reply_settings.get('reply_once', False) and chat_id:
                    db_manager.add_default_reply_record(self.cookie_id, chat_id)
                    logger.info(f"【{self.cookie_id}】记录默认回复: chat_id={chat_id}")

                logger.info(
                    f"【{self.cookie_id}】使用默认回复: "
                    f"text_length={len(formatted_reply)}, has_image={bool(reply_image_url)}"
                )
                return {'text': formatted_reply, 'image_url': reply_image_url if reply_image_url and reply_image_url.strip() else None}
            except Exception as format_error:
                logger.error(f"默认回复变量替换失败: {self._safe_str(format_error)}")
                # 如果变量替换失败，返回原始内容
                return {'text': reply_content, 'image_url': reply_image_url if reply_image_url and reply_image_url.strip() else None}

        except Exception as e:
            logger.error(f"获取默认回复失败: {self._safe_str(e)}")
            return None

    async def get_keyword_reply(self, send_user_name: str, send_user_id: str, send_message: str, item_id: str = None) -> str:
        """获取关键词匹配回复（支持商品ID优先匹配和图片类型）"""
        try:
            from db_manager import db_manager

            # 获取当前账号的关键词列表（包含类型信息）
            keywords = db_manager.get_keywords_with_type(self.cookie_id)

            if not keywords:
                logger.warning(f"账号 {self.cookie_id} 没有配置关键词")
                return None

            # 1. 如果有商品ID，优先匹配该商品ID对应的关键词
            if item_id:
                for keyword_data in keywords:
                    keyword = keyword_data['keyword']
                    reply = keyword_data['reply']
                    keyword_item_id = keyword_data['item_id']
                    keyword_type = keyword_data.get('type', 'text')
                    image_url = keyword_data.get('image_url')

                    if keyword_item_id == item_id and keyword.lower() in send_message.lower():
                        logger.info(f"商品ID关键词匹配成功: 商品{item_id} '{keyword}' (类型: {keyword_type})")

                        # 根据关键词类型处理
                        if keyword_type == 'image' and image_url:
                            # 图片类型关键词，发送图片
                            return await self._handle_image_keyword(keyword, image_url, send_user_name, send_user_id, send_message)
                        else:
                            # 文本类型关键词，检查回复内容是否为空
                            if not reply or (reply and reply.strip() == ''):
                                logger.info(f"商品ID关键词 '{keyword}' 回复内容为空，不进行回复")
                                return "EMPTY_REPLY"  # 返回特殊标记表示匹配到但不回复

                            # 进行变量替换
                            try:
                                formatted_reply = reply.format(
                                    send_user_name=send_user_name,
                                    send_user_id=send_user_id,
                                    send_message=send_message
                                )
                                logger.info(
                                    f"商品ID文本关键词回复已生成: length={len(formatted_reply)}"
                                )
                                return formatted_reply
                            except Exception as format_error:
                                logger.error(f"关键词回复变量替换失败: {self._safe_str(format_error)}")
                                # 如果变量替换失败，返回原始内容
                                return reply

            # 2. 如果商品ID匹配失败或没有商品ID，匹配没有商品ID的通用关键词
            for keyword_data in keywords:
                keyword = keyword_data['keyword']
                reply = keyword_data['reply']
                keyword_item_id = keyword_data['item_id']
                keyword_type = keyword_data.get('type', 'text')
                image_url = keyword_data.get('image_url')

                if not keyword_item_id and keyword.lower() in send_message.lower():
                    logger.info(f"通用关键词匹配成功: '{keyword}' (类型: {keyword_type})")

                    # 根据关键词类型处理
                    if keyword_type == 'image' and image_url:
                        # 图片类型关键词，发送图片
                        return await self._handle_image_keyword(keyword, image_url, send_user_name, send_user_id, send_message)
                    else:
                        # 文本类型关键词，检查回复内容是否为空
                        if not reply or (reply and reply.strip() == ''):
                            logger.info(f"通用关键词 '{keyword}' 回复内容为空，不进行回复")
                            return "EMPTY_REPLY"  # 返回特殊标记表示匹配到但不回复

                        # 进行变量替换
                        try:
                            formatted_reply = reply.format(
                                send_user_name=send_user_name,
                                send_user_id=send_user_id,
                                send_message=send_message
                            )
                            logger.info(
                                f"通用文本关键词回复已生成: length={len(formatted_reply)}"
                            )
                            return formatted_reply
                        except Exception as format_error:
                            logger.error(f"关键词回复变量替换失败: {self._safe_str(format_error)}")
                            # 如果变量替换失败，返回原始内容
                            return reply

            logger.debug(f"未找到匹配的关键词: message_length={len(send_message or '')}")
            return None

        except Exception as e:
            logger.error(f"获取关键词回复失败: {self._safe_str(e)}")
            return None

    async def _handle_image_keyword(self, keyword: str, image_url: str, send_user_name: str, send_user_id: str, send_message: str) -> str:
        """处理图片类型关键词"""
        try:
            # 检查图片URL类型
            if self._is_cdn_url(image_url):
                # 已经是CDN链接，直接使用
                logger.info("使用已有的CDN图片链接")
                return f"__IMAGE_SEND__{image_url}"

            elif image_url.startswith('/static/uploads/') or image_url.startswith('static/uploads/'):
                # 本地图片，需要上传到闲鱼CDN
                local_image_path = image_url.replace('/static/uploads/', 'static/uploads/')
                if os.path.exists(local_image_path):
                    logger.info(f"准备上传本地图片到闲鱼CDN: {local_image_path}")

                    # 使用图片上传器上传到闲鱼CDN
                    from utils.image_uploader import ImageUploader
                    uploader = ImageUploader(self.cookies_str)

                    async with uploader:
                        cdn_url = await uploader.upload_image(local_image_path)
                        if cdn_url:
                            logger.info("图片上传成功")
                            # 更新数据库中的图片URL为CDN URL
                            await self._update_keyword_image_url(keyword, cdn_url)
                            image_url = cdn_url
                        else:
                            logger.error(f"图片上传失败: {local_image_path}")
                            logger.error(f"❌ Cookie可能已失效！请检查配置并更新Cookie")
                            return f"抱歉，图片发送失败（Cookie可能已失效，请检查日志）"
                else:
                    logger.error(f"本地图片文件不存在: {local_image_path}")
                    return f"抱歉，图片文件不存在。"

            else:
                # 其他类型的URL（可能是外部链接），直接使用
                logger.info("使用外部图片链接")

            # 发送图片（这里返回特殊标记，在调用处处理实际发送）
            return f"__IMAGE_SEND__{image_url}"

        except Exception as e:
            logger.error(f"处理图片关键词失败: {e}")
            return f"抱歉，图片发送失败: {str(e)}"

    def _is_cdn_url(self, url: str) -> bool:
        """检查URL是否是闲鱼CDN链接"""
        if not url:
            return False

        # 闲鱼CDN域名列表
        cdn_domains = [
            'gw.alicdn.com',
            'img.alicdn.com',
            'cloud.goofish.com',
            'goofish.com',
            'taobaocdn.com',
            'tbcdn.cn',
            'aliimg.com'
        ]

        try:
            parsed = urlparse(str(url).strip())
            host = str(parsed.hostname or '').rstrip('.').lower()
        except ValueError:
            return False
        return (
            parsed.scheme.lower() == 'https'
            and any(host == domain or host.endswith(f'.{domain}') for domain in cdn_domains)
        )

    async def _get_image_size_from_url(self, image_url: str) -> tuple:
        """从URL获取图片尺寸

        Args:
            image_url: 图片URL

        Returns:
            (width, height) 元组，失败返回 (None, None)
        """
        from io import BytesIO

        try:
            logger.info(f"【{self.cookie_id}】开始读取远程图片尺寸")

            # 不接受AVIF格式（PIL默认不支持），让CDN返回WEBP/JPEG等格式
            headers = {
                'User-Agent': self.browser_user_agent,
                'Accept': 'image/jpeg,image/png,image/gif,image/webp,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Referer': 'https://www.goofish.com/',
            }

            response = await request_public_http(
                'GET',
                image_url,
                headers=headers,
                timeout_seconds=10,
                max_response_bytes=4 * 1024 * 1024,
                allowed_methods=('GET',),
            )
            content_type = str(next(
                (
                    value for key, value in response.headers.items()
                    if str(key).lower() == 'content-type'
                ),
                '',
            )).lower()
            if response.status != 200 or not content_type.startswith('image/'):
                logger.warning(f"【{self.cookie_id}】下载图片失败，HTTP状态码: {response.status}")
                return (None, None)
            from PIL import Image
            with Image.open(BytesIO(response.body)) as img:
                width, height = img.size
                if width <= 0 or height <= 0 or width * height > 100_000_000:
                    logger.warning(f"【{self.cookie_id}】远程图片尺寸超出限制")
                    return (None, None)
                logger.info(f"【{self.cookie_id}】解析图片尺寸成功: {width}x{height}")
                return (width, height)
        except Exception as e:
            code = e.code if isinstance(e, OutboundRequestError) else type(e).__name__
            logger.warning(f"【{self.cookie_id}】从URL获取图片尺寸失败: {code}")

        return (None, None)

    async def _update_keyword_image_url(self, keyword: str, new_image_url: str):
        """更新关键词的图片URL"""
        try:
            from db_manager import db_manager
            success = db_manager.update_keyword_image_url(self.cookie_id, keyword, new_image_url)
            if success:
                logger.info(f"图片URL已更新: {keyword} -> {new_image_url}")
            else:
                logger.warning(f"图片URL更新失败: {keyword}")
        except Exception as e:
            logger.error(f"更新关键词图片URL失败: {e}")

    async def _update_card_image_url(self, card_id: int, new_image_url: str):
        """更新卡券的图片URL"""
        try:
            from db_manager import db_manager
            success = db_manager.update_card_image_url(card_id, new_image_url)
            if success:
                logger.info(f"卡券图片URL已更新: 卡券ID={card_id} -> {new_image_url}")
            else:
                logger.warning(f"卡券图片URL更新失败: 卡券ID={card_id}")
        except Exception as e:
            logger.error(f"更新卡券图片URL失败: {e}")

    async def _update_default_reply_image_url(self, new_image_url: str):
        """更新默认回复的图片URL为CDN URL"""
        try:
            from db_manager import db_manager
            success = db_manager.update_default_reply_image_url(self.cookie_id, new_image_url)
            if success:
                logger.info(f"【{self.cookie_id}】默认回复图片URL已更新: {new_image_url}")
            else:
                logger.warning(f"【{self.cookie_id}】默认回复图片URL更新失败")
        except Exception as e:
            logger.error(f"【{self.cookie_id}】更新默认回复图片URL失败: {e}")

    def _ai_item_info(self, item_info_raw):
        if not item_info_raw:
            return {
                'title': '商品信息获取失败',
                'price': 0,
                'desc': '暂无商品描述',
            }
        return {
            'title': item_info_raw.get('item_title', '未知商品'),
            'price': self._parse_price(item_info_raw.get('item_price', '0')),
            'desc': item_info_raw.get('item_detail', '暂无商品描述'),
        }

    async def _resolve_ai_order_context(self, ai_reply_engine, chat_id: str,
                                        item_id: str, user_id: str = None,
                                        order_id: str = None,
                                        order_scope: str = None):
        """用引擎的归属校验解析订单；旧引擎或旧 schema 下保持原调用形状。"""
        resolver = getattr(ai_reply_engine, 'resolve_order_scope', None)
        if not callable(resolver):
            return None, None
        result = await asyncio.to_thread(
            resolver,
            chat_id=chat_id,
            cookie_id=self.cookie_id,
            item_id=item_id,
            order_id=order_id,
            order_scope=order_scope,
            user_id=user_id,
        )
        if not isinstance(result, dict):
            return None, None
        scope = str(result.get('scope') or '').strip().lower()
        resolved_order_id = str(result.get('order_id') or '').strip() or None
        if scope in {'exact', 'unique'} and resolved_order_id:
            return resolved_order_id, scope
        if scope in {'ambiguous', 'none'}:
            return None, scope
        return None, None

    async def _record_seller_human_message(self, chat_id: str, item_id: str,
                                           content: str):
        """后台记录平台已观察到的人工卖家消息，不阻塞监听与暂停逻辑。"""
        try:
            from ai_reply_engine import ai_reply_engine

            resolved_order_id, order_scope = await self._resolve_ai_order_context(
                ai_reply_engine, chat_id, item_id,
            )
            if not resolved_order_id:
                return
            await asyncio.to_thread(
                ai_reply_engine.save_conversation,
                chat_id=chat_id,
                cookie_id=self.cookie_id,
                user_id=self.myid,
                item_id=item_id,
                role='seller_human',
                content=content,
                order_id=resolved_order_id,
                order_scope=order_scope,
                source='seller_human',
                delivery_state='succeeded',
            )
        except Exception as exc:
            logger.warning(
                "【{}】记录人工回复失败: error_type={}",
                self.cookie_id,
                type(exc).__name__,
            )

    async def _mark_ai_reply_delivery(self, chat_id: str, item_id: str,
                                      content: str, delivery_state: str):
        try:
            from ai_reply_engine import ai_reply_engine

            marked = await asyncio.to_thread(
                ai_reply_engine.mark_conversation_delivery,
                chat_id=chat_id,
                cookie_id=self.cookie_id,
                item_id=item_id,
                delivery_state=delivery_state,
                content=content,
            )
            if not marked:
                logger.warning(f"【{self.cookie_id}】未找到对应AI草稿记录")
        except Exception as exc:
            logger.warning(
                "【{}】记录AI回复发送结果失败: error_type={}",
                self.cookie_id,
                type(exc).__name__,
            )

    def _schedule_ai_shadow_reply(self, send_user_id: str, send_message: str,
                                  item_id: str, chat_id: str, image_refs=None,
                                  order_id: str = None, order_scope: str = None,
                                  sent_reply: str = None, reply_source: str = ''):
        """在正式回复结束后启动旁路候选，永不参与发送。"""
        if not AI_REPLY_SHADOW_ENABLED:
            return

        async def run_shadow():
            resolved_order_id = None
            resolved_scope = None
            try:
                from ai_reply_engine import ai_reply_engine

                resolved_order_id, resolved_scope = await self._resolve_ai_order_context(
                    ai_reply_engine, chat_id, item_id, send_user_id,
                    order_id=order_id, order_scope=order_scope,
                )
                if resolved_order_id:
                    await asyncio.to_thread(
                        ai_reply_engine.save_conversation,
                        chat_id=chat_id,
                        cookie_id=self.cookie_id,
                        user_id=send_user_id,
                        item_id=item_id,
                        role='buyer',
                        content=send_message,
                        order_id=resolved_order_id,
                        order_scope=resolved_scope,
                        source='buyer',
                        delivery_state='received',
                    )
                    if sent_reply:
                        source = {
                            'AI': 'assistant_generated',
                            '关键词': 'keyword',
                            '默认': 'system',
                        }.get(reply_source, 'system')
                        await asyncio.to_thread(
                            ai_reply_engine.save_conversation,
                            chat_id=chat_id,
                            cookie_id=self.cookie_id,
                            user_id=self.myid,
                            item_id=item_id,
                            role='assistant',
                            content=sent_reply,
                            order_id=resolved_order_id,
                            order_scope=resolved_scope,
                            source=source,
                            delivery_state='ambiguous',
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "【{}】记录AI订单作用域消息失败: error_type={}",
                    self.cookie_id,
                    type(exc).__name__,
                )
                return

            semaphore = _get_ai_reply_shadow_semaphore()
            self._ai_reply_shadow_semaphore = semaphore
            if semaphore.locked():
                logger.info(f"【{self.cookie_id}】AI Shadow 正忙，跳过本次候选")
                return

            await semaphore.acquire()
            defer_release = False
            model_task = None
            try:
                item_info = self._ai_item_info(
                    await asyncio.to_thread(
                        db_manager.get_item_info, self.cookie_id, item_id,
                    )
                )
                model_task = self._create_tracked_task(
                    ai_reply_engine.generate_shadow_reply_async(
                        message=send_message,
                        item_info=item_info,
                        chat_id=chat_id,
                        cookie_id=self.cookie_id,
                        user_id=send_user_id,
                        item_id=item_id,
                        image_refs=image_refs,
                        order_id=resolved_order_id,
                        order_scope=resolved_scope,
                    )
                )
                try:
                    await asyncio.wait_for(
                        asyncio.shield(model_task),
                        timeout=AI_REPLY_SHADOW_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.info(f"【{self.cookie_id}】AI Shadow 候选超时")
                    defer_release = True
                    model_task.add_done_callback(lambda _task: semaphore.release())
            except asyncio.CancelledError:
                if model_task is not None and not model_task.done():
                    defer_release = True
                    model_task.add_done_callback(lambda _task: semaphore.release())
                raise
            except Exception as exc:
                logger.warning(
                    "【{}】AI Shadow 候选失败: error_type={}",
                    self.cookie_id,
                    type(exc).__name__,
                )
            finally:
                if not defer_release:
                    semaphore.release()

        self._create_tracked_task(run_shadow())

    async def get_ai_reply(self, send_user_name: str, send_user_id: str, send_message: str, item_id: str, chat_id: str,
                           image_refs=None, order_id: str = None, order_scope: str = None):
        """获取AI回复"""
        self.last_ai_attempt_at = time.time()
        self.last_ai_result = "started"
        try:
            from ai_reply_engine import ai_reply_engine

            # 检查是否启用AI回复
            if not ai_reply_engine.is_ai_enabled(self.cookie_id):
                self.last_ai_result = "disabled"
                logger.warning(f"账号 {self.cookie_id} 未启用AI回复")
                return None

            # 从数据库获取商品信息
            from db_manager import db_manager
            item_info_raw = db_manager.get_item_info(self.cookie_id, item_id)

            if not item_info_raw:
                logger.warning(f"数据库中无商品信息: {item_id}")
            item_info = self._ai_item_info(item_info_raw)

            # 生成AI回复；文本消息继续使用原请求形状，图片消息才追加多模态字段。
            ai_kwargs = {
                "message": send_message,
                "item_info": item_info,
                "chat_id": chat_id,
                "cookie_id": self.cookie_id,
                "user_id": send_user_id,
                "item_id": item_id,
                "skip_wait": True,
            }
            if image_refs:
                ai_kwargs["image_refs"] = image_refs
            reply = await ai_reply_engine.generate_reply_async(**ai_kwargs)

            if reply:
                self.last_ai_result = "generated"
                logger.info(
                    f"【{self.cookie_id}】AI回复生成成功: length={len(reply)}"
                )
                return reply
            else:
                self.last_ai_result = "provider_empty"
                logger.warning(f"AI回复生成失败")
                return None

        except Exception as e:
            self.last_ai_result = f"provider_error:{type(e).__name__}"
            logger.error(f"获取AI回复失败: {self._safe_str(e)}")
            return None

    def _parse_price(self, price_str: str) -> float:
        """解析价格字符串为数字"""
        try:
            if not price_str:
                return 0.0
            price_clean = re.sub(r'[^\d.]', '', str(price_str))
            return float(price_clean) if price_clean else 0.0
        except Exception:
            return 0.0

    async def send_notification(self, send_user_name: str, send_user_id: str, send_message: str, item_id: str = None, chat_id: str = None):
        """发送消息通知"""
        try:
            from db_manager import db_manager
            import aiohttp
            import hashlib

            # 过滤系统默认消息，不发送通知
            system_messages = [
                '发来一条消息',
                '发来一条新消息'
            ]

            if send_message in system_messages:
                logger.info("系统占位消息不发送通知")
                return

            # 生成通知的唯一标识（基于消息内容、chat_id、send_user_id）
            # 用于防重复发送
            notification_key = f"{chat_id or 'unknown'}_{send_user_id}_{send_message}"
            notification_hash = hashlib.md5(notification_key.encode('utf-8')).hexdigest()

            # 使用异步锁保护防重复检查，确保并发安全
            async with self.notification_lock:
                # 检查是否在冷却时间内已发送过相同的通知
                current_time = time.time()
                if notification_hash in self.last_notification_time:
                    time_since_last = current_time - self.last_notification_time[notification_hash]
                    if time_since_last < self.notification_cooldown:
                        remaining_seconds = int(self.notification_cooldown - time_since_last)
                        logger.warning(
                            f"【{self.cookie_id}】通知在冷却期内，剩余 {remaining_seconds} 秒"
                        )
                        return

                # 更新通知发送时间
                self.last_notification_time[notification_hash] = current_time

                # 清理过期的通知记录（超过1小时的记录）
                expired_keys = [
                    key for key, timestamp in self.last_notification_time.items()
                    if current_time - timestamp > 3600  # 1小时
                ]
                for key in expired_keys:
                    del self.last_notification_time[key]

            logger.info(f"【{self.cookie_id}】开始发送消息通知")

            # 获取当前账号的通知配置
            notifications = db_manager.get_account_notifications(self.cookie_id)

            if not notifications:
                logger.warning(f"📱 账号 {self.cookie_id} 未配置消息通知，跳过通知发送")
                return

            logger.info(f"📱 找到 {len(notifications)} 个通知渠道配置")

            # 构建通知消息
            notification_msg = f"🚨 接收消息通知\n\n" \
                             f"账号: {self.cookie_id}\n" \
                             f"买家: {send_user_name} (ID: {send_user_id})\n" \
                             f"商品ID: {item_id or '未知'}\n" \
                             f"聊天ID: {chat_id or '未知'}\n" \
                             f"消息内容: {send_message}\n" \
                             f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"

            # 发送通知到各个渠道
            for i, notification in enumerate(notifications, 1):
                logger.info(f"📱 处理第 {i} 个通知渠道: {notification.get('channel_name', 'Unknown')}")

                if not notification.get('enabled', True):
                    logger.warning(f"📱 通知渠道 {notification.get('channel_name')} 已禁用，跳过")
                    continue

                channel_type = notification.get('channel_type')
                channel_config = notification.get('channel_config')

                logger.info(f"📱 渠道类型: {channel_type}")

                try:
                    # 解析配置数据
                    config_data = self._parse_notification_config(channel_config)
                    match channel_type:
                        case 'ding_talk' | 'dingtalk':
                            logger.info(f"📱 开始发送钉钉通知...")
                            await self._send_dingtalk_notification(config_data, notification_msg)
                        case 'feishu' | 'lark':
                            logger.info(f"📱 开始发送飞书通知...")
                            await self._send_feishu_notification(config_data, notification_msg)
                        case 'bark':
                            logger.info(f"📱 开始发送Bark通知...")
                            await self._send_bark_notification(config_data, notification_msg)
                        case 'email':
                            logger.info(f"📱 开始发送邮件通知...")
                            await self._send_email_notification(config_data, notification_msg)
                        case 'webhook':
                            logger.info(f"📱 开始发送Webhook通知...")
                            await self._send_webhook_notification(config_data, notification_msg)
                        case 'wechat':
                            logger.info(f"📱 开始发送微信通知...")
                            await self._send_wechat_notification(config_data, notification_msg)
                        case 'telegram':
                            logger.info(f"📱 开始发送Telegram通知...")
                            await self._send_telegram_notification(config_data, notification_msg)
                        case _:
                            logger.warning(f"📱 不支持的通知渠道类型: {channel_type}")

                except Exception as notify_error:
                    logger.error(
                        "📱 发送通知失败 "
                        f"channel_type={channel_type}, error={type(notify_error).__name__}"
                    )

        except Exception as e:
            logger.error(f"📱 处理消息通知失败: {type(e).__name__}")

    def _parse_notification_config(self, config: str) -> dict:
        """解析通知配置数据"""
        try:
            import json
            # 尝试解析JSON格式的配置
            return json.loads(config)
        except (json.JSONDecodeError, TypeError):
            # 兼容旧格式（直接字符串）
            return {"config": config}

    async def _send_dingtalk_notification(self, config_data: dict, message: str):
        """发送钉钉通知"""
        try:
            import hmac
            import hashlib
            import base64

            webhook_url = config_data.get('webhook_url') or config_data.get('config', '')
            secret = config_data.get('secret', '')
            webhook_url = webhook_url.strip() if webhook_url else ''
            if not webhook_url:
                logger.warning("钉钉通知配置为空")
                return False

            request_params = None
            if secret:
                timestamp = str(round(time.time() * 1000))
                string_to_sign = f'{timestamp}\n{secret}'
                hmac_code = hmac.new(
                    secret.encode('utf-8'),
                    string_to_sign.encode('utf-8'),
                    digestmod=hashlib.sha256,
                ).digest()
                sign = base64.b64encode(hmac_code).decode('utf-8')
                request_params = {'timestamp': timestamp, 'sign': sign}

            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": "闲鱼自动回复通知",
                    "text": message
                }
            }
            response = await request_public_http(
                'POST',
                webhook_url,
                params=request_params,
                json_body=data,
                timeout_seconds=10,
                require_https=True,
            )
            if not 200 <= response.status < 300:
                logger.warning(f"钉钉通知发送失败: HTTP {response.status}")
                return False
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict) and payload.get('errcode') not in (None, 0, '0'):
                logger.warning("钉钉通知发送失败: platform_error")
                return False
            logger.info("钉钉通知发送成功")
            return True

        except Exception as e:
            code = e.code if isinstance(e, OutboundRequestError) else type(e).__name__
            logger.error(f"发送钉钉通知异常: {code}")
            return False

    async def _send_feishu_notification(self, config_data: dict, message: str):
        """发送飞书通知"""
        try:
            import hmac
            import hashlib
            import base64

            logger.info("飞书通知开始发送")

            # 解析配置
            webhook_url = config_data.get('webhook_url', '')
            secret = config_data.get('secret', '')

            logger.info(f"📱 飞书通知 - 是否有签名密钥: {'是' if secret else '否'}")

            if not webhook_url:
                logger.warning("📱 飞书通知 - Webhook URL配置为空，无法发送通知")
                return False

            # 如果有加签密钥，生成签名
            timestamp = str(int(time.time()))
            sign = ""

            if secret:
                string_to_sign = f'{timestamp}\n{secret}'
                hmac_code = hmac.new(
                    string_to_sign.encode('utf-8'),
                    ''.encode('utf-8'),
                    digestmod=hashlib.sha256
                ).digest()
                sign = base64.b64encode(hmac_code).decode('utf-8')
                logger.info(f"📱 飞书通知 - 已生成签名")

            # 构建请求数据
            data = {
                "msg_type": "text",
                "content": {
                    "text": message
                },
                "timestamp": timestamp
            }

            # 如果有签名，添加到请求数据中
            if sign:
                data["sign"] = sign

            logger.info(f"📱 飞书通知 - 请求数据构建完成")

            response = await request_public_http(
                'POST',
                webhook_url,
                json_body=data,
                timeout_seconds=10,
                require_https=True,
            )
            logger.info(f"📱 飞书通知 - 响应状态: {response.status}")
            if not 200 <= response.status < 300:
                logger.warning(f"📱 飞书通知发送失败: HTTP {response.status}")
                return False
            try:
                response_json = response.json()
            except (ValueError, json.JSONDecodeError):
                response_json = {}
            if isinstance(response_json, dict) and response_json.get('code') not in (None, 0, '0'):
                logger.warning("📱 飞书通知发送失败: platform_error")
                return False
            logger.info("📱 飞书通知发送成功")
            return True

        except Exception as e:
            code = e.code if isinstance(e, OutboundRequestError) else type(e).__name__
            logger.error(f"📱 发送飞书通知异常: {code}")
            return False

    async def _send_bark_notification(self, config_data: dict, message: str):
        """发送Bark通知"""
        try:
            logger.info("Bark通知开始发送")

            # 解析配置
            server_url = config_data.get('server_url', 'https://api.day.app').rstrip('/')
            device_key = config_data.get('device_key', '')
            title = config_data.get('title', '闲鱼自动回复通知')
            sound = config_data.get('sound', 'default')
            icon = config_data.get('icon', '')
            group = config_data.get('group', 'xianyu')
            url = config_data.get('url', '')

            logger.info(f"📱 Bark通知 - 设备密钥已配置: {bool(device_key)}")

            if not device_key:
                logger.warning("📱 Bark通知 - 设备密钥配置为空，无法发送通知")
                return False

            # 构建请求URL和数据
            # Bark支持两种方式：URL路径方式和POST JSON方式
            # 这里使用POST JSON方式，更灵活且支持更多参数

            api_url = f"{server_url}/push"

            # 构建请求数据
            data = {
                "device_key": device_key,
                "title": title,
                "body": message,
                "sound": sound,
                "group": group
            }

            # 可选参数
            if icon:
                data["icon"] = icon
            if url:
                data["url"] = url

            logger.info(f"📱 Bark通知 - 请求数据构建完成")

            response = await request_public_http(
                'POST',
                api_url,
                json_body=data,
                timeout_seconds=10,
                require_https=True,
            )
            logger.info(f"📱 Bark通知 - 响应状态: {response.status}")
            if not 200 <= response.status < 300:
                logger.warning(f"📱 Bark通知发送失败: HTTP {response.status}")
                return False
            try:
                response_json = response.json()
            except (ValueError, json.JSONDecodeError):
                response_json = None
            if isinstance(response_json, dict) and response_json.get('code') not in (None, 200, '200'):
                logger.warning("📱 Bark通知发送失败: platform_error")
                return False
            if response_json is None and not any(
                marker in response.text.lower() for marker in ('success', 'ok')
            ):
                logger.warning("📱 Bark通知响应格式异常")
                return False
            logger.info("📱 Bark通知发送成功")
            return True

        except Exception as e:
            code = e.code if isinstance(e, OutboundRequestError) else type(e).__name__
            logger.error(f"📱 发送Bark通知异常: {code}")
            return False

    async def _send_email_notification(self, config_data: dict, message: str, attachment_path: str = None):
        """发送邮件通知（支持附件）

        Args:
            config_data: 邮件配置
            message: 邮件正文
            attachment_path: 附件文件路径（可选）
        """
        server = None
        try:
            import smtplib
            import ssl
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            from email.mime.image import MIMEImage

            # 解析配置
            smtp_server = str(config_data.get('smtp_server') or '').strip()
            smtp_port = int(config_data.get('smtp_port', 587))
            email_user = str(config_data.get('email_user') or '').strip()
            email_password = config_data.get('email_password', '')
            recipient_email = str(config_data.get('recipient_email') or '').strip()
            smtp_use_ssl = smtp_port == 465
            smtp_use_tls_raw = config_data.get('smtp_use_tls', not smtp_use_ssl)
            smtp_use_tls = (
                smtp_use_tls_raw
                if isinstance(smtp_use_tls_raw, bool)
                else str(smtp_use_tls_raw).strip().lower() in {'1', 'true', 'yes', 'on'}
            ) and not smtp_use_ssl

            if not all([smtp_server, email_user, email_password, recipient_email]):
                logger.warning("邮件通知配置不完整")
                return False
            if not 1 <= smtp_port <= 65535:
                logger.warning("邮件通知SMTP端口无效")
                return False
            if any('\r' in value or '\n' in value for value in (email_user, recipient_email)):
                logger.warning("邮件通知邮箱格式无效")
                return False
            if not smtp_use_ssl and not smtp_use_tls:
                logger.warning("邮件通知必须启用SSL或STARTTLS")
                return False

            # 创建邮件
            msg = MIMEMultipart('mixed')
            msg['From'] = email_user
            msg['To'] = recipient_email
            msg['Subject'] = summarize_notification_email_subject(message)

            # 正文用 alternative 双版本：纯文本兜底 + HTML 排版（标题/信息表格/段落）
            body = MIMEMultipart('alternative')
            body.attach(MIMEText(message, 'plain', 'utf-8'))
            body.attach(
                MIMEText(render_notification_email_html(message), 'html', 'utf-8')
            )
            msg.attach(body)

            # 添加附件（如果有）
            if attachment_path and os.path.exists(attachment_path):
                try:
                    with open(attachment_path, 'rb') as f:
                        img_data = f.read()

                    # 根据文件扩展名判断MIME类型
                    filename = os.path.basename(attachment_path)
                    if attachment_path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        img = MIMEImage(img_data)
                        img.add_header('Content-Disposition', 'attachment', filename=filename)
                        msg.attach(img)
                        logger.info(f"已添加图片附件: {filename}")
                    else:
                        from email.mime.application import MIMEApplication
                        attach = MIMEApplication(img_data)
                        attach.add_header('Content-Disposition', 'attachment', filename=filename)
                        msg.attach(attach)
                        logger.info(f"已添加附件: {filename}")
                except Exception as attach_error:
                    logger.error(f"添加邮件附件失败: {type(attach_error).__name__}")

            def send_message_via_public_smtp() -> None:
                nonlocal server
                context = ssl.create_default_context()
                server = open_public_smtp(
                    smtp_server,
                    smtp_port,
                    use_ssl=smtp_use_ssl,
                    timeout_seconds=20,
                    tls_context=context,
                )
                server.ehlo()
                if smtp_use_tls:
                    server.starttls(context=context)
                    server.ehlo()
                try:
                    server.login(email_user, email_password)
                except smtplib.SMTPAuthenticationError as auth_error:
                    error_code = auth_error.smtp_code if hasattr(auth_error, 'smtp_code') else None
                    logger.error(f"邮件SMTP认证失败 (错误码: {error_code})")
                    raise
                server.send_message(msg)

            await asyncio.to_thread(send_message_via_public_smtp)
            logger.info("邮件通知发送成功")
            return True

        except smtplib.SMTPAuthenticationError:
            return False
        except OutboundRequestError as exc:
            logger.error(f"邮件通知出站连接失败: {exc.code}")
            return False
        except smtplib.SMTPException as smtp_error:
            logger.error(f"SMTP协议错误: {type(smtp_error).__name__}")
            return False
        except Exception as e:
            logger.error(f"发送邮件通知异常: {type(e).__name__}")
            return False
        finally:
            if server:
                def close_server() -> None:
                    try:
                        server.quit()
                    except Exception:
                        try:
                            server.close()
                        except Exception:
                            pass

                await asyncio.to_thread(close_server)

    async def _send_webhook_notification(self, config_data: dict, message: str):
        """发送Webhook通知"""
        try:
            webhook_url = config_data.get('webhook_url', '')
            http_method = config_data.get('http_method', 'POST').upper()
            headers_str = config_data.get('headers', '{}')

            if not webhook_url:
                logger.warning("Webhook通知配置为空")
                return False

            # 解析自定义请求头
            try:
                custom_headers = json.loads(headers_str) if headers_str else {}
            except json.JSONDecodeError:
                custom_headers = {}

            # 设置默认请求头
            headers = {'Content-Type': 'application/json'}
            headers.update(custom_headers)

            # 构建请求数据
            data = {
                'message': message,
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'source': 'xianyu-auto-reply'
            }

            response = await request_public_http(
                http_method,
                webhook_url,
                headers=headers,
                json_body=data,
                timeout_seconds=10,
                allowed_methods=('POST', 'PUT'),
                require_https=True,
            )
            if 200 <= response.status < 300:
                logger.info("Webhook通知发送成功")
                return True
            logger.warning(f"Webhook通知发送失败: HTTP {response.status}")
            return False

        except Exception as e:
            code = e.code if isinstance(e, OutboundRequestError) else type(e).__name__
            logger.error(f"发送Webhook通知异常: {code}")
            return False

    async def _send_wechat_notification(self, config_data: dict, message: str):
        """发送微信通知"""
        try:
            webhook_url = config_data.get('webhook_url', '')

            if not webhook_url:
                logger.warning("微信通知配置为空")
                return False

            data = {
                "msgtype": "text",
                "text": {
                    "content": message
                }
            }

            response = await request_public_http(
                'POST',
                webhook_url,
                json_body=data,
                timeout_seconds=10,
                require_https=True,
            )
            if not 200 <= response.status < 300:
                logger.warning(f"微信通知发送失败: HTTP {response.status}")
                return False
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict) and payload.get('errcode') not in (None, 0, '0'):
                logger.warning("微信通知发送失败: platform_error")
                return False
            logger.info("微信通知发送成功")
            return True

        except Exception as e:
            code = e.code if isinstance(e, OutboundRequestError) else type(e).__name__
            logger.error(f"发送微信通知异常: {code}")
            return False

    async def _send_telegram_notification(self, config_data: dict, message: str):
        """发送Telegram通知"""
        try:
            bot_token = config_data.get('bot_token', '')
            chat_id = config_data.get('chat_id', '')
            api_base = str(
                config_data.get('api_base_url') or 'https://api.telegram.org'
            ).rstrip('/')

            if not all([bot_token, chat_id]):
                logger.warning("Telegram通知配置不完整")
                return False

            api_url = f"{api_base}/bot{quote(str(bot_token), safe=':')}/sendMessage"

            data = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }

            response = await request_public_http(
                'POST',
                api_url,
                json_body=data,
                timeout_seconds=10,
                require_https=True,
            )
            if not 200 <= response.status < 300:
                logger.warning(f"Telegram通知发送失败: HTTP {response.status}")
                return False
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict) and payload.get('ok') is False:
                logger.warning("Telegram通知发送失败: platform_error")
                return False
            logger.info("Telegram通知发送成功")
            return True

        except Exception as e:
            code = e.code if isinstance(e, OutboundRequestError) else type(e).__name__
            logger.error(f"发送Telegram通知异常: {code}")
            return False

    async def send_token_refresh_notification(self, error_message: str, notification_type: str = "token_refresh", chat_id: str = None, attachment_path: str = None, verification_url: str = None):
        """发送Token刷新异常通知（带防重复机制，支持附件）

        Args:
            error_message: 错误消息
            notification_type: 通知类型
            chat_id: 聊天ID（可选）
            attachment_path: 附件路径（可选，用于发送截图）
        """
        try:
            # 检查是否是正常的令牌过期，这种情况不需要发送通知
            if self._is_normal_token_expiry(error_message):
                logger.warning(f"检测到正常的令牌过期，跳过通知: {error_message}")
                return

            # 检查是否在冷却期内
            current_time = time.time()
            last_time = self.last_notification_time.get(notification_type, 0)

            # 为Token刷新异常通知使用特殊的3小时冷却时间
            # 基于错误消息内容判断是否为Token相关异常
            if self._is_token_related_error(error_message):
                cooldown_time = self.token_refresh_notification_cooldown
                cooldown_desc = "3小时"
            else:
                cooldown_time = self.notification_cooldown
                cooldown_desc = f"{self.notification_cooldown // 60}分钟"

            if current_time - last_time < cooldown_time:
                remaining_time = cooldown_time - (current_time - last_time)
                remaining_hours = int(remaining_time // 3600)
                remaining_minutes = int((remaining_time % 3600) // 60)
                remaining_seconds = int(remaining_time % 60)

                if remaining_hours > 0:
                    time_desc = f"{remaining_hours}小时{remaining_minutes}分钟"
                elif remaining_minutes > 0:
                    time_desc = f"{remaining_minutes}分钟{remaining_seconds}秒"
                else:
                    time_desc = f"{remaining_seconds}秒"

                logger.warning(f"Token刷新通知在冷却期内，跳过发送: {notification_type} (还需等待 {time_desc})")
                return

            from db_manager import db_manager

            # 获取当前账号的通知配置
            notifications = db_manager.get_account_notifications(self.cookie_id)

            if not notifications:
                logger.warning("未配置消息通知，跳过Token刷新通知")
                return

            # 构造通知消息
            # 判断异常信息中是否包含"滑块验证成功"
            if "滑块验证成功" in error_message:
                notification_msg = f"{error_message}\n\n" \
                                  f"账号: {self.cookie_id}\n" \
                                  f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            elif verification_url:
                notification_msg = f"{error_message}\n\n" \
                                  f"账号: {self.cookie_id}\n" \
                                  f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n" \
                                  f"请在账号管理页完成身份验证，验证链接不通过通知传输。\n"
            else:
                notification_msg = f"Token刷新异常\n\n" \
                                  f"账号ID: {self.cookie_id}\n" \
                                  f"异常时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n" \
                                  f"异常信息: {error_message}\n\n" \
                                  f"请检查账号Cookie是否过期，如有需要请及时更新Cookie配置。\n"

            logger.info(f"准备发送Token刷新异常通知: {self.cookie_id}")

            # 发送通知到各个渠道
            notification_sent = False
            for notification in notifications:
                if not notification.get('enabled', True):
                    continue

                channel_type = notification.get('channel_type')
                channel_config = notification.get('channel_config')

                try:
                    # 解析配置数据
                    config_data = self._parse_notification_config(channel_config)

                    match channel_type:
                        case 'ding_talk' | 'dingtalk':
                            await self._send_dingtalk_notification(config_data, notification_msg)
                            notification_sent = True
                        case 'feishu' | 'lark':
                            await self._send_feishu_notification(config_data, notification_msg)
                            notification_sent = True
                        case 'bark':
                            await self._send_bark_notification(config_data, notification_msg)
                            notification_sent = True
                        case 'email':
                            # 邮件支持附件
                            await self._send_email_notification(config_data, notification_msg, attachment_path)
                            notification_sent = True
                        case 'webhook':
                            await self._send_webhook_notification(config_data, notification_msg)
                            notification_sent = True
                        case 'wechat':
                            await self._send_wechat_notification(config_data, notification_msg)
                            notification_sent = True
                        case 'telegram':
                            await self._send_telegram_notification(config_data, notification_msg)
                            notification_sent = True
                        case _:
                            logger.warning(f"不支持的通知渠道类型: {channel_type}")

                except Exception as notify_error:
                    logger.error(f"发送Token刷新通知失败 ({notification.get('channel_name', 'Unknown')}): {self._safe_str(notify_error)}")

            # 如果成功发送了通知，更新最后发送时间
            if notification_sent:
                self.last_notification_time[notification_type] = current_time

                # 根据错误消息内容使用不同的冷却时间
                if self._is_token_related_error(error_message):
                    next_send_time = current_time + self.token_refresh_notification_cooldown
                    cooldown_desc = "3小时"
                else:
                    next_send_time = current_time + self.notification_cooldown
                    cooldown_desc = f"{self.notification_cooldown // 60}分钟"

                next_send_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(next_send_time))
                logger.info(f"Token刷新通知已发送，下次可发送时间: {next_send_time_str} (冷却时间: {cooldown_desc})")

        except Exception as e:
            logger.error(f"处理Token刷新通知失败: {self._safe_str(e)}")

    def _is_normal_token_expiry(self, error_message: str) -> bool:
        """检查是否是正常的令牌过期或其他不需要通知的情况"""
        # 不需要发送通知的关键词
        no_notification_keywords = [
            # 正常的令牌过期
            'FAIL_SYS_TOKEN_EXOIRED::令牌过期',
            'FAIL_SYS_TOKEN_EXPIRED::令牌过期',
            'FAIL_SYS_TOKEN_EXOIRED',
            'FAIL_SYS_TOKEN_EXPIRED',
            '令牌过期',
            # Session过期（正常情况）
            'FAIL_SYS_SESSION_EXPIRED::Session过期',
            'FAIL_SYS_SESSION_EXPIRED',
            'Session过期',
            # Token定时刷新失败（会自动重试）
            'Token定时刷新失败，将自动重试',
            'Token定时刷新失败'
        ]

        # 检查错误消息是否包含不需要通知的关键词
        for keyword in no_notification_keywords:
            if keyword in error_message:
                return True

        return False

    def _is_token_related_error(self, error_message: str) -> bool:
        """检查是否是Token相关的错误，需要使用3小时冷却时间"""
        # Token相关错误的关键词
        token_error_keywords = [
            # Token刷新失败相关
            'Token刷新失败',
            'Token刷新异常',
            'token刷新失败',
            'token刷新异常',
            'TOKEN刷新失败',
            'TOKEN刷新异常',
            # 具体的Token错误信息
            'FAIL_SYS_USER_VALIDATE',
            'RGV587_ERROR',
            '哎哟喂,被挤爆啦',
            '请稍后重试',
            'punish?x5secdata',
            'captcha',
            # Token获取失败
            '无法获取有效token',
            '无法获取有效Token',
            'Token获取失败',
            'token获取失败',
            'TOKEN获取失败',
            # Token定时刷新失败
            'Token定时刷新失败',
            'token定时刷新失败',
            'TOKEN定时刷新失败',
            # 初始化Token失败
            '初始化时无法获取有效Token',
            '初始化时无法获取有效token',
            # 其他Token相关错误
            'accessToken',
            'access_token',
            '_m_h5_tk',
            'mtop.taobao.idlemessage.pc.login.token'
        ]

        # 检查错误消息是否包含Token相关的关键词
        error_message_lower = error_message.lower()
        for keyword in token_error_keywords:
            if keyword.lower() in error_message_lower:
                return True

        return False

    async def send_delivery_failure_notification(self, send_user_name: str, send_user_id: str, item_id: str, error_message: str, chat_id: str = None):
        """发送自动发货失败通知"""
        try:
            from db_manager import db_manager

            # 获取当前账号的通知配置
            notifications = db_manager.get_account_notifications(self.cookie_id)

            if not notifications:
                logger.warning("未配置消息通知，跳过自动发货通知")
                return

            # 构造通知消息
            notification_message = f"🚨 自动发货通知\n\n" \
                                 f"账号: {self.cookie_id}\n" \
                                 f"买家: {send_user_name} (ID: {send_user_id})\n" \
                                 f"商品ID: {item_id}\n" \
                                 f"聊天ID: {chat_id or '未知'}\n" \
                                 f"结果: {error_message}\n" \
                                 f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n" \
                                 f"请及时处理！"

            # 发送通知到所有已启用的通知渠道
            for notification in notifications:
                if notification.get('enabled', False):
                    channel_type = notification.get('channel_type', 'qq')
                    channel_config = notification.get('channel_config', '')

                    try:
                        # 解析配置数据
                        config_data = self._parse_notification_config(channel_config)

                        match channel_type:
                            case 'ding_talk' | 'dingtalk':
                                await self._send_dingtalk_notification(config_data, notification_message)
                                logger.info(f"已发送自动发货通知到钉钉")
                            case 'email':
                                await self._send_email_notification(config_data, notification_message)
                                logger.info(f"已发送自动发货通知到邮箱")
                            case 'webhook':
                                await self._send_webhook_notification(config_data, notification_message)
                                logger.info(f"已发送自动发货通知到Webhook")
                            case 'wechat':
                                await self._send_wechat_notification(config_data, notification_message)
                                logger.info(f"已发送自动发货通知到微信")
                            case 'telegram':
                                await self._send_telegram_notification(config_data, notification_message)
                                logger.info(f"已发送自动发货通知到Telegram")
                            case 'bark':
                                await self._send_bark_notification(config_data, notification_message)
                                logger.info(f"已发送自动发货通知到Bark")
                            case 'feishu' | 'lark':
                                await self._send_feishu_notification(config_data, notification_message)
                                logger.info(f"已发送自动发货通知到飞书")
                            case _:
                                logger.warning(f"不支持的通知渠道类型: {channel_type}")

                    except Exception as notify_error:
                        logger.error(f"发送自动发货通知失败: {self._safe_str(notify_error)}")

        except Exception as e:
            logger.error(f"发送自动发货通知异常: {self._safe_str(e)}")

    async def auto_confirm(self, order_id, item_id=None, retry_count=0):
        """自动确认发货 - 使用加密模块，不包含延时处理（延时已在_auto_delivery中处理）"""
        try:
            logger.warning(f"【{self.cookie_id}】开始确认发货，订单ID: {order_id}")

            # 导入解密后的确认发货模块
            from secure_confirm_decrypted import SecureConfirm

            # 创建确认实例，传入主界面类实例
            secure_confirm = SecureConfirm(self.session, self.cookies_str, self.cookie_id, self)

            # 传递必要的属性
            secure_confirm.current_token = self.current_token
            secure_confirm.last_token_refresh_time = self.last_token_refresh_time
            secure_confirm.token_refresh_interval = self.token_refresh_interval

            # 调用确认方法，传入item_id用于token刷新
            result = await secure_confirm.auto_confirm(order_id, item_id, retry_count)

            # 同步更新后的cookies和token
            if secure_confirm.cookies_str != self.cookies_str:
                self.cookies_str = secure_confirm.cookies_str
                self.cookies = secure_confirm.cookies
                logger.warning(f"【{self.cookie_id}】已同步确认发货模块更新的cookies")

            if secure_confirm.current_token != self.current_token:
                self.current_token = secure_confirm.current_token
                self.last_token_refresh_time = secure_confirm.last_token_refresh_time
                logger.warning(f"【{self.cookie_id}】已同步确认发货模块更新的token")

            return result

        except Exception as e:
            logger.error(f"【{self.cookie_id}】加密确认模块调用失败: {self._safe_str(e)}")
            return {"error": f"加密确认模块调用失败: {self._safe_str(e)}", "order_id": order_id}

    async def auto_freeshipping(self, order_id, item_id, buyer_id, retry_count=0):
        """自动免拼发货 - 使用解密模块"""
        try:
            logger.warning(f"【{self.cookie_id}】开始免拼发货，订单ID: {order_id}")

            # 导入解密后的免拼发货模块
            from secure_freeshipping_decrypted import SecureFreeshipping

            # 创建免拼发货实例
            secure_freeshipping = SecureFreeshipping(self.session, self.cookies_str, self.cookie_id)

            # 传递必要的属性
            secure_freeshipping.current_token = self.current_token
            secure_freeshipping.last_token_refresh_time = self.last_token_refresh_time
            secure_freeshipping.token_refresh_interval = self.token_refresh_interval

            # 调用免拼发货方法
            return await secure_freeshipping.auto_freeshipping(order_id, item_id, buyer_id, retry_count)

        except Exception as e:
            logger.error(f"【{self.cookie_id}】免拼发货模块调用失败: {self._safe_str(e)}")
            return {"error": f"免拼发货模块调用失败: {self._safe_str(e)}", "order_id": order_id}

    @staticmethod
    def _match_rules_with_site_fallback(database, rule_owner_user_id, matcher, log_label):
        """账号归属用户无匹配规则时回退主站（admin）共享规则。

        代理零配置开箱即用：命中主站规则即消耗主站卡密库存（用户拍板，
        见 2026-08-29 会话）。代理自有规则永远优先；admin 自己的账号
        不重复查询。matcher 是按 user_id 查规则的闭包，两级查询共用。
        """
        rules = matcher(rule_owner_user_id)
        if rules:
            return rules
        site_owner_id = database.get_site_admin_user_id()
        if site_owner_id is None or int(site_owner_id) == int(rule_owner_user_id):
            return rules
        site_rules = matcher(site_owner_id)
        if site_rules:
            logger.info(f"账号内无{log_label}发货规则，回退主站共享规则（{len(site_rules)}个）")
        return site_rules

    async def _auto_delivery(
        self,
        item_id: str,
        item_title: str = None,
        order_id: str = None,
        send_user_id: str = None,
        *,
        fulfillment_attempt_id: int = None,
        delivery_index: int = 0,
        expected_quantity: int = 1,
        database=None,
    ):
        """Prepare one delivery message without consuming batch inventory directly."""
        try:
            if database is None:
                from db_manager import db_manager as database

            logger.info(f"开始自动发货检查: 商品ID={item_id}")

            # 获取商品详细信息
            item_info = None
            search_text = item_title  # 默认使用传入的标题

            if item_id and item_id != "未知商品":
                # 直接从数据库获取商品信息（发货时不再调用API）
                try:
                    logger.info(f"从数据库获取商品信息: {item_id}")
                    db_item_info = database.get_item_info(self.cookie_id, item_id)
                    if db_item_info:
                        # 拼接商品标题和详情作为搜索文本
                        item_title_db = db_item_info.get('item_title', '') or ''
                        item_detail_db = db_item_info.get('item_detail', '') or ''

                        # 如果数据库中没有详情，尝试自动获取
                        if not item_detail_db.strip():
                            from config import config
                            auto_fetch_config = config.get('ITEM_DETAIL', {}).get('auto_fetch', {})

                            if auto_fetch_config.get('enabled', True):
                                logger.info(f"数据库中商品详情为空，尝试自动获取: {item_id}")
                                try:
                                    fetched_detail = await self.fetch_item_detail_from_api(item_id)
                                    if fetched_detail:
                                        # 保存获取到的详情
                                        await self.save_item_detail_only(item_id, fetched_detail)
                                        item_detail_db = fetched_detail
                                        logger.info(f"成功获取并保存商品详情: {item_id}")
                                    else:
                                        logger.warning(f"未能获取到商品详情: {item_id}")
                                except Exception as api_e:
                                    logger.warning(f"获取商品详情失败: {item_id}, 错误: {self._safe_str(api_e)}")
                            else:
                                logger.warning(f"自动获取商品详情功能已禁用，跳过: {item_id}")

                        # 组合搜索文本：商品标题 + 商品详情
                        search_parts = []
                        if item_title_db.strip():
                            search_parts.append(item_title_db.strip())
                        if item_detail_db.strip():
                            search_parts.append(item_detail_db.strip())

                        if search_parts:
                            search_text = ' '.join(search_parts)
                            logger.info(
                                "使用数据库商品标题和详情匹配发货规则: "
                                f"title_present={bool(item_title_db.strip())}, "
                                f"detail_length={len(item_detail_db)}"
                            )
                        else:
                            logger.warning(f"数据库中商品标题和详情都为空: {item_id}")
                            search_text = item_title or item_id
                    else:
                        logger.warning(f"数据库中未找到商品信息: {item_id}")
                        search_text = item_title or item_id

                except Exception as db_e:
                    logger.warning(f"从数据库获取商品信息失败: {self._safe_str(db_e)}")
                    search_text = item_title or item_id

            if not search_text:
                search_text = item_id or "未知商品"

            logger.info(f"开始匹配发货规则: search_length={len(search_text)}")

            # 检查商品是否为多规格商品
            is_multi_spec = database.get_item_multi_spec_status(self.cookie_id, item_id)
            spec_name = None
            spec_value = None

            # 未验真的详情浏览器保持移除；多规格只接受当前账号已同步的订单快照。
            if is_multi_spec and order_id:
                order_snapshot = database.get_order_by_id(order_id) or {}
                if str(order_snapshot.get('cookie_id') or '') != str(self.cookie_id):
                    logger.warning("多规格订单不存在或账号归属不匹配，跳过自动发货")
                    return None
                spec_name = str(order_snapshot.get('spec_name') or '').strip()
                spec_value = str(order_snapshot.get('spec_value') or '').strip()
                if not spec_name or not spec_value:
                    logger.warning("多规格订单缺少可信规格快照，跳过自动发货")
                    return None
                logger.info("使用当前账号已同步的订单规格快照匹配发货规则")

            # 发货规则匹配必须限定在当前账号归属用户内，防止命中其他租户的规则
            rule_owner_user_id = database.get_cookie_user_id(self.cookie_id)
            if rule_owner_user_id is None:
                logger.warning(f"账号 {self.cookie_id} 未找到归属用户，跳过自动发货")
                return None

            # 关键词只属于从未选择过发货模式的旧商品。任何显式选择一旦
            # 失效都必须失败关闭，不能换成标题碰巧命中的另一个资源。
            delivery_rules = []
            binding_status = database.get_item_delivery_binding_status(
                self.cookie_id,
                item_id,
                rule_owner_user_id,
            )
            if binding_status is None:
                if fulfillment_attempt_id is not None:
                    database.mark_fulfillment_manual_review(
                        int(fulfillment_attempt_id),
                        "delivery_binding_state_unavailable",
                    )
                logger.warning("商品发货模式状态不可用，已停止自动交付")
                return None
            binding_explicit = bool(binding_status.get("binding_explicit"))
            if binding_explicit:
                resource_status = str(
                    binding_status.get("status")
                    or binding_status.get("resource_status")
                    or "missing"
                )
                if resource_status != "active":
                    if resource_status not in {"explicit_off", "invite"} and fulfillment_attempt_id is not None:
                        reason = {
                            "missing": "bound_resource_missing",
                            "disabled": "bound_resource_disabled",
                            "out_of_stock": "bound_resource_out_of_stock",
                            "protocol_invalid": "bound_resource_protocol_invalid",
                            "empty": "bound_resource_empty",
                        }.get(resource_status, "bound_resource_unavailable")
                        database.mark_fulfillment_manual_review(
                            int(fulfillment_attempt_id),
                            reason,
                        )
                    logger.warning(
                        "显式发货模式不可用，禁止关键词回落: status={}",
                        resource_status,
                    )
                    return None
                bound_rule = binding_status.get("rule")
                if not isinstance(bound_rule, dict):
                    if fulfillment_attempt_id is not None:
                        database.mark_fulfillment_manual_review(
                            int(fulfillment_attempt_id),
                            "bound_resource_missing",
                        )
                    return None
                spec_compatible = (
                    not is_multi_spec
                    or not bound_rule.get('is_multi_spec')
                    or (
                        str(bound_rule.get('spec_name') or '') == str(spec_name or '')
                        and str(bound_rule.get('spec_value') or '') == str(spec_value or '')
                    )
                )
                if spec_compatible:
                    logger.info(
                        f"✅ 命中商品级发货绑定: item_id={item_id} -> {bound_rule['card_name']} "
                        f"({bound_rule['card_type']})"
                    )
                    delivery_rules = [bound_rule]
                else:
                    if fulfillment_attempt_id is not None:
                        database.mark_fulfillment_manual_review(
                            int(fulfillment_attempt_id),
                            "bound_resource_spec_mismatch",
                        )
                    logger.warning("商品绑定资源与订单规格不匹配，禁止关键词回落")
                    return None

            if not delivery_rules and not binding_explicit:
                if is_multi_spec:
                    # 多规格商品：只匹配多规格发货规则
                    if spec_name and spec_value:
                        logger.info("多规格商品开始匹配账号内发货规则")
                        delivery_rules = self._match_rules_with_site_fallback(
                            database,
                            rule_owner_user_id,
                            lambda owner_id: [
                                r for r in database.get_delivery_rules_by_keyword_and_spec(
                                    search_text, spec_name, spec_value, user_id=owner_id
                                )
                                if r.get('is_multi_spec')
                            ],
                            "多规格",
                        )

                        if delivery_rules:
                            logger.info(f"✅ 找到匹配的多规格发货规则: {len(delivery_rules)}个")
                        else:
                            logger.warning("❌ 多规格商品未找到匹配的多规格发货规则，跳过自动发货")
                            return None
                    else:
                        logger.warning("❌ 多规格商品但无规格信息，跳过自动发货")
                        return None
                else:
                    # 非多规格商品：只匹配非多规格发货规则
                    logger.info("非多规格商品开始匹配账号内普通发货规则")
                    delivery_rules = self._match_rules_with_site_fallback(
                        database,
                        rule_owner_user_id,
                        lambda owner_id: [
                            r for r in database.get_delivery_rules_by_keyword(
                                search_text, user_id=owner_id
                            )
                            if not r.get('is_multi_spec')
                        ],
                        "普通",
                    )

                    if delivery_rules:
                        logger.info(f"✅ 找到匹配的普通发货规则: {len(delivery_rules)}个")
                    else:
                        logger.warning("❌ 非多规格商品未找到匹配的普通发货规则，跳过自动发货")
                        return None

            # 检查匹配到的卡券数量，只有唯一匹配时才自动发货
            if len(delivery_rules) > 1:
                rule_names = [f"{r['card_name']}({r.get('spec_name', '')}:{r.get('spec_value', '')})" if r.get('is_multi_spec') else r['card_name'] for r in delivery_rules]
                logger.warning(f"❌ 匹配到多个发货规则({len(delivery_rules)}个)，无法确定使用哪个，跳过自动发货: {', '.join(rule_names)}")
                return None

            if not delivery_rules:
                logger.warning("未找到匹配的发货规则")
                return None

            # 使用唯一匹配的规则
            rule = delivery_rules[0]

            # Batch data is reserved before any platform confirmation.  Direct
            # consumption would make a retry after a crash indistinguishable
            # from a completed delivery.
            reserved_batch_values = None
            if rule['card_type'] == 'data':
                if fulfillment_attempt_id is None:
                    logger.warning("批量卡券缺少持久化履约尝试，已停止自动交付")
                    return None
                reserved_batch_values = database.reserve_batch_card_data(
                    fulfillment_attempt_id,
                    rule['card_id'],
                    expected_quantity,
                )
                if (
                    not reserved_batch_values
                    or len(reserved_batch_values) != expected_quantity
                    or delivery_index < 0
                    or delivery_index >= len(reserved_batch_values)
                ):
                    logger.warning("批量卡券预留不足，已停止自动交付")
                    return None
            rule_origin = '商品绑定' if rule.get('source') == 'item_binding' else f"关键词 {rule['keyword']}"
            logger.info(f"✅ 采用发货来源[{rule_origin}] -> {rule['card_name']} ({rule['card_type']})")

            # 保存商品信息到数据库（需要有商品标题才保存）
            # 尝试获取商品标题
            item_title_for_save = None
            try:
                db_item_info = database.get_item_info(self.cookie_id, item_id)
                if db_item_info:
                    item_title_for_save = db_item_info.get('item_title', '').strip()
            except:
                pass

            # 如果有商品标题，则保存商品信息
            if item_title_for_save:
                await self.save_item_info_to_db(item_id, search_text, item_title_for_save)
            else:
                logger.warning(f"跳过保存商品信息：缺少商品标题 - {item_id}")

            # 详细的匹配结果日志
            if rule.get('is_multi_spec'):
                if spec_name and spec_value:
                    logger.info(f"🎯 精确匹配多规格发货规则: {rule['keyword']} -> {rule['card_name']} [{rule['spec_name']}:{rule['spec_value']}]")
                    logger.info(f"📋 订单规格: {spec_name}:{spec_value} ✅ 匹配卡券规格: {rule['spec_name']}:{rule['spec_value']}")
                else:
                    logger.info(f"⚠️ 使用多规格发货规则但无订单规格信息: {rule['keyword']} -> {rule['card_name']} [{rule['spec_name']}:{rule['spec_value']}]")
            else:
                if spec_name and spec_value:
                    logger.info(f"🔄 兜底匹配普通发货规则: {rule['keyword']} -> {rule['card_name']} ({rule['card_type']})")
                    logger.info(f"📋 订单规格: {spec_name}:{spec_value} ➡️ 使用普通卡券兜底")
                else:
                    logger.info(f"✅ 匹配普通发货规则: {rule['keyword']} -> {rule['card_name']} ({rule['card_type']})")

            # 获取延时设置
            delay_seconds = rule.get('card_delay_seconds', 0)

            # 执行延时（不管是否确认发货，只要有延时设置就执行）
            if delay_seconds and delay_seconds > 0:
                logger.info(f"检测到发货延时设置: {delay_seconds}秒，开始延时...")
                await asyncio.sleep(delay_seconds)
                logger.info(f"延时完成")

            # 检查是否存在订单ID，只有存在订单ID才处理发货内容
            if order_id:
                # 保存订单基本信息到数据库（如果还没有详细信息）
                try:
                    # 检查cookie_id是否在cookies表中存在
                    cookie_info = database.get_cookie_by_id(self.cookie_id)
                    if not cookie_info:
                        logger.warning("自动发货订单的账号归属不存在，已停止处理")
                    else:
                        existing_order = database.get_order_by_id(order_id)
                        if not existing_order:
                            # 插入基本订单信息
                            success = database.insert_or_update_order(
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=send_user_id,
                                cookie_id=self.cookie_id
                            )

                            # 使用订单状态处理器设置状态
                            if success and self.order_status_handler:
                                try:
                                    self.order_status_handler.handle_order_basic_info_status(
                                        order_id=order_id,
                                        cookie_id=self.cookie_id,
                                        context="自动发货-基本信息"
                                    )
                                except Exception as e:
                                    logger.error(f"【{self.cookie_id}】订单状态处理器调用失败: {self._safe_str(e)}")

                            if success:
                                logger.info(f"保存基本订单信息到数据库: {order_id}")
                except Exception as db_e:
                    logger.error(f"保存基本订单信息失败: {self._safe_str(db_e)}")

                # 开始处理发货内容
                logger.info(f"开始处理发货内容，规则: {rule['keyword']} -> {rule['card_name']} ({rule['card_type']})")

                delivery_content = None
                content_already_final = False

                # 根据卡券类型处理发货内容
                if rule['card_type'] == 'api':
                    if fulfillment_attempt_id is None:
                        logger.warning("API 资源缺少持久化履约尝试，禁止自动调用")
                        return None
                    api_payloads = await self._prepare_fulfillment_api_v1_payloads(
                        rule=rule,
                        order_id=order_id,
                        item_id=item_id,
                        expected_quantity=expected_quantity,
                        fulfillment_attempt_id=int(fulfillment_attempt_id),
                        spec_name=spec_name,
                        spec_value=spec_value,
                        database=database,
                    )
                    if (
                        not api_payloads
                        or delivery_index < 0
                        or delivery_index >= len(api_payloads)
                    ):
                        return None
                    delivery_content = api_payloads[delivery_index]
                    content_already_final = True

                elif rule['card_type'] == 'text':
                    # 固定文字类型：直接使用文字内容
                    delivery_content = rule['text_content']

                elif rule['card_type'] == 'data':
                    delivery_content = reserved_batch_values[delivery_index]

                elif rule['card_type'] == 'image':
                    # 图片类型：返回图片发送标记，包含卡券ID
                    image_url = rule.get('image_url')
                    if image_url:
                        delivery_content = f"__IMAGE_SEND__{rule['card_id']}|{image_url}"
                        logger.info(f"准备发送图片卡券: card_id={rule['card_id']}")
                    else:
                        logger.error(f"图片卡券缺少图片URL: 卡券ID={rule['card_id']}")
                        delivery_content = None

                if delivery_content:
                    final_content = (
                        delivery_content
                        if content_already_final
                        else self._process_delivery_content_with_description(
                            delivery_content,
                            rule.get('card_description', ''),
                        )
                    )
                    if fulfillment_attempt_id is not None and rule['card_type'] != 'api':
                        if rule['card_type'] == 'data':
                            raw_payloads = list(reserved_batch_values or [])
                        else:
                            raw_payloads = [delivery_content] * int(expected_quantity)
                        final_payloads = [
                            self._process_delivery_content_with_description(
                                value,
                                rule.get('card_description', ''),
                            )
                            for value in raw_payloads
                        ]
                        committed = database.commit_fulfillment_delivery_payload(
                            int(fulfillment_attempt_id),
                            final_payloads,
                            source_type=str(rule['card_type']),
                            source_card_id=int(rule['card_id']),
                        )
                        if (committed or {}).get('outcome') not in {
                            'committed', 'created', 'existing'
                        }:
                            database.mark_fulfillment_manual_review(
                                int(fulfillment_attempt_id),
                                'delivery_payload_conflict',
                            )
                            logger.warning("履约载荷持久化冲突，已停止自动交付")
                            return None
                        persisted_payloads = list(
                            ((committed or {}).get('payload') or {}).get('payloads')
                            or final_payloads
                        )
                        if persisted_payloads != final_payloads:
                            database.mark_fulfillment_manual_review(
                                int(fulfillment_attempt_id),
                                'delivery_payload_conflict',
                            )
                            return None
                        final_content = persisted_payloads[delivery_index]

                    # A durable attempt records delivery only after every
                    # outbound message is acknowledged by the WebSocket.
                    # 商品级绑定没有 delivery_rules 行（rule['id'] 为 None），无计数可累加。
                    if fulfillment_attempt_id is None and rule.get('id') is not None:
                        database.increment_delivery_times(rule['id'])
                    logger.info(f"自动发货成功: 来源={rule_origin}, 内容长度={len(final_content)}")
                    return final_content
                else:
                    logger.warning(f"获取发货内容失败: 来源={rule_origin}")
                    return None
            else:
                # 没有订单ID，记录日志但不处理发货内容
                logger.info(f"⚠️ 未检测到订单ID，跳过发货内容处理。来源: {rule_origin} -> {rule['card_name']} ({rule['card_type']})")
                return None

        except Exception as e:
            logger.error(f"自动发货失败: {self._safe_str(e)}")
            return None



    def _process_delivery_content_with_description(self, delivery_content: str, card_description: str) -> str:
        """处理发货内容和备注信息，实现变量替换"""
        try:
            # 如果是图片发送标记，不进行备注处理，直接返回
            if delivery_content.startswith("__IMAGE_SEND__"):
                return delivery_content

            # 如果没有备注信息，直接返回发货内容
            if not card_description or not card_description.strip():
                return delivery_content

            # 替换备注中的变量
            processed_description = card_description.replace('{DELIVERY_CONTENT}', delivery_content)

            # 如果备注中包含变量替换，返回处理后的备注
            if '{DELIVERY_CONTENT}' in card_description:
                return processed_description
            else:
                # 如果备注中没有变量，将备注和发货内容组合
                return f"{processed_description}\n\n{delivery_content}"

        except Exception as e:
            logger.error(f"处理备注信息失败: {e}")
            # 出错时返回原始发货内容
            return delivery_content

    async def _prepare_fulfillment_api_v1_payloads(
        self,
        *,
        rule,
        order_id: str,
        item_id: str,
        expected_quantity: int,
        fulfillment_attempt_id: int,
        spec_name: str = None,
        spec_value: str = None,
        database=None,
    ):
        """Allocate exactly once with a durable same-key retry fence."""
        if database is None:
            from db_manager import db_manager as database

        def manual_review(reason: str) -> None:
            database.mark_fulfillment_manual_review(
                int(fulfillment_attempt_id),
                reason,
            )

        try:
            expected_quantity = int(expected_quantity)
        except (TypeError, ValueError):
            expected_quantity = 0
        if not fulfillment_attempt_id or expected_quantity < 1:
            return None

        config = rule.get("api_config") or {}
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except (TypeError, ValueError):
                config = {}
        if not isinstance(config, dict) or config.get("protocol") != FULFILLMENT_API_PROTOCOL:
            manual_review("legacy_api_requires_manual_review")
            return None

        url = str(config.get("url") or "").strip()
        token = str(config.get("api_token") or config.get("token") or "").strip()
        if not url.lower().startswith("https://") or not token:
            manual_review("api_v1_configuration_invalid")
            return None
        try:
            timeout = int(config.get("timeout", 10))
        except (TypeError, ValueError):
            timeout = 0
        if timeout < 1 or timeout > 30:
            manual_review("api_v1_configuration_invalid")
            return None
        configured_spec = config.get("spec") or {}
        if not isinstance(configured_spec, dict):
            manual_review("api_v1_configuration_invalid")
            return None
        request_spec = dict(configured_spec)
        if spec_name or spec_value:
            request_spec["selected"] = {
                "name": str(spec_name or ""),
                "value": str(spec_value or ""),
            }

        persisted = database.get_fulfillment_delivery_payload(
            attempt_id=int(fulfillment_attempt_id)
        )
        if persisted:
            payloads = list(persisted.get("payloads") or [])
            if (
                persisted.get("source_type") == "api_v1"
                and len(payloads) == expected_quantity
                and all(isinstance(item, str) and item for item in payloads)
            ):
                return payloads
            manual_review("api_v1_payload_conflict")
            return None

        canonical_config = json.dumps(
            {
                "url": url,
                "timeout": timeout,
                "spec": request_spec,
                "token": token,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        config_fingerprint = str(config.get("config_fingerprint") or "").strip()
        if not config_fingerprint:
            config_fingerprint = hashlib.sha256(
                canonical_config.encode("utf-8")
            ).hexdigest()
        idempotency_key = hashlib.sha256(
            (
                f"fulfillment-api-v1:{fulfillment_attempt_id}:{order_id}:"
                f"{item_id}:{rule.get('card_id')}:{config_fingerprint}"
            ).encode("utf-8")
        ).hexdigest()
        created = database.create_fulfillment_api_operation(
            attempt_id=int(fulfillment_attempt_id),
            card_id=int(rule.get("card_id")),
            idempotency_key=idempotency_key,
            config_fingerprint=config_fingerprint,
            request_spec=request_spec,
        )
        operation = (created or {}).get("operation")
        if (created or {}).get("outcome") == "conflict" or not operation:
            manual_review("api_v1_operation_conflict")
            return None

        operation_id = int(operation["id"])

        def commit_items(items) -> list[str] | None:
            values = [str(item) for item in list(items or [])]
            if (
                len(values) != expected_quantity
                or any(not value or len(value.encode("utf-8")) > 2048 for value in values)
            ):
                manual_review("api_v1_quantity_mismatch")
                return None
            final_values = [
                self._process_delivery_content_with_description(
                    value,
                    str(rule.get("card_description") or ""),
                )
                for value in values
            ]
            committed = database.commit_fulfillment_delivery_payload(
                attempt_id=int(fulfillment_attempt_id),
                payloads=final_values,
                source_type="api_v1",
                source_operation_id=operation_id,
                source_card_id=int(rule.get("card_id")),
            )
            if (committed or {}).get("outcome") not in {
                "committed", "created", "existing"
            }:
                manual_review("api_v1_payload_conflict")
                return None
            payload = (committed or {}).get("payload") or {}
            persisted_values = list(payload.get("payloads") or final_values)
            if persisted_values != final_values:
                manual_review("api_v1_payload_conflict")
                return None
            return final_values

        if operation.get("state") == "succeeded":
            return commit_items(operation.get("response_items") or [])
        if operation.get("state") in {"failed", "manual_review"}:
            if operation.get("state") == "manual_review":
                manual_review("api_v1_operation_manual_review")
            return None

        request_body = {
            "action": "allocate",
            "idempotency_key": str(operation.get("idempotency_key") or idempotency_key),
            "order_id": str(order_id),
            "item_id": str(item_id),
            "quantity": expected_quantity,
            "spec": request_spec,
        }
        attempts_used = int(operation.get("attempt_count") or 0)
        for attempt_number in range(attempts_used, FULFILLMENT_API_MAX_ATTEMPTS):
            try:
                response = await request_public_http(
                    "POST",
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Idempotency-Key": request_body["idempotency_key"],
                    },
                    json_body=request_body,
                    timeout_seconds=timeout,
                    max_response_bytes=256 * 1024,
                    allowed_methods=("POST",),
                    require_https=True,
                )
            except OutboundRequestError as exc:
                retryable = exc.code in {"network_error", "timeout", "http_408", "http_429"}
                database.record_fulfillment_api_attempt(
                    operation_id,
                    state="pending" if retryable else "manual_review",
                    reason_code="api_v1_network_retry" if retryable else "api_v1_request_unknown",
                )
                if not retryable:
                    manual_review("api_v1_request_unknown")
                    return None
                if attempt_number + 1 < FULFILLMENT_API_MAX_ATTEMPTS:
                    await asyncio.sleep(attempt_number + 1)
                continue
            except Exception:
                database.record_fulfillment_api_attempt(
                    operation_id,
                    state="pending",
                    reason_code="api_v1_network_retry",
                )
                if attempt_number + 1 < FULFILLMENT_API_MAX_ATTEMPTS:
                    await asyncio.sleep(attempt_number + 1)
                continue

            http_status = int(getattr(response, "status", 0) or 0)
            if http_status in {408, 429} or http_status >= 500:
                database.record_fulfillment_api_attempt(
                    operation_id,
                    state="pending",
                    http_status=http_status,
                    reason_code="api_v1_http_retry",
                )
                if attempt_number + 1 < FULFILLMENT_API_MAX_ATTEMPTS:
                    await asyncio.sleep(attempt_number + 1)
                continue
            if http_status != 200:
                database.record_fulfillment_api_attempt(
                    operation_id,
                    state="manual_review",
                    http_status=http_status,
                    reason_code="api_v1_http_conflict",
                )
                manual_review("api_v1_http_conflict")
                return None
            try:
                body = json.loads(str(getattr(response, "text", "") or ""))
            except (TypeError, ValueError):
                body = None
            if (
                not isinstance(body, dict)
                or set(body) != {"status", "operation_id", "items"}
                or body.get("status") not in {"succeeded", "pending", "failed"}
                or not isinstance(body.get("operation_id"), str)
                or not body.get("operation_id")
                or not isinstance(body.get("items"), list)
            ):
                database.record_fulfillment_api_attempt(
                    operation_id,
                    state="manual_review",
                    http_status=http_status,
                    reason_code="api_v1_response_invalid",
                )
                manual_review("api_v1_response_invalid")
                return None

            state = str(body["status"])
            if state == "pending":
                database.record_fulfillment_api_attempt(
                    operation_id,
                    state="pending",
                    http_status=http_status,
                    external_operation_id=body["operation_id"],
                )
                if attempt_number + 1 < FULFILLMENT_API_MAX_ATTEMPTS:
                    await asyncio.sleep(attempt_number + 1)
                continue
            if state == "failed":
                database.record_fulfillment_api_attempt(
                    operation_id,
                    state="failed",
                    http_status=http_status,
                    external_operation_id=body["operation_id"],
                    reason_code="api_v1_provider_failed",
                )
                return None

            values = [str(item) for item in body["items"]]
            if (
                len(values) != expected_quantity
                or any(not value or len(value.encode("utf-8")) > 2048 for value in values)
            ):
                database.record_fulfillment_api_attempt(
                    operation_id,
                    state="manual_review",
                    http_status=http_status,
                    external_operation_id=body["operation_id"],
                    reason_code="api_v1_quantity_mismatch",
                )
                manual_review("api_v1_quantity_mismatch")
                return None
            database.record_fulfillment_api_attempt(
                operation_id,
                state="succeeded",
                http_status=http_status,
                external_operation_id=body["operation_id"],
                response_items=values,
            )
            return commit_items(values)

        manual_review("api_v1_retry_exhausted")
        return None

    async def resend_fulfillment_payload(
        self,
        *,
        payload_id: int,
        user_id: int,
        database=None,
    ):
        """Resend only the immutable committed payload and wait for each message ACK."""
        if database is None:
            from db_manager import db_manager as database
        payload = database.get_fulfillment_delivery_payload(
            payload_id=int(payload_id),
            user_id=int(user_id),
        )
        if not payload:
            return None
        attempt = database.get_fulfillment_attempt(int(payload["attempt_id"]))
        if (
            not attempt
            or int(attempt.get("user_id") or 0) != int(user_id)
            or str(attempt.get("state") or "") != "committed"
            or str(attempt.get("cookie_id") or "") != str(self.cookie_id)
        ):
            return None
        order = database.get_order_by_id(str(attempt.get("order_id") or "")) or {}
        if str(order.get("cookie_id") or "") != str(self.cookie_id):
            return None
        buyer_id = str(order.get("buyer_id") or "").strip()
        chat_id = str(order.get("chat_id") or "").strip()
        item_id = str(order.get("item_id") or "").strip()
        payloads = [str(value) for value in list(payload.get("payloads") or [])]
        if not buyer_id or not payloads or not self.ws or getattr(self.ws, "closed", False):
            return None

        prepared = database.record_fulfillment_resend_event(
            payload_id=int(payload_id),
            attempt_id=int(attempt["attempt_id"]),
            user_id=int(user_id),
            cookie_id=str(self.cookie_id),
            status="prepared",
        )
        if not prepared:
            return None

        sent_count = 0
        last_mid = ""
        final_status = "succeeded"
        reason = ""
        try:
            for value in payloads:
                if value.startswith("__IMAGE_SEND__"):
                    image_data = value.replace("__IMAGE_SEND__", "", 1)
                    card_id = None
                    if "|" in image_data:
                        card_id_raw, image_url = image_data.split("|", 1)
                        try:
                            card_id = int(card_id_raw)
                        except ValueError:
                            card_id = None
                    else:
                        image_url = image_data
                    if not chat_id:
                        final_status = "failed"
                        reason = "resend_chat_unavailable"
                        break
                    response = await self.send_image_msg(
                        self.ws,
                        chat_id,
                        buyer_id,
                        image_url,
                        card_id=card_id,
                        wait_for_response=True,
                    )
                elif chat_id:
                    response = await self.send_msg(
                        self.ws,
                        chat_id,
                        buyer_id,
                        value,
                        wait_for_response=True,
                    )
                else:
                    response = await self.send_msg_once(
                        buyer_id,
                        item_id,
                        value,
                        wait_for_response=True,
                    )
                if not isinstance(response, dict):
                    final_status = "ambiguous"
                    reason = "resend_ack_missing"
                    break
                headers = response.get("headers")
                if isinstance(headers, dict):
                    last_mid = str(headers.get("mid") or "")[:128]
                summary = self._direct_frame_error_summary(response)
                code = summary.get("code")
                if code not in (None, "200"):
                    final_status = "ambiguous" if sent_count else "failed"
                    reason = "resend_rejected"
                    break
                sent_count += 1
        except DirectMessageNotSubmitted:
            final_status = "ambiguous" if sent_count else "failed"
            reason = "resend_not_submitted"
        except Exception:
            final_status = "ambiguous"
            reason = "resend_outcome_unknown"

        if sent_count != len(payloads) and final_status == "succeeded":
            final_status = "ambiguous"
            reason = "resend_incomplete"
        final = database.record_fulfillment_resend_event(
            payload_id=int(payload_id),
            attempt_id=int(attempt["attempt_id"]),
            user_id=int(user_id),
            cookie_id=str(self.cookie_id),
            status=final_status,
            request_mid=last_mid,
            reason_code=reason,
        )
        return {
            "status": final_status,
            "event_id": (final or {}).get("id"),
            "request_mid": last_mid,
        }

    async def _get_api_card_content(self, rule, order_id=None, item_id=None, buyer_id=None, spec_name=None, spec_value=None, retry_count=0):
        """调用API获取卡券内容，支持动态参数替换和重试机制"""
        max_retries = 4

        if retry_count >= max_retries:
            logger.error(f"API调用失败，已达到最大重试次数({max_retries})")
            return None

        try:
            api_config = rule.get('api_config')
            if not api_config:
                logger.error(f"API配置为空，规则ID: {rule.get('id')}, 卡券名称: {rule.get('card_name')}")
                return None

            # 解析API配置
            if isinstance(api_config, str):
                api_config = json.loads(api_config)
            if not isinstance(api_config, dict):
                logger.error("API卡券配置格式无效")
                return None

            url = api_config.get('url')
            method = str(api_config.get('method', 'GET')).upper()
            timeout = api_config.get('timeout', 10)
            headers = api_config.get('headers', '{}')
            params = api_config.get('params', '{}')

            # 解析headers和params
            if isinstance(headers, str):
                headers = json.loads(headers)
            if isinstance(params, str):
                params = json.loads(params)
            if not isinstance(headers, dict) or not isinstance(params, dict):
                logger.error("API卡券请求头或参数格式无效")
                return None

            # 如果是POST请求且有动态参数，进行参数替换
            if method == 'POST' and params:
                params = await self._replace_api_dynamic_params(params, order_id, item_id, buyer_id, spec_name, spec_value)

            retry_info = f" (重试 {retry_count + 1}/{max_retries})" if retry_count > 0 else ""
            logger.info(
                "调用API获取卡券: "
                f"method={method}, target={outbound_target_label(url)}{retry_info}"
            )
            if method == 'POST' and params:
                logger.debug(f"POST请求参数已构造: field_count={len(params)}")

            response = await request_public_http(
                method,
                url,
                headers=headers,
                params=params if method == 'GET' else None,
                json_body=params if method == 'POST' else None,
                timeout_seconds=timeout,
                max_response_bytes=256 * 1024,
                allowed_methods=('GET', 'POST'),
                require_https=True,
            )
            status_code = response.status
            response_text = response.text

            if status_code == 200:
                # 尝试解析JSON响应，如果失败则使用原始文本
                try:
                    result = json.loads(response_text)
                    # 如果返回的是对象，尝试提取常见的内容字段
                    if isinstance(result, dict):
                        content = result.get('data') or result.get('content') or result.get('card') or result
                    else:
                        content = result
                    if isinstance(content, (dict, list)):
                        content = json.dumps(content, ensure_ascii=False)
                    else:
                        content = str(content)
                except (TypeError, ValueError, json.JSONDecodeError):
                    content = response_text

                logger.info(f"API调用成功，返回内容长度: {len(content)}")
                return content
            else:
                logger.warning(f"API调用失败: status={status_code}")

                # 如果是服务器错误(5xx)或请求超时，进行重试
                if status_code >= 500 or status_code == 408:
                    if retry_count < max_retries - 1:
                        wait_time = (retry_count + 1) * 2  # 递增等待时间: 2s, 4s, 6s
                        logger.info(f"等待 {wait_time} 秒后重试...")
                        await asyncio.sleep(wait_time)
                        return await self._get_api_card_content(rule, order_id, item_id, buyer_id, spec_name, spec_value, retry_count + 1)

                return None

        except OutboundRequestError as e:
            logger.warning(f"API调用受保护地失败: code={e.code}")
            if e.code == 'network_error' and retry_count < max_retries - 1:
                wait_time = (retry_count + 1) * 2  # 递增等待时间
                logger.info(f"等待 {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)
                return await self._get_api_card_content(rule, order_id, item_id, buyer_id, spec_name, spec_value, retry_count + 1)
            return None

        except Exception as e:
            logger.error(f"API调用异常: {type(e).__name__}")
            return None

    async def _replace_api_dynamic_params(self, params, order_id=None, item_id=None, buyer_id=None, spec_name=None, spec_value=None):
        """替换API请求参数中的动态参数"""
        try:
            if not params or not isinstance(params, dict):
                return params

            # 获取订单和商品信息
            order_info = None
            item_info = None

            # 如果有订单ID，获取订单信息
            if order_id:
                try:
                    from db_manager import db_manager
                    order_info = db_manager.get_order_by_id(order_id)
                    if not order_info:
                        logger.warning("动态参数订单不存在，保留未解析占位符")
                    elif str(order_info.get('cookie_id') or '') != str(self.cookie_id):
                        logger.warning("动态参数订单账号归属不匹配，拒绝读取订单字段")
                        order_info = None
                    else:
                        logger.info("动态参数使用当前账号的本地订单快照")
                except Exception as e:
                    logger.warning(f"获取订单信息失败: {self._safe_str(e)}")

            # 如果有商品ID，获取商品信息
            if item_id:
                try:
                    from db_manager import db_manager
                    item_info = db_manager.get_item_info(self.cookie_id, item_id)
                    if item_info:
                        logger.warning(f"从数据库获取到商品信息: {item_id}")
                    else:
                        logger.warning(f"无法获取商品信息: {item_id}")
                except Exception as e:
                    logger.warning(f"获取商品信息失败: {self._safe_str(e)}")

            # 构建参数映射
            param_mapping = {
                'order_id': order_id or '',
                'item_id': item_id or '',
                'buyer_id': buyer_id or '',
                'cookie_id': self.cookie_id or '',
                'spec_name': spec_name or '',
                'spec_value': spec_value or '',
            }

            # 从订单信息中提取参数
            if order_info:
                param_mapping.update({
                    'order_amount': str(order_info.get('amount', '')),
                    'order_quantity': str(order_info.get('quantity', '')),
                })

            # 从商品信息中提取参数
            if item_info:
                # 处理商品详情，如果是JSON字符串则提取detail字段
                item_detail = item_info.get('item_detail', '')
                if item_detail:
                    try:
                        # 尝试解析JSON
                        import json
                        detail_data = json.loads(item_detail)
                        if isinstance(detail_data, dict) and 'detail' in detail_data:
                            item_detail = detail_data['detail']
                    except (json.JSONDecodeError, TypeError):
                        # 如果不是JSON或解析失败，使用原始字符串
                        pass

                param_mapping.update({
                    'item_detail': item_detail,
                })

            # 递归替换参数
            replaced_params = self._recursive_replace_params(params, param_mapping)

            # 记录替换的参数
            replaced_keys = []
            for key, value in replaced_params.items():
                if isinstance(value, str) and '{' in str(params.get(key, '')):
                    replaced_keys.append(key)

            if replaced_keys:
                logger.info(f"API动态参数替换完成，替换的参数: {replaced_keys}")

            return replaced_params

        except Exception as e:
            logger.error(f"替换API动态参数失败: {self._safe_str(e)}")
            return params

    def _recursive_replace_params(self, obj, param_mapping):
        """递归替换参数中的占位符"""
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                result[key] = self._recursive_replace_params(value, param_mapping)
            return result
        elif isinstance(obj, list):
            return [self._recursive_replace_params(item, param_mapping) for item in obj]
        elif isinstance(obj, str):
            # 替换字符串中的占位符
            result = obj
            for param_key, param_value in param_mapping.items():
                placeholder = f"{{{param_key}}}"
                if placeholder in result:
                    result = result.replace(placeholder, str(param_value))
            return result
        else:
            return obj

    async def token_refresh_loop(self):
        """Token刷新循环"""
        try:
            while True:
                try:
                    # 检查账号是否启用
                    from cookie_manager import manager as cookie_manager
                    if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止Token刷新循环")
                        break

                    refresh_status = db_manager.get_account_session_refresh(self.cookie_id) or {}
                    refresh_state = refresh_status.get("state")
                    refresh_error_code = str(refresh_status.get("error_code") or "").strip()
                    if (
                        refresh_state == "action_required"
                        and refresh_error_code in RETRYABLE_SESSION_ERROR_CODES
                    ):
                        db_manager.update_account_session_refresh(
                            self.cookie_id,
                            state="failed",
                            trigger=refresh_status.get("trigger") or "session_probe",
                            message="平台连接暂时异常，系统将自动重试",
                            error_code=refresh_error_code,
                        )
                        refresh_state = "failed"
                    if refresh_state in {
                        "action_required",
                        "refreshing",
                        "verification_required",
                    }:
                        self.last_token_refresh_status = refresh_state
                        await self._interruptible_sleep(60)
                        continue

                    current_time = time.time()
                    if current_time - self.last_token_refresh_time >= self.token_refresh_interval:
                        logger.info("Token即将过期，准备刷新...")
                        new_token = await self.refresh_token()
                        if new_token:
                            logger.info(f"【{self.cookie_id}】Token刷新成功，将关闭WebSocket以使用新Token重连")

                            # Token刷新成功后，需要关闭WebSocket连接，让它用新Token重新连接
                            # 原因：WebSocket连接建立时使用的是旧Token，新Token需要重新建立连接才能生效
                            # 注意：只关闭WebSocket，不重启整个实例（后台任务继续运行）

                            # 关闭当前WebSocket连接
                            if self.ws and not self.ws.closed:
                                try:
                                    logger.info(f"【{self.cookie_id}】关闭当前WebSocket连接以使用新Token重连...")
                                    await self.ws.close()
                                    logger.info(f"【{self.cookie_id}】WebSocket连接已关闭，将自动重连")
                                except Exception as close_e:
                                    logger.warning(f"【{self.cookie_id}】关闭WebSocket时出错: {self._safe_str(close_e)}")

                            # 退出Token刷新循环，让main循环重新建立连接
                            # 后台任务（心跳、清理等）继续运行
                            logger.info(f"【{self.cookie_id}】Token刷新完成，WebSocket将使用新Token重新连接")
                            break
                        else:
                            if getattr(self, 'last_token_refresh_status', None) in {
                                "action_required",
                                "refreshing",
                                "verification_required",
                            }:
                                logger.info(
                                    f"【{self.cookie_id}】消息 Token 刷新已暂停，"
                                    "等待手动开始验证"
                                )
                                await self._interruptible_sleep(60)
                                continue
                            if is_retryable_session_error_code(
                                refresh_status.get("error_code")
                            ) or getattr(self, "last_token_refresh_status", None) == "retryable_error":
                                logger.info(
                                    f"【{self.cookie_id}】平台连接暂时异常，Token 探测将自动重试"
                                )
                                self.current_token = None
                                await self._interruptible_sleep(self.token_retry_interval)
                                continue
                            # 根据上一次刷新状态决定日志级别（冷却/已重启为正常情况）
                            if getattr(self, 'last_token_refresh_status', None) in ("skipped_cooldown", "restarted_after_cookie_refresh"):
                                logger.info(f"【{self.cookie_id}】Token刷新未执行或已重启（正常），将在{self.token_retry_interval // 60}分钟后重试")
                            else:
                                logger.error(f"【{self.cookie_id}】Token刷新失败，将在{self.token_retry_interval // 60}分钟后重试")

                            # 清空当前token，确保下次重试时重新获取
                            self.current_token = None

                            # 发送Token刷新失败通知
                            await self.send_token_refresh_notification("Token定时刷新失败，将自动重试", "token_scheduled_refresh_failed")
                            await self._interruptible_sleep(self.token_retry_interval)
                            continue
                    await self._interruptible_sleep(60)
                except asyncio.CancelledError:
                    # 收到取消信号，立即退出循环
                    logger.info(f"【{self.cookie_id}】Token刷新循环收到取消信号，准备退出")
                    raise
                except Exception as e:
                    logger.error(f"Token刷新循环出错: {self._safe_str(e)}")
                    # 出错后也等待1分钟再重试，使用可中断的sleep
                    try:
                        await self._interruptible_sleep(60)
                    except asyncio.CancelledError:
                        logger.info(f"【{self.cookie_id}】Token刷新循环在重试等待时收到取消信号，准备退出")
                        raise
        except asyncio.CancelledError:
            # 确保CancelledError被正确传播
            logger.info(f"【{self.cookie_id}】Token刷新循环已取消，正在退出...")
            raise
        finally:
            # 确保任务能正常结束
            logger.info(f"【{self.cookie_id}】Token刷新循环已退出")

    async def create_chat(self, ws, toid, item_id='891198795482', request_mid=None):
        request_mid = request_mid or generate_mid()
        msg = {
            "lwp": "/r/SingleChatConversation/create",
            "headers": {
                "mid": request_mid
            },
            "body": [
                {
                    "pairFirst": f"{toid}@goofish",
                    "pairSecond": f"{self.myid}@goofish",
                    "bizType": "1",
                    "extension": {
                        "itemId": item_id
                    },
                    "ctx": {
                        "appVersion": "1.0",
                        "platform": "web"
                    }
                }
            ]
        }
        await self._ws_send_guarded(ws, msg)
        return request_mid

    def _resolve_direct_conversation_response(self, message_data) -> bool:
        """Resolve a direct-send request received by the main WebSocket loop."""
        if not isinstance(message_data, dict):
            return False
        if str(message_data.get("lwp") or "").startswith("/s/"):
            return False
        headers = message_data.get("headers")
        if not isinstance(headers, dict):
            return False
        request_mid = str(headers.get("mid") or "").strip()
        if not request_mid:
            return False
        waiter = self._direct_conversation_waiters.get(request_mid)
        if waiter is None or waiter.done():
            return False
        waiter.set_result(message_data)
        return True

    def _fail_direct_conversation_waiters(self, reason: str) -> None:
        """Wake direct senders when the owning WebSocket is being closed."""
        for waiter in list(self._direct_conversation_waiters.values()):
            if not waiter.done():
                waiter.set_exception(DirectMessageNotSubmitted(reason))
        self._direct_conversation_waiters.clear()

    async def _request_lwp_response(
        self,
        websocket,
        lwp: str,
        body=None,
        timeout: float = 10,
        headers=None,
    ):
        """Send one LWP request and let the owning reader correlate its response."""
        request_mid = generate_mid()
        waiter = asyncio.get_running_loop().create_future()
        self._direct_conversation_waiters[request_mid] = waiter
        try:
            request_headers = dict(headers or {})
            request_headers["mid"] = request_mid
            message = {
                "lwp": lwp,
                "headers": request_headers,
            }
            if body is not None:
                message["body"] = body
            await self._ws_send_guarded(websocket, message)
            return await asyncio.wait_for(waiter, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise ConnectionError(f"{lwp} response timed out") from exc
        except DirectMessageNotSubmitted:
            if self._websocket_bootstrap_error is not None:
                raise self._websocket_bootstrap_error
            raise
        finally:
            self._direct_conversation_waiters.pop(request_mid, None)

    @staticmethod
    def _extract_direct_conversation_cid(value, depth: int = 0):
        """Find the conversation id in platform response variants."""
        if depth > 8:
            return ""
        if isinstance(value, dict):
            for key in ("singleChatConversation", "conversation", "conversationInfo"):
                nested = value.get(key)
                if isinstance(nested, dict):
                    cid = nested.get("cid")
                    if isinstance(cid, str) and cid.strip():
                        return cid.split("@", 1)[0].strip()
            cid = value.get("cid")
            if isinstance(cid, str) and cid.strip():
                return cid.split("@", 1)[0].strip()
            for nested in value.values():
                found = XianyuLive._extract_direct_conversation_cid(nested, depth + 1)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = XianyuLive._extract_direct_conversation_cid(nested, depth + 1)
                if found:
                    return found
        elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
            try:
                return XianyuLive._extract_direct_conversation_cid(json.loads(value), depth + 1)
            except (TypeError, ValueError):
                return ""
        return ""

    @staticmethod
    def _extract_existing_direct_conversation_cid(value, toid, myid, item_id):
        """Return one existing conversation matching both participants and item."""
        body = value.get("body", {}) if isinstance(value, dict) else {}
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except (TypeError, ValueError):
                return ""
        if not isinstance(body, dict):
            return ""

        expected_users = {str(toid).split("@", 1)[0], str(myid).split("@", 1)[0]}
        expected_item = str(item_id or "").strip()
        exact_matches = []
        itemless_matches = []
        for raw in body.get("userConvs", []):
            if not isinstance(raw, dict):
                continue
            wrapper = raw.get("singleChatUserConversation", raw)
            if not isinstance(wrapper, dict):
                continue
            conversation = wrapper.get("singleChatConversation", wrapper)
            if not isinstance(conversation, dict):
                continue
            participants = {
                str(conversation.get("pairFirst") or "").split("@", 1)[0],
                str(conversation.get("pairSecond") or "").split("@", 1)[0],
            }
            if participants != expected_users:
                continue
            cid = str(conversation.get("cid") or "").split("@", 1)[0].strip()
            if not cid:
                continue
            extension = conversation.get("extension") or {}
            if isinstance(extension, str):
                try:
                    extension = json.loads(extension)
                except (TypeError, ValueError):
                    extension = {}
            conversation_item = ""
            if isinstance(extension, dict):
                conversation_item = str(
                    extension.get("itemId") or extension.get("item_id") or ""
                ).strip()
            if expected_item and conversation_item == expected_item:
                exact_matches.append(cid)
            elif not conversation_item:
                itemless_matches.append(cid)

        matches = list(dict.fromkeys(exact_matches or itemless_matches))
        return matches[0] if len(matches) == 1 else ""

    @staticmethod
    def _extract_session_sync_direct_conversation_cid(value, toid, item_id):
        """Return one direct cid from the signed H5 session list."""
        data = value.get("data", {}) if isinstance(value, dict) else {}
        if not isinstance(data, dict):
            return ""
        sessions = data.get("sessions")
        if not isinstance(sessions, list):
            return ""

        expected_peer = str(toid or "").split("@", 1)[0].strip()
        expected_item = str(item_id or "").strip()
        exact_matches = []
        peer_matches = []
        for raw in sessions:
            if not isinstance(raw, dict):
                continue
            session = raw.get("session", raw)
            if not isinstance(session, dict):
                continue
            user_info = session.get("userInfo") or {}
            if not isinstance(user_info, dict):
                continue
            peer_id = str(user_info.get("userId") or "").split("@", 1)[0].strip()
            if not expected_peer or peer_id != expected_peer:
                continue
            session_type = str(session.get("sessionType") or "").strip()
            if session_type and session_type != "1":
                continue
            cid = str(session.get("sessionId") or raw.get("sessionId") or "")
            cid = cid.split("@", 1)[0].strip()
            if not cid:
                continue
            peer_matches.append(cid)

            extension = session.get("extensions") or session.get("extension") or {}
            if isinstance(extension, str):
                try:
                    extension = json.loads(extension)
                except (TypeError, ValueError):
                    extension = {}
            session_item = ""
            if isinstance(extension, dict):
                session_item = str(
                    extension.get("itemId") or extension.get("item_id") or ""
                ).strip()
            if expected_item and session_item == expected_item:
                exact_matches.append(cid)

        exact_matches = list(dict.fromkeys(exact_matches))
        if len(exact_matches) == 1:
            return exact_matches[0]

        peer_matches = list(dict.fromkeys(peer_matches))
        has_more = str(data.get("hasMore") or "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        return peer_matches[0] if not has_more and len(peer_matches) == 1 else ""

    async def _find_direct_conversation_via_session_sync(self, toid, item_id):
        """Resolve an existing direct cid without opening another WebSocket."""
        token_source = trans_cookies(self.cookies_str).get("_m_h5_tk", "")
        token = token_source.split("_", 1)[0] if token_source else ""
        if not token:
            return ""

        api = "mtop.taobao.idlemessage.pc.session.sync"
        version = "3.0"
        timestamp = str(int(time.time() * 1000))
        data_value = json.dumps({"fetchNum": 100}, separators=(",", ":"))
        params = {
            "jsv": "2.7.2",
            "appKey": "34839810",
            "t": timestamp,
            "sign": generate_sign(timestamp, token, data_value),
            "v": version,
            "type": "originaljson",
            "accountSite": "xianyu",
            "dataType": "json",
            "timeout": "20000",
            "api": api,
            "sessionOption": "AutoLoginOnly",
            "spm_cnt": "a21ybx.im.0.0",
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": self.cookies_str,
            "Origin": "https://www.goofish.com",
            "Referer": "https://www.goofish.com/",
            "User-Agent": self.browser_user_agent,
        }
        url = _resolve_h5_api_url(
            f"https://h5api.m.goofish.com/h5/{api}/{version}/"
        )
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                cookie_jar=aiohttp.DummyCookieJar(),
            ) as session:
                async with session.post(
                    url,
                    params=params,
                    data={"data": data_value},
                    headers=headers,
                ) as response:
                    if response.status >= 400:
                        logger.warning(
                            "【{}】会话列表回退失败: http_status={}",
                            self.cookie_id,
                            response.status,
                        )
                        return ""
                    payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            logger.warning(
                "【{}】会话列表回退异常: error_type={}",
                self.cookie_id,
                type(exc).__name__,
            )
            return ""

        ret = payload.get("ret") if isinstance(payload, dict) else []
        ret = ret if isinstance(ret, list) else []
        if not any("SUCCESS" in str(value) for value in ret):
            code = sanitize_runtime_error(str(ret[0] if ret else "unknown"))
            logger.warning(
                "【{}】会话列表回退被平台拒绝: code={}",
                self.cookie_id,
                code.split("::", 1)[0][:80],
            )
            return ""
        cid = self._extract_session_sync_direct_conversation_cid(
            payload,
            toid,
            item_id,
        )
        data = payload.get("data") if isinstance(payload, dict) else {}
        sessions = data.get("sessions") if isinstance(data, dict) else []
        logger.info(
            "【{}】会话列表回退完成: sessions={}, matched={}",
            self.cookie_id,
            len(sessions) if isinstance(sessions, list) else 0,
            bool(cid),
        )
        return cid

    def _remember_direct_conversation(self, toid, item_id, cid) -> None:
        """Replace a synthetic direct-order reference with the verified IM cid."""
        try:
            order = db_manager.get_recent_order_by_item_and_buyer(str(item_id), str(toid))
            order_id = str((order or {}).get("order_id") or "")
            detail = db_manager.get_order_by_id(order_id) if order_id else None
            if (
                not detail
                or str(detail.get("cookie_id") or "") != str(self.cookie_id)
                or str(detail.get("item_id") or "") != str(item_id)
                or str(detail.get("buyer_id") or "") != str(toid)
                or not str(detail.get("chat_id") or "").startswith("direct:")
            ):
                return
            db_manager.insert_or_update_order(
                order_id=order_id,
                cookie_id=str(self.cookie_id),
                chat_id=str(cid),
            )
        except Exception as exc:
            logger.warning(
                "【{}】直接会话回写失败: error_type={}",
                self.cookie_id,
                type(exc).__name__,
            )

    @staticmethod
    def _direct_frame_shape(value, depth: int = 0):
        """Return response keys and value types without logging payload values."""
        if depth > 6:
            return "depth"
        if isinstance(value, dict):
            return {
                str(key): XianyuLive._direct_frame_shape(nested, depth + 1)
                for key, nested in list(value.items())[:20]
            }
        if isinstance(value, list):
            return [XianyuLive._direct_frame_shape(nested, depth + 1) for nested in value[:3]]
        if isinstance(value, str):
            if value.lstrip().startswith(("{", "[")):
                try:
                    return XianyuLive._direct_frame_shape(json.loads(value), depth + 1)
                except (TypeError, ValueError):
                    pass
            return f"str:{len(value)}"
        return type(value).__name__

    @staticmethod
    def _direct_frame_error_summary(value, depth: int = 0):
        """Expose only normalized protocol error fields for diagnosis."""
        if depth > 6:
            return {}
        if isinstance(value, dict):
            result = {}
            for key in ("code", "reason", "scope"):
                raw = value.get(key)
                if isinstance(raw, (str, int, float, bool)):
                    normalized = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(raw)).strip("_")
                    result[key] = normalized[:80]
            for nested in value.values():
                child = XianyuLive._direct_frame_error_summary(nested, depth + 1)
                for key, raw in child.items():
                    result.setdefault(key, raw)
            return result
        if isinstance(value, list):
            result = {}
            for nested in value[:3]:
                child = XianyuLive._direct_frame_error_summary(nested, depth + 1)
                for key, raw in child.items():
                    result.setdefault(key, raw)
            return result
        if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
            try:
                return XianyuLive._direct_frame_error_summary(json.loads(value), depth + 1)
            except (TypeError, ValueError):
                return {}
        return {}

    async def send_msg(self, ws, cid, toid, text, wait_for_response=False):
        text = {
            "contentType": 1,
            "text": {
                "text": text
            }
        }
        text_base64 = str(base64.b64encode(json.dumps(text).encode('utf-8')), 'utf-8')
        body = [
                {
                    "uuid": generate_uuid(),
                    "cid": f"{cid}@goofish",
                    "conversationType": 1,
                    "content": {
                        "contentType": 101,
                        "custom": {
                            "type": 1,
                            "data": text_base64
                        }
                    },
                    "redPointPolicy": 0,
                    "extension": {
                        "extJson": "{}"
                    },
                    "ctx": {
                        "appVersion": "1.0",
                        "platform": "web"
                    },
                    "mtags": {},
                    "msgReadStatusSetting": 1
                },
                {
                    "actualReceivers": [
                        f"{toid}@goofish",
                        f"{self.myid}@goofish"
                    ]
                }
            ]
        if wait_for_response:
            return await self._request_lwp_response(
                ws,
                "/r/MessageSend/sendByReceiverScope",
                body=body,
                timeout=10,
            )
        msg = {
            "lwp": "/r/MessageSend/sendByReceiverScope",
            "headers": {
                "mid": generate_mid()
            },
            "body": body,
        }
        await self._ws_send_guarded(ws, msg)
        return True

    async def init(self, ws):
        # 如果没有token或者token过期，获取新token
        token_refresh_attempted = False
        if not self.current_token or (time.time() - self.last_token_refresh_time) >= self.token_refresh_interval:
            logger.info(f"【{self.cookie_id}】获取初始token...")
            token_refresh_attempted = True

            await self.refresh_token()

        if not self.current_token:
            logger.error("无法获取有效token，初始化失败")
            # 只有在没有尝试刷新token的情况下才发送通知，避免与refresh_token中的通知重复
            if not token_refresh_attempted:
                await self.send_token_refresh_notification("初始化时无法获取有效Token", "token_init_failed")
            else:
                logger.info("由于刚刚尝试过token刷新，跳过重复的初始化失败通知")
            raise Exception("Token获取失败")

        self._websocket_bootstrap_sync_event = asyncio.Event()
        register_response = await self._request_lwp_response(
            ws,
            "/reg",
            headers={
                "cache-header": "app-key token ua wv",
                "app-key": APP_CONFIG.get('app_key'),
                "token": self.current_token,
                "ua": self.browser_user_agent,
                "dt": "j",
                "wv": "im:3,au:3,sy:6",
                "sync": "0,0;0;0;",
                "did": self.device_id,
            },
        )
        if (
            not isinstance(register_response, dict)
            or register_response.get("code") not in (None, 200, "200")
        ):
            logger.warning(
                "【{}】WebSocket注册响应未通过: shape={}, protocol={}",
                self.cookie_id,
                json.dumps(self._direct_frame_shape(register_response), ensure_ascii=False),
                json.dumps(
                    self._direct_frame_error_summary(register_response),
                    ensure_ascii=False,
                ),
            )
            raise ConnectionError("websocket registration failed")
        if self._websocket_bootstrap_error is not None:
            raise self._websocket_bootstrap_error

        await self._ws_send_guarded(ws, {
            "lwp": "/r/Conversation/listNewestPagination",
            "headers": {"mid": generate_mid()},
            "body": [9007199254740991, 50],
        })
        try:
            await asyncio.wait_for(
                self._websocket_bootstrap_sync_event.wait(),
                timeout=getattr(self, "_websocket_bootstrap_sync_timeout", 3.0),
            )
        except asyncio.TimeoutError:
            logger.debug(f"【{self.cookie_id}】初始同步推送未在等待窗口内到达")
        if self._websocket_bootstrap_error is not None:
            raise self._websocket_bootstrap_error

        state_response = await self._request_lwp_response(
            ws,
            "/r/SyncStatus/getState",
            [{"topic": "sync"}],
        )
        if (
            not isinstance(state_response, dict)
            or state_response.get("code") not in (None, 200, "200")
        ):
            raise ConnectionError("websocket sync state request failed")
        sync_state = state_response.get("body")
        if not isinstance(sync_state, dict) or not sync_state:
            raise ConnectionError("websocket sync state was missing")
        ack_response = await self._request_lwp_response(
            ws,
            "/r/SyncStatus/ackDiff",
            [sync_state],
        )
        if (
            not isinstance(ack_response, dict)
            or ack_response.get("code") not in (None, 200, "200")
        ):
            raise ConnectionError("websocket sync acknowledgement failed")
        self._websocket_bootstrap_sync_event = None
        logger.info(f'【{self.cookie_id}】连接注册完成')

    async def send_heartbeat(self, ws):
        """发送心跳包"""
        # 检查WebSocket连接状态，如果已关闭则不发送
        if ws.closed:
            raise ConnectionError("WebSocket连接已关闭，无法发送心跳")

        msg = {
            "lwp": "/!",
            "headers": {
                "mid": generate_mid()
            }
        }
        # 添加超时保护，避免在WebSocket关闭时阻塞
        try:
            await asyncio.wait_for(ws.send(json.dumps(msg)), timeout=2.0)
            self.last_heartbeat_time = time.time()
            logger.warning(f"【{self.cookie_id}】心跳包已发送")
        except asyncio.TimeoutError:
            raise ConnectionError("心跳发送超时，WebSocket可能已断开")
        except asyncio.CancelledError:
            # 如果被取消，立即重新抛出，不执行后续操作
            raise

    async def _ws_send_guarded(self, ws, payload):
        """带超时的 WebSocket 发送。

        没有上限时 `await ws.send` 可能永久挂起；若发生在履约路径上，会一直占住
        订单锁或履约租约。超时统一按连接不可用处理，交由上层重连。
        """
        try:
            await asyncio.wait_for(ws.send(json.dumps(payload)), timeout=WS_SEND_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise ConnectionError(
                f"WebSocket 发送超过 {WS_SEND_TIMEOUT}s 未完成，判定连接不可用"
            ) from exc

    async def _close_dead_websocket(self, ws):
        """关闭判定为假死的连接；关闭本身也要有上限，避免卡在关闭握手上。"""
        try:
            await asyncio.wait_for(ws.close(), timeout=WS_CLOSE_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception as close_error:
            # 关闭失败不影响"这条连接已废弃"的结论，记录后交给外层重连
            logger.warning(
                f"【{self.cookie_id}】关闭假死连接时出错（已忽略）: {self._safe_str(close_error)}"
            )

    async def heartbeat_loop(self, ws):
        """心跳循环

        除了按间隔发心跳，还必须核对对端是否真的在回。只发不收说明是半开连接，
        此时 send 仍会"成功"，若不主动断开，账号监听会长期僵死且不报错。
        """
        consecutive_failures = 0
        max_failures = 3  # 连续失败3次后停止心跳
        # 以进入循环的时刻作为响应基线，避免新连接首轮就被判成假死
        self.last_heartbeat_response = time.time()

        try:
            while True:
                try:
                    # 检查账号是否启用
                    from cookie_manager import manager as cookie_manager
                    if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止心跳循环")
                        break

                    # 检查WebSocket连接状态
                    if ws.closed:
                        logger.warning(f"【{self.cookie_id}】WebSocket连接已关闭，停止心跳循环")
                        break

                    await self.send_heartbeat(ws)
                    consecutive_failures = 0  # 重置失败计数

                    await self._interruptible_sleep(self.heartbeat_interval)

                    # 真正执行 heartbeat_timeout：超时未见任何心跳响应即判定假死并断开，
                    # 由外层重连逻辑接手，而不是继续对着一条死连接发心跳。
                    silence_seconds = time.time() - self.last_heartbeat_response
                    if silence_seconds > self.heartbeat_timeout:
                        logger.error(
                            f"【{self.cookie_id}】心跳静默 {silence_seconds:.1f}s "
                            f"已超过 {self.heartbeat_timeout}s 阈值，判定连接假死，主动关闭以触发重连"
                        )
                        await self._close_dead_websocket(ws)
                        break

                except asyncio.CancelledError:
                    # 收到取消信号，立即退出循环
                    logger.info(f"【{self.cookie_id}】心跳循环收到取消信号，准备退出")
                    raise  # 重新抛出，让任务正常结束
                except Exception as e:
                    consecutive_failures += 1
                    logger.error(f"心跳发送失败 ({consecutive_failures}/{max_failures}): {self._safe_str(e)}")

                    if consecutive_failures >= max_failures:
                        logger.error(f"【{self.cookie_id}】心跳连续失败{max_failures}次，停止心跳循环")
                        break

                    # 失败后短暂等待再重试，使用可中断的sleep
                    try:
                        await self._interruptible_sleep(5)
                    except asyncio.CancelledError:
                        # 在等待重试时收到取消信号，立即退出
                        logger.info(f"【{self.cookie_id}】心跳循环在重试等待时收到取消信号，准备退出")
                        raise
        except asyncio.CancelledError:
            # 确保CancelledError被正确传播
            logger.info(f"【{self.cookie_id}】心跳循环已取消，正在退出...")
            raise
        finally:
            # 确保任务能正常结束
            logger.info(f"【{self.cookie_id}】心跳循环已退出")

    async def handle_heartbeat_response(self, message_data):
        """处理心跳响应"""
        try:
            if message_data.get("code") == 200 and not message_data.get("lwp"):
                self.last_heartbeat_response = time.time()
                logger.warning("心跳响应正常")
                return True
        except Exception as e:
            logger.error(f"处理心跳响应出错: {self._safe_str(e)}")
        return False

    async def pause_cleanup_loop(self):
        """定期清理过期的暂停记录、锁和缓存"""
        try:
            while True:
                try:
                    # 检查账号是否启用
                    from cookie_manager import manager as cookie_manager
                    if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止清理循环")
                        break

                    # 清理过期的暂停记录
                    pause_manager.cleanup_expired_pauses()
                    await asyncio.sleep(0)  # 让出控制权，允许检查取消信号

                    # 清理过期的锁（每5分钟清理一次，保留24小时内的锁）
                    self.cleanup_expired_locks(max_age_hours=24)
                    await asyncio.sleep(0)  # 让出控制权，允许检查取消信号

                    # 清理过期的商品详情缓存
                    try:
                        cleaned_count = await self._cleanup_item_cache()
                        if cleaned_count > 0:
                            logger.info(f"【{self.cookie_id}】清理了 {cleaned_count} 个过期的商品详情缓存")
                    except asyncio.CancelledError:
                        raise
                    except Exception as cache_clean_e:
                        logger.warning(f"【{self.cookie_id}】清理商品详情缓存时出错: {cache_clean_e}")

                    # 清理过期的通知、发货和订单确认记录（防止内存泄漏）
                    self._cleanup_instance_caches()
                    await asyncio.sleep(0)  # 让出控制权，允许检查取消信号

                    # 清理Playwright浏览器临时文件和缓存（每5分钟检查一次）
                    try:
                        await self._cleanup_playwright_cache()
                    except asyncio.CancelledError:
                        raise
                    except Exception as pw_clean_e:
                        logger.warning(f"【{self.cookie_id}】清理Playwright缓存时出错: {pw_clean_e}")

                    # 清理过期的日志文件（每5分钟检查一次，保留7天）
                    try:
                        cleaned_logs = await self._cleanup_old_logs(retention_days=7)
                        await asyncio.sleep(0)  # 让出控制权，允许检查取消信号
                    except asyncio.CancelledError:
                        raise
                    except Exception as log_clean_e:
                        logger.warning(f"【{self.cookie_id}】清理日志文件时出错: {log_clean_e}")

                    # 清理数据库历史数据（每天一次，保留90天数据）
                    # 为避免所有实例同时执行，只让第一个实例执行
                    try:
                        if hasattr(self.__class__, '_last_db_cleanup_time'):
                            last_cleanup = self.__class__._last_db_cleanup_time
                        else:
                            self.__class__._last_db_cleanup_time = 0
                            last_cleanup = 0

                        current_time = time.time()
                        # 每24小时清理一次
                        if current_time - last_cleanup > 86400:
                            logger.info(f"【{self.cookie_id}】开始执行数据库历史数据清理...")
                            # 数据库清理可能很耗时，使用线程池执行，避免阻塞事件循环
                            # 这样即使清理操作很慢，也能响应取消信号
                            try:
                                stats = await asyncio.to_thread(db_manager.cleanup_old_data, days=90)
                                if 'error' not in stats:
                                    logger.info(f"【{self.cookie_id}】数据库清理完成: {stats}")
                                    self.__class__._last_db_cleanup_time = current_time
                                else:
                                    logger.error(f"【{self.cookie_id}】数据库清理失败: {stats['error']}")
                            except asyncio.CancelledError:
                                logger.warning(f"【{self.cookie_id}】数据库清理被取消")
                                raise
                    except asyncio.CancelledError:
                        raise  # 重新抛出取消信号
                    except Exception as db_clean_e:
                        logger.error(f"【{self.cookie_id}】清理数据库历史数据时出错: {db_clean_e}")

                    # 每5分钟清理一次
                    await self._interruptible_sleep(300)
                except asyncio.CancelledError:
                    # 收到取消信号，立即退出循环
                    logger.info(f"【{self.cookie_id}】清理循环收到取消信号，准备退出")
                    raise
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】清理任务失败: {self._safe_str(e)}")
                    # 出错后也等待5分钟再重试，使用可中断的sleep
                    try:
                        await self._interruptible_sleep(300)
                    except asyncio.CancelledError:
                        logger.info(f"【{self.cookie_id}】清理循环在重试等待时收到取消信号，准备退出")
                        raise
        except asyncio.CancelledError:
            # 确保CancelledError被正确传播
            logger.info(f"【{self.cookie_id}】清理循环已取消，正在退出...")
            raise
        finally:
            # 确保任务能正常结束
            logger.info(f"【{self.cookie_id}】清理循环已退出")


    async def item_sync_loop(self):
        """商品同步定时任务 - 按配置间隔定时同步商品信息

        支持动态配置更新：每次循环时从数据库读取最新配置
        """
        try:
            while True:
                try:
                    # 检查账号是否启用
                    from cookie_manager import manager as cookie_manager
                    if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止商品同步循环")
                        break

                    # 从数据库读取最新配置（支持动态更新）
                    from db_manager import db_manager
                    from settings_service import resolve_user_basic_settings

                    global_sync_settings = {
                        'item_sync_enabled': db_manager.get_system_setting('item_sync_enabled'),
                        'item_sync_interval': db_manager.get_system_setting('item_sync_interval'),
                        'item_sync_max_pages': db_manager.get_system_setting('item_sync_max_pages'),
                    }
                    personal_sync_settings = (
                        db_manager.get_user_settings(self.user_id)
                        if self.user_id is not None else {}
                    )
                    effective_sync = resolve_user_basic_settings(
                        global_sync_settings,
                        personal_sync_settings,
                    )['settings']
                    item_sync_enabled = effective_sync['item_sync_enabled']
                    item_sync_interval = effective_sync['item_sync_interval']
                    item_sync_max_pages = effective_sync['item_sync_max_pages']

                    # 检查是否启用了商品同步功能
                    if not item_sync_enabled:
                        await self._interruptible_sleep(60)  # 未启用时每分钟检查一次
                        continue

                    # 检查距离上次同步的时间
                    current_time = time.time()
                    if current_time - self.last_item_sync_time < item_sync_interval:
                        # 未到达同步时间，等待
                        wait_time = min(60, item_sync_interval - (current_time - self.last_item_sync_time))
                        await self._interruptible_sleep(wait_time)
                        continue

                    # 使用Lock防止重复执行
                    if self.item_sync_lock.locked():
                        logger.info(f"【{self.cookie_id}】商品同步任务正在进行中，跳过本次执行")
                        await self._interruptible_sleep(60)
                        continue

                    # 执行商品同步
                    async with self.item_sync_lock:
                        try:
                            logger.info(f"【{self.cookie_id}】🔄 开始定时同步商品信息...")
                            result = await self.get_all_items(page_size=20, max_pages=item_sync_max_pages)

                            if result.get('success'):
                                total_count = result.get('total_count', 0)
                                saved_count = result.get('total_saved', 0)
                                self.last_item_sync_time = current_time
                                logger.info(f"【{self.cookie_id}】✅ 商品同步完成: 共 {total_count} 件商品，保存/更新 {saved_count} 件")
                            else:
                                error_msg = result.get('error', '未知错误')
                                logger.warning(f"【{self.cookie_id}】❌ 商品同步失败: {error_msg}")

                        except asyncio.CancelledError:
                            logger.info(f"【{self.cookie_id}】商品同步被取消")
                            raise
                        except Exception as sync_error:
                            logger.error(f"【{self.cookie_id}】商品同步异常: {self._safe_str(sync_error)}")

                    # 等待下次同步时间
                    await self._interruptible_sleep(item_sync_interval)

                except asyncio.CancelledError:
                    # 收到取消信号，立即退出循环
                    logger.info(f"【{self.cookie_id}】商品同步循环收到取消信号，准备退出")
                    raise
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】商品同步任务失败: {self._safe_str(e)}")
                    # 出错后等待1分钟再重试
                    try:
                        await self._interruptible_sleep(60)
                    except asyncio.CancelledError:
                        logger.info(f"【{self.cookie_id}】商品同步循环在重试等待时收到取消信号，准备退出")
                        raise
        except asyncio.CancelledError:
            # 确保CancelledError被正确传播
            logger.info(f"【{self.cookie_id}】商品同步循环已取消，正在退出...")
            raise
        finally:
            # 确保任务能正常结束
            logger.info(f"【{self.cookie_id}】商品同步循环已退出")


    async def cookie_refresh_loop(self):
        """Cookie刷新定时任务 - 按账号设置执行预防性刷新"""
        try:
            while True:
                try:
                    self.refresh_cookie_refresh_settings_from_db()

                    # 检查账号是否启用
                    from cookie_manager import manager as cookie_manager
                    if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止Cookie刷新循环")
                        break

                    # L3 主动保活：独立于预防性 Cookie 刷新开关——即便后者关着，有 L3
                    # 记忆的账号也趁会话仍有效时免密续期，避免"死后才续必失败"。开关取
                    # 「全局 config.L3_KEEPALIVE_ENABLED 或该号灰度开关」的或，便于只给
                    # 配了住宅代理的号先开。
                    # getattr 兜底：测试常用 object.__new__ 构造骨架实例，不应因此炸循环。
                    if XianyuLive._l3_keepalive_due(
                        time.time(),
                        getattr(self, 'last_l3_keepalive_time', 0),
                        getattr(self, 'l3_keepalive_interval', 0),
                        enabled=self._l3_keepalive_switch(),
                    ):
                        self.last_l3_keepalive_time = time.time()
                        asyncio.create_task(self._execute_l3_keepalive())

                    # 检查Cookie刷新功能是否启用
                    if not self.cookie_refresh_enabled:
                        await self._interruptible_sleep(300)  # 5分钟后再检查
                        continue

                    current_time = time.time()
                    next_refresh_time = getattr(
                        self,
                        'next_cookie_refresh_time',
                        self.last_cookie_refresh_time + self.cookie_refresh_interval,
                    )
                    if current_time >= next_refresh_time:
                        # 检查是否在消息接收后的冷却时间内
                        time_since_last_message = current_time - self.last_message_received_time
                        if time_since_last_message < self.message_cookie_refresh_cooldown:
                            remaining_time = self.message_cookie_refresh_cooldown - time_since_last_message
                            remaining_minutes = int(remaining_time // 60)
                            remaining_seconds = int(remaining_time % 60)
                            logger.warning(f"【{self.cookie_id}】收到消息后冷却中，还需等待 {remaining_minutes}分{remaining_seconds}秒 才能执行Cookie刷新")
                        # 检查是否已有Cookie刷新任务在执行
                        elif self.cookie_refresh_lock.locked():
                            logger.warning(f"【{self.cookie_id}】Cookie刷新任务已在执行中，跳过本次触发")
                        else:
                            logger.info(f"【{self.cookie_id}】开始执行定时Cookie刷新任务，间隔 {self._format_cookie_refresh_interval()}...")
                            self.last_cookie_refresh_time = current_time
                            self.next_cookie_refresh_time = current_time + self._next_cookie_refresh_delay()
                            # 在独立的任务中执行Cookie刷新，避免阻塞主循环
                            asyncio.create_task(self._execute_cookie_refresh(current_time))

                    # 每分钟检查一次是否需要执行
                    await self._interruptible_sleep(60)
                except asyncio.CancelledError:
                    # 收到取消信号，立即退出循环
                    logger.info(f"【{self.cookie_id}】Cookie刷新循环收到取消信号，准备退出")
                    raise
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】Cookie刷新循环失败: {self._safe_str(e)}")
                    # 出错后也等待1分钟再重试，使用可中断的sleep
                    try:
                        await self._interruptible_sleep(60)
                    except asyncio.CancelledError:
                        logger.info(f"【{self.cookie_id}】Cookie刷新循环在重试等待时收到取消信号，准备退出")
                        raise
        except asyncio.CancelledError:
            # 确保CancelledError被正确传播
            logger.info(f"【{self.cookie_id}】Cookie刷新循环已取消，正在退出...")
            raise
        finally:
            # 确保任务能正常结束
            logger.info(f"【{self.cookie_id}】Cookie刷新循环已退出")

    async def _execute_cookie_refresh(self, current_time):
        """Run scheduled renewal through the same official profile service."""
        from account_session_refresh import active_refresh_registry

        if active_refresh_registry.is_active(self.cookie_id):
            logger.info(f"【{self.cookie_id}】已有Cookie刷新或验证任务运行中，跳过定时刷新")
            return

        async with self.cookie_refresh_lock:
            try:
                self.refresh_cookie_refresh_settings_from_db()
                if not self.cookie_refresh_enabled:
                    logger.info(
                        f"【{self.cookie_id}】定时 Cookie 刷新已关闭，跳过待执行任务"
                    )
                    return
                self.last_cookie_refresh_time = current_time
                success = await self._try_password_login_refresh(
                    f"定时 Cookie 刷新（每{self._format_cookie_refresh_interval()}）"
                )
                if success:
                    self.last_cookie_refresh_time = current_time
                    self.next_cookie_refresh_time = time.time() + self._next_cookie_refresh_delay()
                    logger.info(f"【{self.cookie_id}】定时 Cookie 刷新完成")
                else:
                    self.last_cookie_refresh_time = current_time
                    self.next_cookie_refresh_time = time.time() + self._next_cookie_refresh_delay()
                    logger.warning(f"【{self.cookie_id}】定时 Cookie 刷新未完成")
            except Exception as e:
                logger.error(f"【{self.cookie_id}】执行Cookie刷新任务异常: {self._safe_str(e)}")
                self.last_cookie_refresh_time = current_time
                self.next_cookie_refresh_time = time.time() + self._next_cookie_refresh_delay()
            finally:
                self.last_message_received_time = 0

    @staticmethod
    def _l3_keepalive_due(now, last_time, interval, *, enabled) -> bool:
        """主动保活是否到点：仅开关开启且间隔已过才 True（纯函数，便于单测）。"""
        if not enabled or interval <= 0:
            return False
        return (float(now) - float(last_time or 0)) >= float(interval)

    def _l3_keepalive_switch(self) -> bool:
        """保活是否对本号开启：全局开关或该号的按号灰度开关任一为真。

        按号开关让「只给配了住宅代理的号开保活」成为可能，避免全局一开、
        没配代理的号从机房 IP 去打 passport。
        """
        return bool(L3_KEEPALIVE_ENABLED or getattr(self, "l3_keepalive_enabled", False))

    async def _execute_l3_keepalive(self, *, manual: bool = False) -> dict:
        """趁会话仍有效时用 L3「快速进入」免密续签，让 cookie2 常青。

        安全铁律：只有成功拿到新会话才交接监听；任何失败/未续新都只记日志，
        绝不清 has_l3_memory、不标过期、不动现有监听——绝不因保活打扰在跑的账号。

        manual=True 由「立即刷新 Cookie」按钮触发，只绕过保活开关与调度间隔，
        其余护栏（并发锁、记忆存在性、代理健康门禁）一律照旧。
        """
        if not manual and not self._l3_keepalive_switch():
            return {"ok": False, "code": "keepalive_disabled", "message": "该账号未开启主动保活"}
        if self.l3_keepalive_lock.locked() or self.cookie_refresh_lock.locked():
            return {"ok": False, "code": "busy", "message": "该账号正在续签中，请稍后再试"}
        from db_manager import db_manager
        from account_session_refresh import active_refresh_registry

        if active_refresh_registry.is_active(self.cookie_id):
            return {"ok": False, "code": "busy", "message": "该账号已有登录或刷新会话在进行"}
        account_info = await asyncio.to_thread(db_manager.get_cookie_details, self.cookie_id)
        if not account_info or not bool(account_info.get("has_l3_memory")):
            return {"ok": False, "code": "no_l3_memory", "message": "该账号还没有浏览器登录记忆"}
        profile_unb = str(account_info.get("xianyu_unb") or "").strip()
        if not profile_unb:
            return {"ok": False, "code": "no_unb", "message": "该账号缺少稳定身份标识"}
        if not await self._proxy_preflight_ok("L3 主动保活"):
            return {"ok": False, "code": "proxy_unhealthy", "message": "账号代理不可用，已跳过续签"}
        async with self.l3_keepalive_lock:
            try:
                l3_cookie = await self._recover_via_passwordless_refresh(
                    profile_unb, self.cookies_str, "L3主动保活"
                )
            except Exception as exc:
                logger.warning(
                    f"【{self.cookie_id}】L3 主动保活异常（忽略，不影响现有会话）: {self._safe_str(exc)}"
                )
                return {
                    "ok": False,
                    "code": "l3_exception",
                    "message": "免密续签执行异常，现有会话不受影响",
                }
            if not l3_cookie:
                error_code = str(getattr(self, "_last_l3_error_code", "") or "")
                if error_code in L3_KEEPALIVE_RESEED_CODES:
                    logger.info(
                        f"【{self.cookie_id}】L3 记忆不可用（{error_code}），趁会话仍有效重建档案"
                    )
                    await self._reseed_l3_memory(profile_unb)
                    return {
                        "ok": False,
                        "code": "l3_reseeded",
                        "message": "浏览器记忆已重建，稍后会自动续签",
                    }
                logger.info(
                    f"【{self.cookie_id}】L3 主动保活本次未续新"
                    f"（{error_code or 'no-op'}），保持现有会话"
                )
                return {
                    "ok": False,
                    "code": error_code or "l3_no_op",
                    "message": "本次未续新，现有会话保持不变",
                }
            updated = await self._update_cookies_and_restart(
                l3_cookie,
                browser_user_agent=self.browser_user_agent or detect_default_browser_user_agent(),
                expected_xianyu_unb=profile_unb,
            )
            if updated:
                await asyncio.to_thread(db_manager.mark_cookie_validated, self.cookie_id)
                logger.info(f"【{self.cookie_id}】L3 主动保活成功，会话已提前续新")
                return {"ok": True, "code": "renewed", "message": "免密续签成功，会话已续新"}
            logger.warning(
                f"【{self.cookie_id}】L3 主动保活拿到 Cookie 但监听交接失败（现有会话不受影响）"
            )
            return {
                "ok": False,
                "code": "handover_failed",
                "message": "已续到新会话但监听交接失败",
            }

    async def _reseed_l3_memory(self, profile_unb: str):
        """记忆已死但会话还活着：趁活用当前 Cookie 重建浏览器档案（含就地验证）。

        这是保活相对被动续签的独有窗口——会话死后只能人工重登，会话活着时
        却可以随时把记忆种回去。验证结论如实回写 has_l3_memory：重建成功标
        True；验证明确失败或建档失败标 False（本来就是从「记忆不可用」进来
        的，如实清标能让保活停止无谓重试，扫码重登时会重新建档）。
        """
        from db_manager import db_manager

        def _seed():
            from utils.xianyu_l3_memory import seed_profile_from_cookies

            account_proxy = db_manager.get_account_proxy_config(self.cookie_id)
            return seed_profile_from_cookies(
                profile_unb, self.cookies_str, proxy=account_proxy
            )

        try:
            seeded = await asyncio.to_thread(_seed)
        except Exception as exc:
            logger.warning(f"【{self.cookie_id}】L3 记忆重建异常: {self._safe_str(exc)}")
            return
        ready = bool(getattr(seeded, "has_l3_memory", False))
        await asyncio.to_thread(db_manager.mark_l3_memory, self.cookie_id, ready=ready)
        if not ready:
            logger.warning(
                f"【{self.cookie_id}】L3 记忆重建未成功"
                f"（{getattr(seeded, 'error_code', '') or 'unknown'}），已如实标记无记忆"
            )
            return
        logger.info(f"【{self.cookie_id}】L3 记忆重建成功")
        if getattr(seeded, "quick_entry_verified", None) is not True:
            return
        # 就地验证点了「快速进入」，服务端可能已轮换 cookie2：必须把换发的
        # 新会话交接给监听，否则现有监听拿着旧值迟早断流。
        from utils.xianyu_l3_memory import cookies_to_string as l3_cookies_to_string

        renewed = l3_cookies_to_string(getattr(seeded, "cookies", None) or {})
        if not renewed:
            return
        updated = await self._update_cookies_and_restart(
            renewed,
            browser_user_agent=(
                getattr(seeded, "browser_user_agent", "") or self.browser_user_agent
            ),
            expected_xianyu_unb=profile_unb,
        )
        if updated:
            await asyncio.to_thread(db_manager.mark_cookie_validated, self.cookie_id)
            logger.info(f"【{self.cookie_id}】L3 记忆重建附带续新会话，已交接监听")

    def enable_cookie_refresh(self, enabled: bool = True):
        """启用或禁用Cookie刷新功能"""
        interval_minutes = max(1, int(self.cookie_refresh_interval // 60))
        self.configure_cookie_refresh(enabled, interval_minutes)


    async def send_msg_once(self, toid, item_id, text, wait_for_response=False):
        async with self.direct_message_lock:
            websocket = self.ws
            if not websocket or getattr(websocket, "closed", False):
                raise DirectMessageNotSubmitted("account websocket is offline")
            request_mid = generate_mid()
            loop = asyncio.get_running_loop()
            waiter = loop.create_future()
            self._direct_conversation_waiters[request_mid] = waiter
            try:
                await self.create_chat(websocket, toid, item_id, request_mid=request_mid)
                response = await asyncio.wait_for(waiter, timeout=10)
                cid = self._extract_direct_conversation_cid(response)
                summary = self._direct_frame_error_summary(response)
                if not cid:
                    logger.warning(
                        "【{}】直接会话响应缺少 cid: shape={}, protocol={}",
                        self.cookie_id,
                        json.dumps(self._direct_frame_shape(response), ensure_ascii=False),
                        json.dumps(summary, ensure_ascii=False),
                    )
                if not cid and summary.get("code") == "400":
                    list_mid = generate_mid()
                    list_waiter = loop.create_future()
                    self._direct_conversation_waiters[list_mid] = list_waiter
                    try:
                        await self._ws_send_guarded(websocket, {
                            "lwp": "/r/Conversation/listNewestPagination",
                            "headers": {"mid": list_mid},
                            "body": [9007199254740991, 100],
                        })
                        list_response = await asyncio.wait_for(list_waiter, timeout=10)
                    finally:
                        self._direct_conversation_waiters.pop(list_mid, None)
                    cid = self._extract_existing_direct_conversation_cid(
                        list_response,
                        toid,
                        self.myid,
                        item_id,
                    )
                    if cid:
                        logger.info(f"【{self.cookie_id}】复用已有直接会话发送消息")
                if not cid:
                    cid = await self._find_direct_conversation_via_session_sync(
                        toid,
                        item_id,
                    )
                    if cid:
                        logger.info(f"【{self.cookie_id}】通过会话列表复用已有直接会话")
                if not cid:
                    raise DirectMessageNotSubmitted(
                        "direct conversation response did not include a conversation id"
                        + (f": {json.dumps(summary, ensure_ascii=False)}" if summary else "")
                    )
                if wait_for_response:
                    message_response = await self.send_msg(
                        websocket,
                        cid,
                        toid,
                        text,
                        wait_for_response=True,
                    )
                else:
                    await self.send_msg(websocket, cid, toid, text)
                    message_response = True
                self._remember_direct_conversation(toid, item_id, cid)
                logger.info(f'【{self.cookie_id}】send message')
                return message_response
            except asyncio.TimeoutError as exc:
                self.direct_send_init_error_count += 1
                raise DirectMessageNotSubmitted("direct conversation response timed out") from exc
            finally:
                self._direct_conversation_waiters.pop(request_mid, None)

    async def _create_websocket_connection(self, headers):
        """创建WebSocket连接，兼容不同版本的websockets库"""
        import websockets

        # 获取websockets版本用于调试
        websockets_version = getattr(websockets, '__version__', '未知')
        logger.warning(f"websockets库版本: {websockets_version}")

        # 三个兼容分支都必须带上超时，否则退化分支会悄悄回到无上限的连接
        timeout_kwargs = {
            'open_timeout': WS_OPEN_TIMEOUT,
            'close_timeout': WS_CLOSE_TIMEOUT,
            'ping_interval': WS_PING_INTERVAL,
            'ping_timeout': WS_PING_TIMEOUT,
        }

        try:
            # 尝试使用extra_headers参数
            return websockets.connect(
                self.base_url,
                extra_headers=headers,
                **timeout_kwargs
            )
        except Exception as e:
            # 捕获所有异常类型，不仅仅是TypeError
            error_msg = self._safe_str(e)
            logger.warning(f"extra_headers参数失败: {error_msg}")

            if "extra_headers" in error_msg or "unexpected keyword argument" in error_msg:
                logger.warning("websockets库不支持extra_headers参数，尝试additional_headers")
                # 使用additional_headers参数（较新版本）
                try:
                    return websockets.connect(
                        self.base_url,
                        additional_headers=headers,
                        **timeout_kwargs
                    )
                except Exception as e2:
                    error_msg2 = self._safe_str(e2)
                    logger.warning(f"additional_headers参数失败: {error_msg2}")

                    if "additional_headers" in error_msg2 or "unexpected keyword argument" in error_msg2:
                        # 如果都不支持，则不传递headers
                        logger.warning("websockets库不支持headers参数，使用基础连接模式")
                        return websockets.connect(self.base_url, **timeout_kwargs)
                    else:
                        raise e2
            else:
                raise e

    def is_chat_message(self, message):
        """判断是否为用户聊天消息"""
        try:
            return (
                isinstance(message, dict)
                and "1" in message
                and isinstance(message["1"], dict)
                and "10" in message["1"]
                and isinstance(message["1"]["10"], dict)
                and message_has_content(message)
            )
        except Exception:
            return False

    def is_sync_package(self, message_data):
        """判断是否为同步包消息"""
        try:
            return (
                isinstance(message_data, dict)
                and "body" in message_data
                and "syncPushPackage" in message_data["body"]
                and "data" in message_data["body"]["syncPushPackage"]
                and len(message_data["body"]["syncPushPackage"]["data"]) > 0
            )
        except Exception:
            return False

    async def _sync_account_profile(self):
        """缓存本账号在闲鱼的头像与昵称，供控制台账号卡片展示。

        控制台此前只能显示灰色占位头像——平台身份数据从未被采集。这里在账号连上
        之后用同一会话补一次只读的用户主页接口；每个实例最多成功一次（重新登录会
        新建实例，从而自然刷新），失败只记 debug 且不影响监听主流程。
        """
        if getattr(self, '_account_profile_synced', False):
            return
        token_source = trans_cookies(self.cookies_str).get('_m_h5_tk', '')
        token = token_source.split('_', 1)[0] if token_source else ''
        if not token or not self.myid:
            return

        api = 'mtop.idle.web.user.page.head'
        version = '1.0'
        timestamp = str(int(time.time() * 1000))
        data_value = json.dumps({'userId': str(self.myid)}, separators=(',', ':'))
        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': timestamp,
            'sign': generate_sign(timestamp, token, data_value),
            'v': version,
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': api,
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
        }
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cookie': self.cookies_str,
            'Origin': 'https://www.goofish.com',
            'Referer': 'https://www.goofish.com/',
            'User-Agent': self.browser_user_agent,
        }
        url = _resolve_h5_api_url(f'https://h5api.m.goofish.com/h5/{api}/{version}/')
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                cookie_jar=aiohttp.DummyCookieJar(),
            ) as session:
                async with session.post(
                    url,
                    params=params,
                    data={'data': data_value},
                    headers=headers,
                ) as response:
                    body = await response.json(content_type=None)
        except Exception as exc:
            logger.debug(f"【{self.cookie_id}】账号资料获取跳过: {type(exc).__name__}")
            return

        if not isinstance(body, dict):
            return
        module = (body.get('data') or {}).get('module')
        base = module.get('base') if isinstance(module, dict) else None
        if not isinstance(base, dict):
            return
        avatar_node = base.get('avatar')
        avatar_url = ''
        if isinstance(avatar_node, dict):
            avatar_url = str(avatar_node.get('avatar') or '').strip()
        elif isinstance(avatar_node, str):
            avatar_url = avatar_node.strip()
        # 平台返回 http:// 或协议相对地址；控制台走 HTTPS，必须升级协议，
        # 否则浏览器按混合内容拦截图片（alicdn 同域支持 https）。
        if avatar_url.startswith('//'):
            avatar_url = f'https:{avatar_url}'
        elif avatar_url.startswith('http://'):
            avatar_url = f'https://{avatar_url[len("http://"):]}'
        if avatar_url and not avatar_url.startswith('https://'):
            avatar_url = ''
        nickname = str(base.get('displayName') or '').strip()
        if not avatar_url and not nickname:
            return
        saved = await asyncio.to_thread(
            db_manager.update_account_profile,
            self.cookie_id,
            avatar_url,
            nickname,
        )
        if saved:
            self._account_profile_synced = True
            logger.info(
                f"【{self.cookie_id}】账号资料已缓存: avatar={bool(avatar_url)} nick={bool(nickname)}"
            )

    async def create_session(self):
        """创建aiohttp session"""
        if not self.session:
            # 创建带有cookies和headers的session
            headers = DEFAULT_HEADERS.copy()
            headers['cookie'] = self.cookies_str

            self.session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            )

    async def close_session(self):
        """关闭aiohttp session"""
        if self.session:
            await self.session.close()
            self.session = None

    async def _handle_message_with_semaphore(self, message_data, websocket):
        """带信号量的消息处理包装器，防止并发任务过多"""
        async with self.message_semaphore:
            self.active_message_tasks += 1
            try:
                await self.handle_message(message_data, websocket)
            finally:
                self.active_message_tasks -= 1
                # 定期记录活跃任务数（每100个任务记录一次）
                if self.active_message_tasks % 100 == 0 and self.active_message_tasks > 0:
                    logger.info(f"【{self.cookie_id}】当前活跃消息处理任务数: {self.active_message_tasks}")

    async def _websocket_reader_loop(self, websocket):
        """Own all reads for one account WebSocket, including startup pushes."""
        try:
            async for message in websocket:
                logger.info(
                    f"【{self.cookie_id}】收到WebSocket消息: "
                    f"{len(message) if message else 0} 字节"
                )
                try:
                    message_data = json.loads(message)
                    lwp = str(message_data.get("lwp") or "")
                    is_server_push = lwp.startswith("/s/")
                    if is_server_push and getattr(self, "_websocket_bootstrap_active", False):
                        acknowledged = await self._send_message_ack(
                            message_data,
                            websocket,
                        )
                        if not acknowledged:
                            self._websocket_bootstrap_error = ConnectionError(
                                "websocket bootstrap ACK failed"
                            )
                            return
                        await self.handle_message(
                            message_data,
                            websocket,
                            acknowledge=False,
                        )
                        if lwp == "/s/sync":
                            sync_event = getattr(
                                self,
                                "_websocket_bootstrap_sync_event",
                                None,
                            )
                            if sync_event is not None:
                                sync_event.set()
                        continue

                    if self._resolve_direct_conversation_response(message_data):
                        continue
                    if getattr(self, "_websocket_bootstrap_active", False):
                        if await self.handle_heartbeat_response(message_data):
                            continue
                        logger.debug(
                            "【{}】忽略未匹配的初始化响应: lwp={}",
                            self.cookie_id,
                            lwp or "response",
                        )
                        continue
                    if await self.handle_heartbeat_response(message_data):
                        continue
                    self._create_tracked_task(
                        self._handle_message_with_semaphore(message_data, websocket)
                    )
                except Exception as exc:
                    logger.error(f"处理消息出错: {self._safe_str(exc)}")
                    if getattr(self, "_websocket_bootstrap_active", False):
                        self._websocket_bootstrap_error = ConnectionError(
                            f"websocket bootstrap frame failed: {type(exc).__name__}"
                        )
                        return
        finally:
            if getattr(self, "_websocket_bootstrap_active", False):
                if self._websocket_bootstrap_error is None:
                    self._websocket_bootstrap_error = ConnectionError(
                        "account websocket closed during bootstrap"
                    )
                sync_event = getattr(
                    self,
                    "_websocket_bootstrap_sync_event",
                    None,
                )
                if sync_event is not None:
                    sync_event.set()
            self._fail_direct_conversation_waiters("account websocket closed")

    def _extract_message_id(self, message_data: dict) -> str:
        """
        从消息数据中提取消息ID，用于去重

        Args:
            message_data: 原始消息数据

        Returns:
            消息ID字符串，如果无法提取则返回None
        """
        try:
            # 尝试从 message['1']['10']['bizTag'] 中提取 messageId
            if isinstance(message_data, dict) and "1" in message_data:
                message_1 = message_data.get("1")
                if isinstance(message_1, dict) and "10" in message_1:
                    message_10 = message_1.get("10")
                    if isinstance(message_10, dict) and "bizTag" in message_10:
                        biz_tag = message_10.get("bizTag", "")
                        if isinstance(biz_tag, str):
                            # bizTag 是 JSON 字符串，格式如: '{"sourceId":"S:1","messageId":"984f323c719d4cd0a7b993a0769a33b6"}'
                            try:
                                import json
                                biz_tag_dict = json.loads(biz_tag)
                                if isinstance(biz_tag_dict, dict) and "messageId" in biz_tag_dict:
                                    return biz_tag_dict.get("messageId")
                            except (json.JSONDecodeError, TypeError):
                                pass

                        # 如果 bizTag 解析失败，尝试从 extJson 中提取
                        if "extJson" in message_10:
                            ext_json = message_10.get("extJson", "")
                            if isinstance(ext_json, str):
                                try:
                                    import json
                                    ext_json_dict = json.loads(ext_json)
                                    if isinstance(ext_json_dict, dict) and "messageId" in ext_json_dict:
                                        return ext_json_dict.get("messageId")
                                except (json.JSONDecodeError, TypeError):
                                    pass
        except Exception as e:
            logger.debug(f"【{self.cookie_id}】提取消息ID失败: {self._safe_str(e)}")

        return None

    async def _schedule_debounced_reply(self, chat_id: str, message_data: dict, websocket,
                                       send_user_name: str, send_user_id: str, send_message: str,
                                       item_id: str, msg_time: str, image_refs=None,
                                       order_id: str = None):
        """
        调度防抖回复：如果用户连续发送消息，等待用户停止发送后再回复最后一条消息

        Args:
            chat_id: 聊天ID
            message_data: 原始消息数据
            websocket: WebSocket连接
            send_user_name: 发送者用户名
            send_user_id: 发送者用户ID
            send_message: 消息内容
            item_id: 商品ID
            msg_time: 消息时间
        """
        # 提取消息ID并检查是否已处理
        message_id = self._extract_message_id(message_data)
        # 如果没有 messageId，使用备用标识（chat_id + send_message + 时间戳）
        if not message_id:
            try:
                # 尝试从消息数据中提取时间戳
                create_time = 0
                if isinstance(message_data, dict) and "1" in message_data:
                    message_1 = message_data.get("1")
                    if isinstance(message_1, dict):
                        create_time = message_1.get("5", 0)
                # 使用组合键作为备用标识
                message_id = f"{chat_id}_{send_message}_{create_time}"
            except Exception:
                # 如果提取失败，使用当前时间戳
                message_id = f"{chat_id}_{send_message}_{int(time.time() * 1000)}"

        async with self.processed_message_ids_lock:
            current_time = time.time()

            # 检查消息是否已处理且未过期
            if message_id in self.processed_message_ids:
                last_process_time = self.processed_message_ids[message_id]
                time_elapsed = current_time - last_process_time

                # 如果消息处理时间未超过1小时，跳过
                if time_elapsed < self.message_expire_time:
                    remaining_time = int(self.message_expire_time - time_elapsed)
                    logger.warning(
                        f"【{self.cookie_id}】消息已处理过，距离可重复回复还需 {remaining_time} 秒"
                    )
                    return
                else:
                    # 超过1小时，可以重新处理
                    logger.info(
                        f"【{self.cookie_id}】消息去重记录已超过 {int(time_elapsed/60)} 分钟，允许重新回复"
                    )

            # 标记消息ID为已处理（更新或添加时间戳）
            self.processed_message_ids[message_id] = current_time

            # 定期清理过期的消息ID
            if len(self.processed_message_ids) > self.processed_message_ids_max_size:
                # 清理超过1小时的旧记录
                expired_ids = [
                    msg_id for msg_id, timestamp in self.processed_message_ids.items()
                    if current_time - timestamp > self.message_expire_time
                ]

                for msg_id in expired_ids:
                    del self.processed_message_ids[msg_id]

                logger.info(f"【{self.cookie_id}】已清理 {len(expired_ids)} 个过期消息ID")

                # 如果清理后仍然过大，删除最旧的一半
                if len(self.processed_message_ids) > self.processed_message_ids_max_size:
                    sorted_ids = sorted(self.processed_message_ids.items(), key=lambda x: x[1])
                    remove_count = len(sorted_ids) // 2
                    for msg_id, _ in sorted_ids[:remove_count]:
                        del self.processed_message_ids[msg_id]
                    logger.info(f"【{self.cookie_id}】消息ID去重字典过大，已清理 {remove_count} 个最旧记录")

        async with self.message_debounce_lock:
            # 如果该chat_id已有防抖任务，取消它
            if chat_id in self.message_debounce_tasks:
                old_task = self.message_debounce_tasks[chat_id].get('task')
                if old_task and not old_task.done():
                    old_task.cancel()
                    logger.warning(f"【{self.cookie_id}】取消旧防抖任务")

            # 更新最后一条消息信息
            current_timer = time.time()
            self.message_debounce_tasks[chat_id] = {
                'last_message': {
                    'message_data': message_data,
                    'websocket': websocket,
                    'send_user_name': send_user_name,
                    'send_user_id': send_user_id,
                    'send_message': send_message,
                    'item_id': item_id,
                    'msg_time': msg_time,
                    'image_refs': image_refs or (),
                    'order_id': order_id,
                },
                'timer': current_timer
            }

            # 创建新的防抖任务
            async def debounce_task():
                saved_timer = current_timer  # 保存创建任务时的时间戳
                try:
                    # 等待防抖延迟时间
                    await asyncio.sleep(self.message_debounce_delay)

                    # 检查是否仍然是最新的消息（防止在等待期间有新消息）
                    async with self.message_debounce_lock:
                        if chat_id not in self.message_debounce_tasks:
                            return

                        debounce_info = self.message_debounce_tasks[chat_id]
                        # 检查时间戳是否匹配（确保这是最新的消息）
                        if saved_timer != debounce_info['timer']:
                            logger.warning(f"【{self.cookie_id}】防抖期间有新消息，跳过旧消息处理")
                            return

                        # 获取最后一条消息
                        last_msg = debounce_info['last_message']

                        # 从防抖任务中移除
                        del self.message_debounce_tasks[chat_id]

                    # 处理最后一条消息
                    logger.info(
                        f"【{self.cookie_id}】防抖延迟结束，开始处理最后一条消息: "
                        f"message_length={len(last_msg['send_message'] or '')}"
                    )
                    await self._process_chat_message_reply(
                        last_msg['message_data'],
                        last_msg['websocket'],
                        last_msg['send_user_name'],
                        last_msg['send_user_id'],
                        last_msg['send_message'],
                        last_msg['item_id'],
                        chat_id,
                        last_msg['msg_time'],
                        last_msg.get('image_refs'),
                        last_msg.get('order_id'),
                    )

                except asyncio.CancelledError:
                    logger.warning(f"【{self.cookie_id}】防抖任务被取消")
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】处理防抖回复时发生错误: {self._safe_str(e)}")
                    # 确保从防抖任务中移除
                    async with self.message_debounce_lock:
                        if chat_id in self.message_debounce_tasks:
                            del self.message_debounce_tasks[chat_id]

            task = self._create_tracked_task(debounce_task())
            self.message_debounce_tasks[chat_id]['task'] = task
            logger.warning(f"【{self.cookie_id}】创建防抖任务，延迟 {self.message_debounce_delay} 秒")

    async def _process_chat_message_reply(self, message_data: dict, websocket, send_user_name: str,
                                         send_user_id: str, send_message: str, item_id: str,
                                         chat_id: str, msg_time: str, image_refs=None,
                                         order_id: str = None):
        """
        处理聊天消息的回复逻辑（从handle_message中提取出来的核心回复逻辑）

        Args:
            message_data: 原始消息数据
            websocket: WebSocket连接
            send_user_name: 发送者用户名
            send_user_id: 发送者用户ID
            send_message: 消息内容
            item_id: 商品ID
            chat_id: 聊天ID
            msg_time: 消息时间
        """
        reply = None
        reply_source = ''
        shadow_scheduled = False
        try:
            # 自动回复消息
            if not AUTO_REPLY.get('enabled', True):
                logger.info(f"[{msg_time}] 【{self.cookie_id}】【系统】自动回复已禁用")
                return

            # 检查该chat_id是否处于暂停状态
            if pause_manager.is_chat_paused(chat_id, self.cookie_id):
                remaining_time = pause_manager.get_remaining_pause_time(chat_id, self.cookie_id)
                remaining_minutes = remaining_time // 60
                remaining_seconds = remaining_time % 60
                logger.info(f"[{msg_time}] 【{self.cookie_id}】【系统】自动回复已暂停，剩余时间: {remaining_minutes}分{remaining_seconds}秒")
                return

            # 按关键词、AI、默认回复的顺序处理；旧的未认证内部 API
            # 不再接收聊天、账号或商品标识。
            if not reply:
                # 1. 首先尝试关键词匹配（传入商品ID）
                reply = None if image_refs else await self.get_keyword_reply(
                    send_user_name, send_user_id, send_message, item_id
                )
                if reply == "EMPTY_REPLY":
                    # 匹配到关键词但回复内容为空，不进行任何回复
                    logger.info(f"[{msg_time}] 【{self.cookie_id}】匹配到空回复关键词，跳过自动回复")
                    return
                elif reply:
                    reply_source = '关键词'  # 标记为关键词回复
                else:
                    # 2. 关键词匹配失败，如果AI开关打开，尝试AI回复
                    if image_refs:
                        reply = await self.get_ai_reply(
                            send_user_name, send_user_id, send_message, item_id, chat_id,
                            image_refs=image_refs,
                        )
                    else:
                        reply = await self.get_ai_reply(
                            send_user_name, send_user_id, send_message, item_id, chat_id,
                        )
                    if reply:
                        reply_source = 'AI'  # 标记为AI回复
                    else:
                        # 3. 最后使用默认回复
                        default_reply_result = await self.get_default_reply(send_user_name, send_user_id, send_message, chat_id, item_id)
                        if default_reply_result == "EMPTY_REPLY":
                            # 默认回复内容为空，不进行任何回复
                            logger.info(f"[{msg_time}] 【{self.cookie_id}】默认回复内容为空，跳过自动回复")
                            return

                        # 处理默认回复（可能包含图片和文字）
                        if default_reply_result and isinstance(default_reply_result, dict):
                            reply_source = '默认'  # 标记为默认回复
                            default_image_url = default_reply_result.get('image_url')
                            default_text = default_reply_result.get('text')

                            # 如果存在图片，先发送图片
                            if default_image_url:
                                try:
                                    # 处理图片URL（上传到CDN如果需要）
                                    final_image_url = default_image_url
                                    image_width, image_height = 800, 600  # 默认尺寸

                                    if self._is_cdn_url(default_image_url):
                                        # 已经是CDN链接，获取真实尺寸
                                        logger.info(f"【{self.cookie_id}】默认回复使用已上传图片")
                                        width, height = await self._get_image_size_from_url(default_image_url)
                                        if width and height:
                                            image_width, image_height = width, height
                                    elif default_image_url.startswith('/static/uploads/') or default_image_url.startswith('static/uploads/'):
                                        # 本地图片，需要上传到闲鱼CDN
                                        local_image_path = default_image_url.replace('/static/uploads/', 'static/uploads/')
                                        if os.path.exists(local_image_path):
                                            logger.info(f"【{self.cookie_id}】准备上传默认回复本地图片到闲鱼CDN: {local_image_path}")

                                            from utils.image_uploader import ImageUploader
                                            uploader = ImageUploader(self.cookies_str)

                                            async with uploader:
                                                cdn_url = await uploader.upload_image(local_image_path)
                                                if cdn_url:
                                                    logger.info(f"【{self.cookie_id}】默认回复图片上传成功")
                                                    final_image_url = cdn_url

                                                    # 更新数据库中的图片URL为CDN URL
                                                    await self._update_default_reply_image_url(cdn_url)

                                                    # 获取实际图片尺寸
                                                    from utils.image_utils import image_manager
                                                    try:
                                                        actual_width, actual_height = image_manager.get_image_size(local_image_path)
                                                        if actual_width and actual_height:
                                                            image_width, image_height = actual_width, actual_height
                                                    except Exception as e:
                                                        logger.warning(f"【{self.cookie_id}】获取图片尺寸失败，使用默认尺寸: {e}")
                                                else:
                                                    logger.error(f"【{self.cookie_id}】默认回复图片上传失败: {local_image_path}")
                                                    final_image_url = None
                                        else:
                                            logger.error(f"【{self.cookie_id}】默认回复本地图片文件不存在: {local_image_path}")
                                            final_image_url = None
                                    else:
                                        # 其他类型的URL，获取真实尺寸
                                        width, height = await self._get_image_size_from_url(default_image_url)
                                        if width and height:
                                            image_width, image_height = width, height

                                    # 发送图片
                                    if final_image_url:
                                        await self.send_image_msg(websocket, chat_id, send_user_id, final_image_url, image_width, image_height)
                                        msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                                        logger.info(f"[{msg_time}] 【{reply_source}图片发出】发送成功")
                                except Exception as e:
                                    logger.error(f"【{self.cookie_id}】默认回复图片发送失败: {self._safe_str(e)}")

                            # 然后发送文字（如果有）
                            if default_text and default_text.strip():
                                reply = default_text
                            else:
                                # 只有图片没有文字，已经发送完毕
                                if default_image_url:
                                    self._schedule_ai_shadow_reply(
                                        send_user_id, send_message, item_id, chat_id,
                                        image_refs=image_refs, order_id=order_id,
                                    )
                                    return
                                reply = None
                        else:
                            reply = None

            # 注意：这里只有商品ID，没有标题和详情，根据新的规则不保存到数据库
            # 商品信息会在其他有完整信息的地方保存（如发货规则匹配时）
            # 如果有回复内容，发送消息
            if reply:
                # 检查是否是图片发送标记
                if reply.startswith("__IMAGE_SEND__"):
                    # 提取图片URL（关键词回复不包含卡券ID）
                    image_url = reply.replace("__IMAGE_SEND__", "")
                    # 发送图片消息
                    try:
                        await self.send_image_msg(websocket, chat_id, send_user_id, image_url)
                        if reply_source == "AI":
                            await self._mark_ai_reply_delivery(
                                chat_id, item_id, reply, 'ambiguous',
                            )
                        # 记录发出的图片消息
                        msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                        logger.info(f"[{msg_time}] 【{reply_source}图片发出】发送成功")
                    except Exception as e:
                        # 图片发送失败，发送错误提示
                        if reply_source == "AI":
                            await self._mark_ai_reply_delivery(
                                chat_id, item_id, reply, 'ambiguous',
                            )
                        logger.error(f"图片发送失败: {self._safe_str(e)}")
                        await self.send_msg(websocket, chat_id, send_user_id, "抱歉，图片发送失败，请稍后重试。")
                        msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                        logger.error(f"[{msg_time}] 【{reply_source}图片发送失败】stage=send_image")
                else:
                    # 普通文本消息
                    await self.send_msg(websocket, chat_id, send_user_id, reply)
                    if reply_source == "AI":
                        await self._mark_ai_reply_delivery(
                            chat_id, item_id, reply, 'ambiguous',
                        )
                    # 记录发出的消息
                    msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    logger.info(
                        f"[{msg_time}] 【{reply_source}发出】发送成功: "
                        f"reply_length={len(reply)}"
                    )
                    if reply_source == "AI":
                        self.last_ai_result = "sent"
            else:
                msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                logger.info(f"[{msg_time}] 【{self.cookie_id}】【系统】未找到匹配的回复规则，不回复")
            shadow_scheduled = True
            self._schedule_ai_shadow_reply(
                send_user_id, send_message, item_id, chat_id,
                image_refs=image_refs, order_id=order_id,
                sent_reply=reply, reply_source=reply_source,
            )
        except Exception as e:
            if reply_source == "AI" and reply:
                await self._mark_ai_reply_delivery(
                    chat_id, item_id, reply, 'ambiguous',
                )
            if reply and not shadow_scheduled:
                shadow_scheduled = True
                try:
                    self._schedule_ai_shadow_reply(
                        send_user_id, send_message, item_id, chat_id,
                        image_refs=image_refs, order_id=order_id,
                        sent_reply=reply, reply_source=reply_source,
                    )
                except Exception as shadow_error:
                    logger.warning(
                        "Shadow 任务调度失败: error_type={}",
                        type(shadow_error).__name__,
                    )
            if self.last_ai_result == "generated":
                self.last_ai_result = f"send_error:{type(e).__name__}"
            logger.error(f"处理聊天消息回复时发生错误: {self._safe_str(e)}")

    async def _send_message_ack(self, message_data, websocket) -> bool:
        """Acknowledge one pushed frame and report whether it reached the socket."""
        try:
            headers = message_data.get("headers", {})
            ack = {
                "code": 200,
                "headers": {
                    "mid": headers.get("mid", generate_mid()),
                    "sid": headers.get("sid", ""),
                },
            }
            for header_name in ("app-key", "ua", "dt"):
                if header_name in headers:
                    ack["headers"][header_name] = headers[header_name]
            await self._ws_send_guarded(websocket, ack)
            return True
        except Exception as exc:
            self.message_ack_error_count += 1
            logger.debug(
                "【{}】消息 ACK 发送失败: error_type={}",
                self.cookie_id,
                type(exc).__name__,
            )
            return False

    async def handle_message(self, message_data, websocket, acknowledge: bool = True):
        """处理所有类型的消息"""
        try:
            # 检查账号是否启用
            from cookie_manager import manager as cookie_manager
            if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                logger.warning(f"【{self.cookie_id}】账号已禁用，跳过消息处理")
                return

            if acknowledge:
                await self._send_message_ack(message_data, websocket)

            # 如果不是同步包消息，直接返回
            if not self.is_sync_package(message_data):
                # 添加调试日志，记录非同步包消息
                logger.debug(f"【{self.cookie_id}】非同步包消息，跳过处理")
                return

            # 获取并解密数据
            sync_data = message_data["body"]["syncPushPackage"]["data"][0]

            # 检查是否有必要的字段
            if "data" not in sync_data:
                logger.warning("同步包中无data字段")
                return

            # 解密数据
            message = None
            try:
                data = sync_data["data"]
                try:
                    data = base64.b64decode(data).decode("utf-8")
                    parsed_data = json.loads(data)
                    # 处理未加密的消息（如系统提示等）
                    msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    operation = parsed_data.get('operation') if isinstance(parsed_data, dict) else None
                    content = operation.get('content') if isinstance(operation, dict) else None
                    if isinstance(content, dict):
                        if 'sessionArouse' in content:
                            # 处理系统引导消息
                            logger.info(f"[{msg_time}] 【{self.cookie_id}】【系统】小闲鱼智能提示:")
                            if 'arouseChatScriptInfo' in content['sessionArouse']:
                                for qa in content['sessionArouse']['arouseChatScriptInfo']:
                                    logger.info(f"  - {qa['chatScrip']}")
                            return
                        message = normalize_operation_message(parsed_data)
                        if message is None:
                            content_keys = sorted(str(key) for key in content.keys())
                            logger.warning(
                                f"[{msg_time}] 【{self.cookie_id}】【系统】"
                                f"其他类型消息: keys={content_keys}"
                            )
                            return
                    else:
                        # 如果不是系统消息，将解析的数据作为message
                        message = parsed_data
                except Exception as e:
                    # 如果JSON解析失败，尝试解密
                    decrypted_data = decrypt(data)
                    message = json.loads(decrypted_data)
                    normalized = normalize_operation_message(message) if isinstance(message, dict) else None
                    if normalized:
                        message = normalized
            except Exception as e:
                logger.error(f"消息解密失败: {self._safe_str(e)}")
                return

            # 确保message不为空
            if message is None:
                logger.error("消息解析后为空")
                return

            # 确保message是字典类型
            if not isinstance(message, dict):
                logger.error(f"消息格式错误，期望字典但得到: {type(message)}")
                return

            early_message_1 = message.get("1")
            early_message_10 = (
                early_message_1.get("10")
                if isinstance(early_message_1, dict)
                else None
            )
            early_sender_id = (
                str(early_message_10.get("senderUserId") or "").strip()
                if isinstance(early_message_10, dict)
                else ""
            )
            early_content = extract_inbound_content(message)
            early_text = early_content.text or (
                str(early_message_10.get("reminderContent") or "").strip()
                if isinstance(early_message_10, dict)
                else ""
            )
            if (
                early_sender_id != str(self.myid or "").strip()
                and "".join(early_text.split()) == "开通留资卡功能建联更安全"
            ):
                logger.info(f"【{self.cookie_id}】留资推广卡在消息入口丢弃")
                return

            # 【消息接收标识】记录收到消息的时间，用于控制Cookie刷新
            self.last_message_received_time = time.time()
            logger.warning(f"【{self.cookie_id}】收到消息，更新消息接收时间标识")

            # 【优先处理】只提取订单ID并关联持久化状态事件；未验真详情适配器保持关闭。
            order_id = None
            try:
                order_id = self._extract_order_id(message)
                if order_id:
                    msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】检测到订单ID并关联状态事件')

                    # 通知订单状态处理器订单ID已提取
                    if self.order_status_handler:
                        logger.info(f"【{self.cookie_id}】准备调用订单状态处理器.on_order_id_extracted: {order_id}")
                        try:
                            self.order_status_handler.on_order_id_extracted(order_id, self.cookie_id, message)
                            logger.info(f"【{self.cookie_id}】订单状态事件关联完成")
                        except Exception as e:
                            logger.error(f"【{self.cookie_id}】通知订单状态处理器订单ID提取失败: {self._safe_str(e)}")
                    else:
                        logger.warning(f"【{self.cookie_id}】订单状态处理器未初始化，跳过事件关联")
                else:
                    logger.warning(f"【{self.cookie_id}】未检测到订单ID")
            except Exception as e:
                logger.error(f"【{self.cookie_id}】提取订单ID失败: {self._safe_str(e)}")

            # 安全地获取用户ID
            user_id = None
            try:
                message_1 = message.get("1")
                if isinstance(message_1, str) and '@' in message_1:
                    user_id = message_1.split('@')[0]
                elif isinstance(message_1, dict):
                    # 如果message['1']是字典，从message["1"]["10"]["senderUserId"]中提取user_id
                    if "10" in message_1 and isinstance(message_1["10"], dict):
                        user_id = message_1["10"].get("senderUserId", "unknown_user")
                    else:
                        user_id = "unknown_user"
                else:
                    user_id = "unknown_user"
            except Exception as e:
                logger.warning(f"提取用户ID失败: {self._safe_str(e)}")
                user_id = "unknown_user"



            # 安全地提取商品ID
            item_id = None
            try:
                if "1" in message and isinstance(message["1"], dict) and "10" in message["1"] and isinstance(message["1"]["10"], dict):
                    url_info = message["1"]["10"].get("reminderUrl", "")
                    if isinstance(url_info, str) and "itemId=" in url_info:
                        item_id = url_info.split("itemId=")[1].split("&")[0]

                # 如果没有提取到，使用辅助方法
                if not item_id:
                    item_id = self.extract_item_id_from_message(message)

                if not item_id:
                    item_id = f"auto_{user_id}_{int(time.time())}"
                    logger.warning(f"无法提取商品ID，使用默认值: {item_id}")

            except Exception as e:
                logger.error(f"提取商品ID时发生错误: {self._safe_str(e)}")
                item_id = f"auto_{user_id}_{int(time.time())}"
            # 处理订单状态消息
            try:
                logger.debug(
                    f"【{self.cookie_id}】解析订单状态消息: "
                    f"keys={len(message) if isinstance(message, dict) else 0}"
                )
                msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

                # 安全地检查订单状态
                red_reminder = None
                if isinstance(message, dict) and "3" in message and isinstance(message["3"], dict):
                    red_reminder = message["3"].get("redReminder")

                if red_reminder == '等待买家付款':
                    logger.info(f'[{msg_time}] 【系统】订单状态为等待买家付款')
                    return
                elif red_reminder == '交易关闭':
                    logger.info(f'[{msg_time}] 【系统】订单状态为交易关闭')
                    return
                elif red_reminder == '等待卖家发货':
                    logger.info(f'[{msg_time}] 【系统】订单状态为等待卖家发货')
                    # return
            except:
                pass

            # 判断是否为聊天消息
            if not self.is_chat_message(message):
                logger.warning("非聊天消息")
                return

            # 处理聊天消息
            try:
                # 安全地提取聊天消息信息
                if not (isinstance(message, dict) and "1" in message and isinstance(message["1"], dict)):
                    logger.error("消息格式错误：缺少必要的字段结构")
                    return

                message_1 = message["1"]
                if not isinstance(message_1.get("10"), dict):
                    logger.error("消息格式错误：缺少消息详情字段")
                    return

                create_time = int(message_1.get("5", 0))
                message_10 = message_1["10"]
                send_user_name = message_10.get("senderNick", message_10.get("reminderTitle", "未知用户"))
                send_user_id = message_10.get("senderUserId", "unknown")
                inbound_content = extract_inbound_content(message)
                image_refs = inbound_content.images
                send_message = inbound_content.text or message_10.get("reminderContent", "")
                if not send_message and image_refs:
                    send_message = IMAGE_PLACEHOLDER

                chat_id_raw = message_1.get("2", "")
                chat_id = chat_id_raw.split('@')[0] if '@' in str(chat_id_raw) else str(chat_id_raw)

            except Exception as e:
                logger.error(f"提取聊天消息信息失败: {self._safe_str(e)}")
                return

            # 格式化消息时间
            msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(create_time/1000))



            # 判断消息方向
            if send_user_id == self.myid:
                logger.info(
                    f"[{msg_time}] 【手动发出】message_length={len(send_message or '')}"
                )

                # 暂停该chat_id的自动回复10分钟
                pause_manager.pause_chat(chat_id, self.cookie_id)

                if send_message:
                    self._create_tracked_task(
                        self._record_seller_human_message(
                            chat_id, item_id, send_message,
                        )
                    )

                return
            else:
                observed_at = create_time / 1000 if create_time > 0 else time.time()
                _upsert_realtime_customer_profile(
                    database=db_manager,
                    cookie_id=self.cookie_id,
                    sender_user_id=send_user_id,
                    sender_nickname=send_user_name,
                    observed_at=observed_at,
                )
                logger.info(
                    f"[{msg_time}] 【收到客户消息】"
                    f"message_length={len(send_message or '')}, "
                    f"item_present={bool(item_id)}"
                )
                self.last_inbound_at = time.time()
                self.last_inbound_kind = "customer_chat"

            # 【优先处理】使用订单状态处理器处理系统消息
            if self.order_status_handler:
                try:
                    # 处理系统消息的订单状态更新
                    try:
                        handled = self.order_status_handler.handle_system_message(
                            message=message,
                            send_message=send_message,
                            cookie_id=self.cookie_id,
                            msg_time=msg_time
                        )
                    except Exception as e:
                        logger.error(f"【{self.cookie_id}】处理系统消息失败: {self._safe_str(e)}")
                        handled = False

                    # 处理红色提醒消息
                    if not handled:
                        try:
                            if isinstance(message, dict) and "3" in message and isinstance(message["3"], dict):
                                red_reminder = message["3"].get("redReminder")
                                user_id = message["3"].get("userId", "unknown")

                                if red_reminder:
                                    try:
                                        self.order_status_handler.handle_red_reminder_message(
                                            message=message,
                                            red_reminder=red_reminder,
                                            user_id=user_id,
                                            cookie_id=self.cookie_id,
                                            msg_time=msg_time
                                        )
                                    except Exception as e:
                                        logger.error(f"【{self.cookie_id}】处理红色提醒消息失败: {self._safe_str(e)}")
                        except Exception as red_e:
                            logger.warning(f"处理红色提醒消息失败: {self._safe_str(red_e)}")

                except Exception as e:
                    logger.error(f"订单状态处理失败: {self._safe_str(e)}")

            # 【优先处理】检查系统消息和自动发货触发消息（不受人工接入暂停影响）
            if send_message == '[我已拍下，待付款]':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】系统消息不处理')
                return
            elif send_message == '[你关闭了订单，钱款已原路退返]':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】系统消息不处理')
                return
            elif send_message == '[不想宝贝被砍价?设置不砍价回复  ]':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】系统提示信息不处理')
                return
            elif send_message == 'AI正在帮你回复消息，不错过每笔订单':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】系统提示信息不处理')
                return
            elif send_message == '发来一条消息':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】系统通知消息不处理')
                return
            elif send_message == '发来一条新消息':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】系统通知消息不处理')
                return
            elif send_message == '[买家确认收货，交易成功]':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】交易完成消息不处理')
                return
            elif send_message == '快给ta一个评价吧~' or send_message == '快给ta一个评价吧～':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】评价提醒消息不处理')
                return
            elif send_message == '卖家人不错？送Ta闲鱼小红花':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】小红花提醒消息不处理')
                return
            elif send_message == '[你已确认收货，交易成功]':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】买家确认收货消息不处理')
                return
            elif send_message == '[你已发货]':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】发货确认消息不处理')
                return
            elif send_message == '已发货':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】发货确认消息不处理')
                return
            # 【重要】检查是否为自动发货触发消息 - 即使在人工接入暂停期间也要处理
            elif self._is_auto_delivery_trigger(send_message):
                logger.info(f'[{msg_time}] 【{self.cookie_id}】检测到自动发货触发事件')
                # 使用统一的自动发货处理方法
                await self._handle_auto_delivery(websocket, message, send_user_name, send_user_id,
                                               item_id, chat_id, msg_time)
                return
            # 【重要】检查是否为"我已小刀，待刀成"卡片消息 - 即使在人工接入暂停期间也要处理
            elif send_message == '[卡片消息]':
                # 检查是否为"我已小刀，待刀成"的卡片消息
                try:
                    # 从消息中提取卡片内容
                    card_title = None
                    if isinstance(message, dict) and "1" in message and isinstance(message["1"], dict):
                        message_1 = message["1"]
                        if "6" in message_1 and isinstance(message_1["6"], dict):
                            message_6 = message_1["6"]
                            if "3" in message_6 and isinstance(message_6["3"], dict):
                                message_6_3 = message_6["3"]
                                if "5" in message_6_3:
                                    # 解析JSON内容
                                    try:
                                        card_content = json.loads(message_6_3["5"])
                                        if "dxCard" in card_content and "item" in card_content["dxCard"]:
                                            card_item = card_content["dxCard"]["item"]
                                            if "main" in card_item and "exContent" in card_item["main"]:
                                                ex_content = card_item["main"]["exContent"]
                                                card_title = ex_content.get("title", "")
                                    except (json.JSONDecodeError, KeyError) as e:
                                        logger.warning(f"解析卡片消息失败: {e}")

                    # 检查是否为"我已小刀，待刀成"
                    if card_title == "我已小刀，待刀成":
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】【系统】检测到"我已小刀，待刀成"，即使在暂停期间也继续处理')

                        # 检查商品是否属于当前cookies
                        if item_id and item_id != "未知商品":
                            try:
                                item_info = db_manager.get_item_info(self.cookie_id, item_id)
                                if not item_info:
                                    logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 商品 {item_id} 不属于当前账号，跳过免拼发货')
                                    return
                                logger.warning(f'[{msg_time}] 【{self.cookie_id}】✅ 商品 {item_id} 归属验证通过')
                            except Exception as e:
                                logger.error(f'[{msg_time}] 【{self.cookie_id}】检查商品归属失败: {self._safe_str(e)}，跳过免拼发货')
                                return

                        # 提取订单ID
                        order_id = self._extract_order_id(message)
                        if not order_id:
                            logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 未能提取到订单ID，无法执行免拼发货')
                            return

                        # 更新订单的is_bargain字段为True（标记为小刀订单）
                        try:
                            db_manager.insert_or_update_order(
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=send_user_id,
                                cookie_id=self.cookie_id,
                                is_bargain=True,
                                chat_id=chat_id
                            )
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】✅ 订单 {order_id} 已标记为小刀订单')
                        except Exception as e:
                            logger.error(f'[{msg_time}] 【{self.cookie_id}】标记小刀订单失败: {self._safe_str(e)}')

                        # Let the unified delivery path perform the real-time
                        # payment/identity gate before any platform action.
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】交由统一履约门禁处理免拼发货')
                        await asyncio.sleep(2)
                        await self._handle_auto_delivery(websocket, message, send_user_name, send_user_id,
                                                       item_id, chat_id, msg_time,
                                                       delivery_source=AUTO_DELIVERY_SOURCE_BARGAIN_FREESHIPPING)
                        return
                    else:
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】收到卡片消息，标题: {card_title or "未知"}')
                        # 如果不是目标卡片消息，继续正常处理流程（会受到暂停影响）

                except Exception as e:
                    logger.error(f"处理卡片消息异常: {self._safe_str(e)}")
                    # 如果处理异常，继续正常处理流程（会受到暂停影响）

            # 使用防抖机制处理聊天消息回复
            # 如果用户连续发送消息，等待用户停止发送后再回复最后一条消息
            await self._schedule_debounced_reply(
                chat_id=chat_id,
                message_data=message_data,
                websocket=websocket,
                send_user_name=send_user_name,
                send_user_id=send_user_id,
                send_message=send_message,
                item_id=item_id,
                msg_time=msg_time,
                image_refs=image_refs,
                order_id=order_id,
            )

        except Exception as e:
            logger.error(f"处理消息时发生错误: {self._safe_str(e)}")
            logger.warning(f"原始消息已省略，类型: {type(message_data).__name__}")

    async def main(self):
        """主程序入口"""
        try:
            logger.info(f"【{self.cookie_id}】开始启动XianyuLive主程序...")
            await self.create_session()  # 创建session
            logger.info(f"【{self.cookie_id}】Session创建完成，开始WebSocket连接循环...")

            while True:
                try:
                    # 检查账号是否启用
                    from cookie_manager import manager as cookie_manager
                    if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止主循环")
                        break

                    refresh_status = (
                        db_manager.get_account_session_refresh(self.cookie_id) or {}
                    )
                    if self._session_refresh_blocks_listener(refresh_status):
                        self._set_connection_state(
                            ConnectionState.DISCONNECTED,
                            "等待人工验证会话",
                        )
                        await self._interruptible_sleep(30)
                        continue

                    headers = WEBSOCKET_HEADERS.copy()
                    headers['Cookie'] = self.cookies_str
                    for header_name in list(headers):
                        if header_name.lower() == "user-agent":
                            headers.pop(header_name, None)
                    headers["User-Agent"] = self.browser_user_agent

                    # 更新连接状态为连接中
                    self._set_connection_state(ConnectionState.CONNECTING, "准备建立WebSocket连接")
                    logger.info(f"【{self.cookie_id}】WebSocket目标地址: {self.base_url}")

                    # 兼容不同版本的websockets库
                    async with await self._create_websocket_connection(headers) as websocket:
                        self.ws = websocket
                        logger.info(f"【{self.cookie_id}】WebSocket连接建立成功，开始初始化...")
                        websocket_reader_task = None

                        try:
                            self._websocket_bootstrap_active = True
                            self._websocket_bootstrap_error = None
                            websocket_reader_task = asyncio.create_task(
                                self._websocket_reader_loop(websocket)
                            )
                            # 开始初始化
                            await self.init(websocket)
                            self._websocket_bootstrap_active = False
                            logger.info(f"【{self.cookie_id}】WebSocket初始化完成！")

                            # 初始化完成后才设置为已连接状态
                            self._set_connection_state(ConnectionState.CONNECTED, "初始化完成，连接就绪")
                            self.connection_failures = 0
                            self.last_successful_connection = time.time()
                            self.connected_at = self.last_successful_connection

                            # 会话可用后补一次账号头像/昵称缓存（失败不影响监听）
                            await self._sync_account_profile()

                            # 记录后台任务启动前的状态
                            logger.warning(f"【{self.cookie_id}】准备启动后台任务 - 当前状态: heartbeat={self.heartbeat_task}, token_refresh={self.token_refresh_task}, cleanup={self.cleanup_task}, cookie_refresh={self.cookie_refresh_task}")

                            # 如果存在心跳任务引用，先清理（心跳任务依赖WebSocket，必须重启）
                            if self.heartbeat_task:
                                logger.warning(f"【{self.cookie_id}】检测到旧心跳任务引用，先清理...")
                                self._reset_background_tasks()

                            # 启动心跳任务（依赖WebSocket，每次重连都需要重启）
                            logger.info(f"【{self.cookie_id}】启动心跳任务...")
                            self.heartbeat_task = asyncio.create_task(self.heartbeat_loop(websocket))

                            # 启动其他后台任务（不依赖WebSocket，只在首次连接时启动）
                            tasks_started = []

                            if not self.token_refresh_task or self.token_refresh_task.done():
                                logger.info(f"【{self.cookie_id}】启动Token刷新任务...")
                                self.token_refresh_task = asyncio.create_task(self.token_refresh_loop())
                                tasks_started.append("Token刷新")
                            else:
                                logger.info(f"【{self.cookie_id}】Token刷新任务已在运行，跳过启动")

                            if not self.cleanup_task or self.cleanup_task.done():
                                logger.info(f"【{self.cookie_id}】启动暂停记录清理任务...")
                                self.cleanup_task = asyncio.create_task(self.pause_cleanup_loop())
                                tasks_started.append("暂停清理")
                            else:
                                logger.info(f"【{self.cookie_id}】暂停记录清理任务已在运行，跳过启动")

                            if not self.cookie_refresh_task or self.cookie_refresh_task.done():
                                logger.info(f"【{self.cookie_id}】启动Cookie刷新任务...")
                                self.cookie_refresh_task = asyncio.create_task(self.cookie_refresh_loop())
                                tasks_started.append("Cookie刷新")
                            else:
                                logger.info(f"【{self.cookie_id}】Cookie刷新任务已在运行，跳过启动")

                            # 启动商品同步任务
                            if self.item_sync_enabled:
                                if not self.item_sync_task or self.item_sync_task.done():
                                    logger.info(f"【{self.cookie_id}】启动商品同步任务（间隔: {self.item_sync_interval}秒）...")
                                    self.item_sync_task = asyncio.create_task(self.item_sync_loop())
                                    tasks_started.append("商品同步")
                                else:
                                    logger.info(f"【{self.cookie_id}】商品同步任务已在运行，跳过启动")
                            else:
                                logger.info(f"【{self.cookie_id}】商品同步功能未启用")

                            # 记录所有后台任务状态
                            if tasks_started:
                                logger.info(f"【{self.cookie_id}】✅ 新启动的任务: {', '.join(tasks_started)}")
                            item_sync_status = '运行中' if self.item_sync_task and not self.item_sync_task.done() else '已启动' if self.item_sync_enabled else '未启用'
                            logger.info(f"【{self.cookie_id}】✅ 所有后台任务状态: 心跳(已启动), Token刷新({'运行中' if self.token_refresh_task and not self.token_refresh_task.done() else '已启动'}), 暂停清理({'运行中' if self.cleanup_task and not self.cleanup_task.done() else '已启动'}), Cookie刷新({'运行中' if self.cookie_refresh_task and not self.cookie_refresh_task.done() else '已启动'}), 商品同步({item_sync_status})")

                            logger.info(f"【{self.cookie_id}】开始监听WebSocket消息...")
                            logger.info(f"【{self.cookie_id}】WebSocket连接状态正常，等待服务器消息...")
                            logger.info(f"【{self.cookie_id}】准备进入消息循环...")
                            await websocket_reader_task
                        finally:
                            self._websocket_bootstrap_active = False
                            self._websocket_bootstrap_error = None
                            if websocket_reader_task and not websocket_reader_task.done():
                                websocket_reader_task.cancel()
                            if websocket_reader_task:
                                await asyncio.gather(
                                    websocket_reader_task,
                                    return_exceptions=True,
                                )
                            self._fail_direct_conversation_waiters("account websocket closed")
                            # 确保在退出 async with 块时清理 WebSocket 引用
                            # 注意：async with 会自动关闭 WebSocket，但我们需要清理引用
                            if self.ws == websocket:
                                self.ws = None
                                logger.info(f"【{self.cookie_id}】WebSocket连接已退出，引用已清理")

                except Exception as e:
                    error_msg = self._safe_str(e)
                    error_type = type(e).__name__
                    self.connection_failures += 1
                    is_connection_closed = self._log_websocket_connection_failure(
                        self.cookie_id,
                        error_type=error_type,
                        error_message=error_msg,
                        failure_count=self.connection_failures,
                        max_failures=self.max_connection_failures,
                    )
                    # 更新连接状态为重连中
                    self._set_connection_state(ConnectionState.RECONNECTING, f"第{self.connection_failures}次失败")

                    # 确保清理 WebSocket 引用
                    if self.ws:
                        try:
                            # 检查 WebSocket 是否仍然打开
                            if hasattr(self.ws, 'close_code') and self.ws.close_code is None:
                                # WebSocket 可能仍然打开，尝试关闭
                                try:
                                    await asyncio.wait_for(self.ws.close(), timeout=2.0)
                                except (asyncio.TimeoutError, Exception):
                                    pass
                        except Exception:
                            pass
                        finally:
                            self.ws = None
                            logger.info(f"【{self.cookie_id}】WebSocket引用已清理")

                    # 对于连接关闭错误，补充更明确的重连状态。
                    if is_connection_closed:
                        self._set_connection_state(ConnectionState.RECONNECTING, f"连接关闭，第{self.connection_failures}次重连")

                    # 检查是否超过最大失败次数
                    if self.connection_failures >= self.max_connection_failures:
                        self._set_connection_state(ConnectionState.FAILED, f"连续失败{self.max_connection_failures}次")
                        logger.warning(
                            f"【{self.cookie_id}】连续连接失败，"
                            "按临时网络故障继续自动重试"
                        )
                        await self._mark_retryable_token_probe_failure(
                            SessionProbeResult(
                                status=PROBE_RETRYABLE_ERROR,
                                cookies=dict(self.cookies),
                                error_code="connection_failures",
                                message="连续连接失败",
                            ),
                            trigger=f"连续连接失败{self.connection_failures}次",
                        )

                    # 计算重试延迟
                    retry_delay = self._calculate_retry_delay(error_msg)
                    logger.warning(f"【{self.cookie_id}】将在 {retry_delay} 秒后重试连接...")

                    try:
                        # 清空当前token，确保重新连接时会重新获取
                        if self.current_token:
                            logger.warning(f"【{self.cookie_id}】清空当前token，重新连接时将重新获取")
                            self.current_token = None

                        # 直接重置任务引用，不等待取消（快速重连方案）
                        # 这样可以避免等待任务取消导致的阻塞问题
                        logger.info(f"【{self.cookie_id}】准备重置后台任务引用（快速重连模式）...")
                        self._reset_background_tasks()
                        logger.info(f"【{self.cookie_id}】后台任务引用已重置，可以立即重连")

                        # 等待后重试 - 使用可中断的sleep，并定期输出日志证明进程还活着
                        logger.info(f"【{self.cookie_id}】开始等待 {retry_delay} 秒...")
                        # 强制刷新日志缓冲区，确保日志被写入
                        try:
                            sys.stdout.flush()
                        except:
                            pass

                        # 使用可中断的sleep，每5秒输出一次心跳日志
                        chunk_size = 5.0  # 每5秒输出一次日志
                        remaining = retry_delay
                        start_time = time.time()

                        while remaining > 0:
                            sleep_time = min(chunk_size, remaining)
                            try:
                                await asyncio.sleep(sleep_time)
                                remaining -= sleep_time
                                elapsed = time.time() - start_time
                                if remaining > 0:
                                    logger.info(f"【{self.cookie_id}】等待中... 已等待 {elapsed:.1f} 秒，剩余 {remaining:.1f} 秒")
                                    # 定期刷新日志
                                    try:
                                        sys.stdout.flush()
                                    except:
                                        pass
                            except asyncio.CancelledError:
                                logger.warning(f"【{self.cookie_id}】等待期间收到取消信号")
                                raise
                            except Exception as sleep_error:
                                logger.error(f"【{self.cookie_id}】等待期间发生异常: {self._safe_str(sleep_error)}")
                                # 即使出错也继续等待剩余时间
                                if remaining > 0:
                                    await asyncio.sleep(remaining)
                                break

                        logger.info(f"【{self.cookie_id}】等待完成（总耗时 {time.time() - start_time:.1f} 秒），准备重新连接...")
                        # 再次强制刷新日志
                        try:
                            sys.stdout.flush()
                        except:
                            pass

                    except Exception as cleanup_error:
                        logger.error(f"【{self.cookie_id}】清理过程出错: {self._safe_str(cleanup_error)}")
                        # 即使清理失败，也要重置任务引用并等待后重试
                        self.heartbeat_task = None
                        self.token_refresh_task = None
                        self.cleanup_task = None
                        self.cookie_refresh_task = None
                        logger.warning(f"【{self.cookie_id}】清理失败，已强制重置所有任务引用")
                        # 使用可中断的sleep，并定期输出日志
                        logger.info(f"【{self.cookie_id}】清理失败后开始等待 {retry_delay} 秒...")
                        chunk_size = 5.0
                        remaining = retry_delay
                        start_time = time.time()

                        while remaining > 0:
                            sleep_time = min(chunk_size, remaining)
                            try:
                                await asyncio.sleep(sleep_time)
                                remaining -= sleep_time
                                if remaining > 0:
                                    logger.info(f"【{self.cookie_id}】清理失败后等待中... 剩余 {remaining:.1f} 秒")
                            except asyncio.CancelledError:
                                logger.warning(f"【{self.cookie_id}】清理失败后等待期间收到取消信号")
                                raise
                            except Exception as sleep_error:
                                logger.error(f"【{self.cookie_id}】清理失败后等待期间发生异常: {self._safe_str(sleep_error)}")
                                if remaining > 0:
                                    await asyncio.sleep(remaining)
                                break

                        logger.info(f"【{self.cookie_id}】清理失败后等待完成（总耗时 {time.time() - start_time:.1f} 秒）")

                    # 继续下一次循环
                    logger.info(f"【{self.cookie_id}】开始新一轮WebSocket连接尝试...")
                    continue
        finally:
            # 更新连接状态为已关闭
            self._set_connection_state(ConnectionState.CLOSED, "程序退出")

            # 清空当前token
            if self.current_token:
                logger.info(f"【{self.cookie_id}】程序退出，清空当前token")
                self.current_token = None

            # 检查是否还有未取消的后台任务，如果有才执行清理
            has_pending_tasks = any([
                self.heartbeat_task and not self.heartbeat_task.done(),
                self.token_refresh_task and not self.token_refresh_task.done(),
                self.cleanup_task and not self.cleanup_task.done(),
                self.cookie_refresh_task and not self.cookie_refresh_task.done()
            ])

            if has_pending_tasks:
                logger.info(f"【{self.cookie_id}】检测到未完成的后台任务，执行清理...")
                # 使用统一的任务清理方法，添加超时保护
                try:
                    await asyncio.wait_for(
                        self._cancel_background_tasks(),
                        timeout=10.0
                    )
                except asyncio.TimeoutError:
                    logger.error(f"【{self.cookie_id}】程序退出时任务取消超时，强制继续")
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】程序退出时任务取消失败: {self._safe_str(e)}")
                finally:
                    # 确保任务引用被重置
                    self.heartbeat_task = None
                    self.token_refresh_task = None
                    self.cleanup_task = None
                    self.cookie_refresh_task = None
            else:
                logger.info(f"【{self.cookie_id}】所有后台任务已清理完成，跳过重复清理")
                # 确保任务引用被重置
                self.heartbeat_task = None
                self.token_refresh_task = None
                self.cleanup_task = None
                self.cookie_refresh_task = None

            # 清理所有后台任务
            if self.background_tasks:
                logger.info(f"【{self.cookie_id}】等待 {len(self.background_tasks)} 个后台任务完成...")
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*self.background_tasks, return_exceptions=True),
                        timeout=10.0  # 10秒超时
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"【{self.cookie_id}】后台任务清理超时，强制继续")

            # 确保关闭session
            await self.close_session()

            # 从全局实例字典中注销当前实例
            self._unregister_instance()
            logger.info(f"【{self.cookie_id}】XianyuLive主程序已完全退出")

    async def get_item_list_info(self, page_number=1, page_size=20, retry_count=0, save_to_db=True):
        """获取商品信息，自动处理token失效的情况

        Args:
            page_number (int): 页码，从1开始
            page_size (int): 每页数量，默认20
            retry_count (int): 重试次数，内部使用
        """
        if retry_count >= 4:  # 最多重试3次
            logger.error("获取商品信息失败，重试次数过多")
            return {"error": "获取商品信息失败，重试次数过多"}

        # 确保session已创建
        if not self.session:
            await self.create_session()

        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': str(int(time.time()) * 1000),
            'sign': '',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': 'mtop.idle.web.xyh.item.list',
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
            'spm_pre': 'a21ybx.collection.menu.1.272b5141NafCNK'
        }

        data = {
            'needGroupInfo': False,
            'pageNumber': page_number,
            'pageSize': page_size,
            'groupName': '在售',
            'groupId': '58877261',
            'defaultGroup': True,
            "userId": self.myid
        }

        # 始终从最新的 Cookie 中取签名令牌，日志只记录状态与长度。
        token_source = trans_cookies(self.cookies_str).get('_m_h5_tk', '')
        token = token_source.split('_', 1)[0] if token_source else ''
        logger.debug(
            "商品列表签名令牌状态: ready={}, length={}",
            bool(token),
            len(token),
        )

        # 生成签名
        data_val = json.dumps(data, separators=(',', ':'))
        sign = generate_sign(params['t'], token, data_val)
        params['sign'] = sign

        try:
            async with self.session.post(
                _resolve_h5_api_url('https://h5api.m.goofish.com/h5/mtop.idle.web.xyh.item.list/1.0/'),
                params=params,
                data={'data': data_val}
            ) as response:
                res_json = await response.json()

                # 检查并更新Cookie
                if 'set-cookie' in response.headers:
                    new_cookies = {}
                    for cookie in response.headers.getall('set-cookie', []):
                        if '=' in cookie:
                            name, value = cookie.split(';')[0].split('=', 1)
                            new_cookies[name.strip()] = value.strip()

                    # 更新cookies
                    if new_cookies:
                        self.cookies.update(new_cookies)
                        # 生成新的cookie字符串
                        self.cookies_str = '; '.join([f"{k}={v}" for k, v in self.cookies.items()])
                        # 更新数据库中的Cookie
                        await self.update_config_cookies()
                        logger.warning("已更新Cookie到数据库")

                ret_values = res_json.get('ret') if isinstance(res_json, dict) else []
                ret_values = ret_values if isinstance(ret_values, list) else []
                response_data = res_json.get('data') if isinstance(res_json, dict) else {}
                response_data = response_data if isinstance(response_data, dict) else {}
                response_cards = response_data.get('cardList')
                response_cards = response_cards if isinstance(response_cards, list) else []
                response_code = sanitize_runtime_error(
                    str(ret_values[0] if ret_values else 'unknown')
                ).split('::', 1)[0][:80]
                logger.info(
                    "商品列表响应摘要: page={}, status={}, cards={}",
                    page_number,
                    response_code,
                    len(response_cards),
                )

                # 检查响应是否成功
                if res_json.get('ret') and res_json['ret'][0] == 'SUCCESS::调用成功':
                    items_data = res_json.get('data', {})
                    card_list = items_data.get('cardList')
                    if card_list is None and 'nextPage' in items_data:
                        # 在售列表为空时闲鱼不返回 cardList 字段（实测 2026-08-28：
                        # SUCCESS + totalCount/nextPage 存在但无 cardList），按 0 件商品处理。
                        logger.info("商品列表为空（无在售商品）: page={}", page_number)
                        card_list = []
                    if not isinstance(card_list, list):
                        logger.error("商品列表响应缺少 cardList: page={}", page_number)
                        return {"error": "商品列表响应结构异常", "error_code": "invalid_response"}

                    # 解析cardList中的商品信息
                    items_list = []
                    filtered_count = 0
                    invalid_card_count = 0
                    for card in card_list:
                        card_data = card.get('cardData', {})
                        if card_data:
                            # 提取商品基本信息
                            detail_params = card_data.get('detailParams', {})
                            item_id = detail_params.get('itemId', card_data.get('id', ''))

                            raw_status = card_data.get('itemStatus')
                            try:
                                item_status = int(raw_status)
                            except (TypeError, ValueError):
                                invalid_card_count += 1
                                continue
                            if item_status != 0:
                                filtered_count += 1
                                continue

                            item_title = card_data.get('title') or detail_params.get('title') or ''
                            if not item_id or not str(item_title).strip():
                                invalid_card_count += 1
                                continue

                            item_info = {
                                'id': item_id,
                                'title': item_title,
                                'price': card_data.get('priceInfo', {}).get('price', ''),
                                'price_text': card_data.get('priceInfo', {}).get('preText', '') + card_data.get('priceInfo', {}).get('price', ''),
                                'category_id': card_data.get('categoryId', ''),
                                'auction_type': card_data.get('auctionType', ''),
                                'item_status': item_status,
                                'detail_url': card_data.get('detailUrl', ''),
                                # Web可访问的商品URL（用于浏览器打开）
                                'web_url': f'https://www.goofish.com/item?id={item_id}',
                                'pic_info': card_data.get('picInfo', {}),
                                'item_image': extract_catalog_image_url(card_data),
                                'detail_params': detail_params,
                                'track_params': card_data.get('trackParams', {}),
                                'item_label_data': card_data.get('itemLabelDataVO', {}),
                                'card_type': card.get('cardType', 0)
                            }
                            items_list.append(item_info)
                        else:
                            invalid_card_count += 1

                    if invalid_card_count:
                        logger.error(
                            "商品列表存在无法解析的卡片: page={}, invalid={}",
                            page_number,
                            invalid_card_count,
                        )
                        return {"error": "商品列表包含无法解析的记录", "error_code": "invalid_response"}

                    next_page_value = items_data.get('nextPage')
                    has_next_page = (
                        str(next_page_value).lower() == 'true'
                        if next_page_value is not None
                        else len(card_list) >= page_size
                    )
                    logger.info(
                        "商品列表解析完成: page={}, published={}, filtered={}, next_page={}",
                        page_number,
                        len(items_list),
                        filtered_count,
                        has_next_page,
                    )

                    # 自动保存商品信息到数据库
                    sync_summary = None
                    if save_to_db:
                        sync_summary = await self.save_items_list_to_db(items_list, reconcile=False)

                    return {
                        "success": True,
                        "page_number": page_number,
                        "page_size": page_size,
                        "current_count": len(items_list),
                        "items": items_list,
                        "saved_count": (sync_summary or {}).get('saved_count', 0),
                        "sync_summary": sync_summary,
                        "filtered_count": filtered_count,
                        "has_next_page": has_next_page,
                    }
                else:
                    # 检查是否是token失效
                    error_msg = res_json.get('ret', [''])[0] if res_json.get('ret') else ''
                    if 'FAIL_SYS_TOKEN_EXOIRED' in error_msg or 'token' in error_msg.lower():
                        logger.warning(f"Token失效，准备重试: {error_msg}")
                        await asyncio.sleep(0.5)
                        return await self.get_item_list_info(
                            page_number,
                            page_size,
                            retry_count + 1,
                            save_to_db=save_to_db,
                        )
                    else:
                        safe_error = sanitize_runtime_error(error_msg).split('::', 1)[0][:80]
                        logger.error("商品列表请求失败: {}", safe_error or 'unknown')
                        return {"error": f"获取商品信息失败: {safe_error}"}

        except Exception as e:
            logger.error(f"商品信息API请求异常: {self._safe_str(e)}")
            await asyncio.sleep(0.5)
            return await self.get_item_list_info(
                page_number,
                page_size,
                retry_count + 1,
                save_to_db=save_to_db,
            )

    async def get_all_items(self, page_size=20, max_pages=None):
        """获取所有商品信息（自动分页）

        Args:
            page_size (int): 每页数量，默认20
            max_pages (int): 最大页数限制，None表示无限制

        Returns:
            dict: 包含所有商品信息的字典
        """
        all_items = []
        page_number = 1
        filtered_count = 0
        pages_scanned = 0

        logger.info(f"开始获取所有商品信息，每页{page_size}条")

        while True:
            if max_pages and page_number > max_pages:
                logger.info(f"达到最大页数限制 {max_pages}，停止获取")
                break

            logger.info(f"正在获取第 {page_number} 页...")
            result = await self.get_item_list_info(
                page_number,
                page_size,
                save_to_db=False,
            )

            if not result.get("success"):
                logger.error(
                    "商品列表分页失败: page={}, error_code={}",
                    page_number,
                    str(result.get('error_code') or 'page_failed')[:80],
                )
                return {
                    "success": False,
                    "error": result.get('error') or f"第 {page_number} 页获取失败",
                    "error_code": result.get('error_code') or 'page_failed',
                    "total_pages": pages_scanned,
                    "total_count": len(all_items),
                    "total_saved": 0,
                    "items": all_items,
                }

            current_items = result.get("items", [])
            all_items.extend(current_items)
            filtered_count += int(result.get('filtered_count') or 0)
            pages_scanned += 1

            logger.info(f"第 {page_number} 页获取到 {len(current_items)} 个商品")

            if not result.get('has_next_page'):
                break

            if max_pages and page_number >= max_pages:
                logger.error("商品同步达到页数上限但平台仍有下一页，取消目录对账")
                return {
                    "success": False,
                    "error": "商品列表未完整获取，未更新在售状态",
                    "error_code": "page_limit_reached",
                    "total_pages": pages_scanned,
                    "total_count": len(all_items),
                    "total_saved": 0,
                    "items": all_items,
                }

            page_number += 1

            # 添加延迟避免请求过快
            await asyncio.sleep(1)

        sync_summary = await self.save_items_list_to_db(all_items, reconcile=True)
        total_saved = int(sync_summary.get('saved_count') or 0)
        if sync_summary.get('failed_count'):
            return {
                "success": False,
                "error": "商品目录保存不完整，未能安全完成同步",
                "error_code": "catalog_persist_failed",
                "total_pages": pages_scanned,
                "total_count": len(all_items),
                "total_saved": total_saved,
                "items": all_items,
                "sync_summary": sync_summary,
            }

        logger.info(
            "所有在售商品同步完成: pages={}, active={}, hidden={}, images_updated={}",
            pages_scanned,
            sync_summary.get('active_count', 0),
            sync_summary.get('hidden_count', 0),
            sync_summary.get('images_updated', 0),
        )

        return {
            "success": True,
            "total_pages": pages_scanned,
            "total_count": len(all_items),
            "total_saved": total_saved,
            "filtered_count": filtered_count,
            "active_count": sync_summary.get('active_count', 0),
            "hidden_count": sync_summary.get('hidden_count', 0),
            "images_updated": sync_summary.get('images_updated', 0),
            "failed_count": sync_summary.get('failed_count', 0),
            "items": all_items,
            "sync_summary": sync_summary,
        }

    async def send_image_msg(
        self,
        ws,
        cid,
        toid,
        image_url,
        width=800,
        height=600,
        card_id=None,
        wait_for_response=False,
    ):
        """发送图片消息"""
        try:
            # 检查图片URL是否需要上传到CDN
            original_url = image_url

            if self._is_cdn_url(image_url):
                # 已经是CDN链接，直接使用
                logger.info(f"【{self.cookie_id}】使用已有的CDN图片链接")
            elif image_url.startswith('/static/uploads/') or image_url.startswith('static/uploads/'):
                # 本地图片，需要上传到闲鱼CDN
                local_image_path = image_url.replace('/static/uploads/', 'static/uploads/')
                if os.path.exists(local_image_path):
                    logger.info(f"【{self.cookie_id}】准备上传本地图片到闲鱼CDN: {local_image_path}")

                    # 使用图片上传器上传到闲鱼CDN
                    from utils.image_uploader import ImageUploader
                    uploader = ImageUploader(self.cookies_str)

                    async with uploader:
                        cdn_url = await uploader.upload_image(local_image_path)
                        if cdn_url:
                            logger.info(f"【{self.cookie_id}】图片上传成功")
                            image_url = cdn_url

                            # 如果是卡券图片，更新数据库中的图片URL
                            if card_id is not None:
                                await self._update_card_image_url(card_id, cdn_url)

                            # 获取实际图片尺寸
                            from utils.image_utils import image_manager
                            try:
                                actual_width, actual_height = image_manager.get_image_size(local_image_path)
                                if actual_width and actual_height:
                                    width, height = actual_width, actual_height
                                    logger.info(f"【{self.cookie_id}】获取到实际图片尺寸: {width}x{height}")
                            except Exception as e:
                                logger.warning(f"【{self.cookie_id}】获取图片尺寸失败，使用默认尺寸: {e}")
                        else:
                            logger.error(f"【{self.cookie_id}】图片上传失败: {local_image_path}")
                            logger.error(f"【{self.cookie_id}】❌ Cookie可能已失效！请检查配置并更新Cookie")
                            raise Exception(f"图片上传失败（Cookie可能已失效）: {local_image_path}")
                else:
                    logger.error(f"【{self.cookie_id}】本地图片文件不存在: {local_image_path}")
                    raise Exception(f"本地图片文件不存在: {local_image_path}")
            else:
                logger.warning(f"【{self.cookie_id}】未知的图片URL格式")

            logger.info(
                f"【{self.cookie_id}】准备发送图片消息: "
                f"uploaded={image_url != original_url}, size={width}x{height}"
            )

            # 构造图片消息内容 - 使用正确的闲鱼格式
            image_content = {
                "contentType": 2,  # 图片消息类型
                "image": {
                    "pics": [
                        {
                            "height": int(height),
                            "type": 0,
                            "url": image_url,
                            "width": int(width)
                        }
                    ]
                }
            }

            # Base64编码
            content_json = json.dumps(image_content, ensure_ascii=False)
            content_base64 = str(base64.b64encode(content_json.encode('utf-8')), 'utf-8')

            logger.info(f"【{self.cookie_id}】Base64编码长度: {len(content_base64)}")

            body = [
                    {
                        "uuid": generate_uuid(),
                        "cid": f"{cid}@goofish",
                        "conversationType": 1,
                        "content": {
                            "contentType": 101,
                            "custom": {
                                "type": 1,
                                "data": content_base64
                            }
                        },
                        "redPointPolicy": 0,
                        "extension": {
                            "extJson": "{}"
                        },
                        "ctx": {
                            "appVersion": "1.0",
                            "platform": "web"
                        },
                        "mtags": {},
                        "msgReadStatusSetting": 1
                    },
                    {
                        "actualReceivers": [
                            f"{toid}@goofish",
                            f"{self.myid}@goofish"
                        ]
                    }
                ]
            if wait_for_response:
                response = await self._request_lwp_response(
                    ws,
                    "/r/MessageSend/sendByReceiverScope",
                    body=body,
                    timeout=10,
                )
            else:
                msg = {
                    "lwp": "/r/MessageSend/sendByReceiverScope",
                    "headers": {"mid": generate_mid()},
                    "body": body,
                }
                await self._ws_send_guarded(ws, msg)
                response = True
            logger.info(f"【{self.cookie_id}】图片消息发送成功")
            return response

        except Exception as e:
            logger.error(f"【{self.cookie_id}】发送图片消息失败: {self._safe_str(e)}")
            raise

    async def send_image_from_file(self, ws, cid, toid, image_path):
        """从本地文件发送图片"""
        try:
            # 上传图片到闲鱼CDN
            logger.info(f"【{self.cookie_id}】开始上传图片: {image_path}")

            from utils.image_uploader import ImageUploader
            uploader = ImageUploader(self.cookies_str)

            async with uploader:
                image_url = await uploader.upload_image(image_path)

            if image_url:
                # 获取图片信息
                from utils.image_utils import image_manager
                try:
                    from PIL import Image
                    with Image.open(image_path) as img:
                        width, height = img.size
                except Exception as e:
                    logger.warning(f"无法获取图片尺寸，使用默认值: {e}")
                    width, height = 800, 600

                # 发送图片消息
                await self.send_image_msg(ws, cid, toid, image_url, width, height)
                logger.info(f"【{self.cookie_id}】图片发送完成")
                return True
            else:
                logger.error(f"【{self.cookie_id}】图片上传失败: {image_path}")
                logger.error(f"【{self.cookie_id}】❌ Cookie可能已失效！请检查配置并更新Cookie")
                return False

        except Exception as e:
            logger.error(f"【{self.cookie_id}】从文件发送图片失败: {self._safe_str(e)}")
            return False

if __name__ == '__main__':
    cookies_str = os.getenv('COOKIES_STR')
    xianyuLive = XianyuLive(cookies_str)
    asyncio.run(xianyuLive.main())
