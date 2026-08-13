"""
自动免拼发货模块 - 解密版本

与 secure_confirm_decrypted 采用同一套平台 ret 分类与指数退避策略：
已发货/重复操作按幂等成功处理，会话失效与风控立即失败关闭，
限流与平台暂时不可用退避重试，其余未知失败一律失败关闭（不再盲目递归）。
失败返回带 category，供上层"确认发货失败自愈"决定是否回退到另一种发货接口
（未知失败往往意味着把普通单当拼单、或把拼单当普通单，即用错了发货接口）。
"""

import asyncio
import random
import time
from loguru import logger
from utils.xianyu_utils import trans_cookies, generate_sign

# ---- 重试策略参数（与 secure_confirm_decrypted 同量级）----
# 平台请求总次数上限（含首次请求）
MAX_FREESHIPPING_ATTEMPTS = 4
# 指数退避基础等待（秒）：第1/2/3次重试前基础等待约 5s/10s/20s（再乘抖动）
BACKOFF_BASE_SECONDS = 5.0
# 单次退避等待封顶（秒）
BACKOFF_MAX_SECONDS = 60.0
# 乘性抖动区间，避免多账号在同一时刻集中重试
BACKOFF_JITTER_RANGE = (0.5, 1.5)

# ---- 平台 ret 分类标记（与确认发货同源，因两者同为闲鱼发货类 MTOP）----
# 已发货/重复免拼：平台已处于发货终态，按幂等成功处理，不重试
ALREADY_SHIPPED_MARKERS = (
    "已发货",
    "已经发货",
    "重复发货",
    "重复确认",
    "请勿重复",
    "已免拼",
    "已成团",
)
# 会话/令牌失效：重试无意义，立即失败并交由登录维护流程处理
SESSION_INVALID_MARKERS = (
    "FAIL_SYS_SESSION_EXPIRED",
    "SESSION_EXPIRED",
    "SESSION过期",
    "FAIL_SYS_TOKEN_EXOIRED",
    "FAIL_SYS_TOKEN_EXPIRED",
    "FAIL_SYS_TOKEN_EMPTY",
    "令牌过期",
    "TOKEN过期",
    "MINI_LOGIN",
    "PASSPORT.GOOFISH.COM",
)
# 风控/需真人验证：继续重试只会加重风控，立即失败并转人工
HUMAN_INTERVENTION_MARKERS = (
    "FAIL_SYS_USER_VALIDATE",
    "FAIL_SYS_ILLEGAL_ACCESS",
    "FAIL_BIZ_WUA_IS_MACHINE",
    "WUA_IS_MACHINE",
    "RGV587",
    "PUNISH",
    "CAPTCHA",
    "VALIDATE",
)
# 限流：可在退避后重试
RATE_LIMIT_MARKERS = (
    "TRAFFIC_LIMIT",
    "HTTP_429",
    "TOO MANY REQUESTS",
    "RATE LIMIT",
    "限流",
    "请求频繁",
    "挤爆",
)
# 平台暂时不可用（网关/服务繁忙类）：可在退避后重试
PLATFORM_RETRYABLE_MARKERS = (
    "SERVICE UNAVAILABLE",
    "SERVICE_UNAVAILABLE",
    "网关",
    "服务繁忙",
)


def classify_freeshipping_ret(ret_values):
    """把平台 ret 列表归入重试决策类别，返回 (category, ret_text)。

    category 取值与确认发货一致：
    - success              调用成功
    - already_shipped      已发货/重复操作，幂等成功
    - session_invalid      会话/令牌失效，立即失败不重试
    - human_intervention   风控/需真人验证，立即失败不重试
    - rate_limited         限流，可退避重试
    - platform_unavailable 5xx/网关类暂时不可用，可退避重试
    - unknown_failure      无法确定的失败，一律失败关闭不重试
                           （上层据此判断是否用错发货接口并回退）
    """
    values = [str(value) for value in (ret_values or [])]
    ret_text = " | ".join(values)
    if not ret_text:
        return "unknown_failure", "平台响应缺少ret字段"
    upper = ret_text.upper()
    if "SUCCESS::" in upper:
        return "success", ret_text
    if any(marker.upper() in upper for marker in ALREADY_SHIPPED_MARKERS):
        return "already_shipped", ret_text
    if any(marker in upper for marker in SESSION_INVALID_MARKERS):
        return "session_invalid", ret_text
    if any(marker in upper for marker in HUMAN_INTERVENTION_MARKERS):
        return "human_intervention", ret_text
    if any(marker in upper for marker in RATE_LIMIT_MARKERS):
        return "rate_limited", ret_text
    if any(marker in upper for marker in PLATFORM_RETRYABLE_MARKERS):
        return "platform_unavailable", ret_text
    if any(f"HTTP_{status}" in upper for status in range(500, 600)):
        return "platform_unavailable", ret_text
    return "unknown_failure", ret_text


def _ret_error_code(ret_text):
    """提取首条 ret 的错误码部分（`::` 之前），避免整条平台文案进入日志与返回值。"""
    first = ret_text.split(" | ", 1)[0]
    code = first.split("::", 1)[0].strip()
    return (code or "UNKNOWN")[:64]


def _compute_backoff_seconds(retry_index):
    """计算第 retry_index 次重试（从0开始）前的等待秒数：指数退避 + 乘性抖动，封顶。"""
    base = min(BACKOFF_BASE_SECONDS * (2 ** max(0, retry_index)), BACKOFF_MAX_SECONDS)
    return base * random.uniform(*BACKOFF_JITTER_RANGE)


class SecureFreeshipping:
    def __init__(self, session, cookies_str, cookie_id):
        self.session = session
        self.cookies_str = cookies_str
        self.cookie_id = cookie_id
        self.cookies = trans_cookies(cookies_str) if cookies_str else {}

        # 这些属性将由主类传递
        self.current_token = None
        self.last_token_refresh_time = None
        self.token_refresh_interval = None

    def _safe_str(self, obj):
        """安全转换为字符串"""
        try:
            return str(obj)
        except Exception:
            return "无法转换的对象"

    async def update_config_cookies(self):
        """更新数据库中的cookies"""
        try:
            from db_manager import db_manager

            db_manager.update_config_cookies(self.cookie_id, self.cookies_str)
            logger.debug(f"【{self.cookie_id}】Cookie已更新到数据库")
        except Exception as e:
            logger.error(f"【{self.cookie_id}】更新Cookie到数据库失败: {self._safe_str(e)}")

    def _build_freeshipping_request(self, order_id, item_id, buyer_id):
        """构造一次免拼发货请求的 params/data（纯本地计算，不发网络）。"""
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
            'api': 'mtop.idle.groupon.activity.seller.freeshipping',
            'sessionOption': 'AutoLoginOnly',
        }
        data_val = '{"bizOrderId":"' + order_id + '", "itemId":' + item_id + ',"buyerId":' + buyer_id + '}'

        # 始终从最新的cookies中获取_m_h5_tk token（刷新后cookies会被更新）
        raw_token = trans_cookies(self.cookies_str).get('_m_h5_tk') or ''
        token = raw_token.split('_')[0] if raw_token else ''

        if token:
            logger.info(f"已从Cookie读取_m_h5_tk token，长度: {len(token)}")
        else:
            logger.warning("cookies中没有找到_m_h5_tk token")

        params['sign'] = generate_sign(params['t'], token, data_val)
        return params, {'data': data_val}

    async def _post_freeshipping(self, params, data):
        """发送一次免拼发货请求，返回可供分类的响应字典。"""
        async with self.session.post(
            'https://h5api.m.goofish.com/h5/mtop.idle.groupon.activity.seller.freeshipping/1.0/',
            params=params,
            data=data
        ) as response:
            status = response.status
            if status != 200:
                # 非200不解析响应体，合成HTTP状态标记交给分类器
                # （429 → 限流退避，5xx → 平台暂不可用退避，其余失败关闭）
                logger.warning(f"【{self.cookie_id}】免拼发货HTTP状态异常: {status}")
                return {"ret": [f"HTTP_{status}::免拼发货接口HTTP状态异常"]}

            res_json = await response.json()
            if not isinstance(res_json, dict):
                return {"ret": ["INVALID_RESPONSE::免拼发货响应不是JSON对象"]}

            # 检查并更新Cookie
            if 'set-cookie' in response.headers:
                new_cookies = {}
                for cookie in response.headers.getall('set-cookie', []):
                    if '=' in cookie:
                        name, value = cookie.split(';')[0].split('=', 1)
                        new_cookies[name.strip()] = value.strip()

                if new_cookies:
                    self.cookies.update(new_cookies)
                    self.cookies_str = '; '.join([f"{k}={v}" for k, v in self.cookies.items()])
                    await self.update_config_cookies()
                    logger.debug("已更新Cookie到数据库")

            logger.info(
                f"【{self.cookie_id}】免拼发货响应已接收: "
                f"ret_count={len(res_json.get('ret') or [])}"
            )
            return res_json

    async def auto_freeshipping(self, order_id, item_id, buyer_id, retry_count=0):
        """自动免拼发货：按平台 ret 分类决定成功/失败关闭/退避重试。

        对外签名与返回结构保持兼容：
        - 成功（含已发货幂等成功）返回 {"success": True, "order_id": ...}
        - 失败返回 {"error": ..., "order_id": ..., "category": ...}
        retry_count 表示已消耗的请求次数（兼容旧入参语义）。
        """
        # 确保session已创建
        if not self.session:
            raise Exception("Session未创建")

        # 关键字段缺失会拼出非法JSON、请求必败，直接失败关闭（不发网络、不重试）
        order_id = str(order_id or "").strip()
        item_id = str(item_id or "").strip()
        buyer_id = str(buyer_id or "").strip()
        if not order_id or not item_id or not buyer_id:
            logger.error(f"【{self.cookie_id}】免拼发货缺少必要参数(order/item/buyer)，失败关闭")
            return {
                "error": "免拼发货缺少必要参数",
                "order_id": order_id,
                "category": "invalid_request",
            }

        attempts_used = max(0, int(retry_count))
        last_error = "免拼发货失败，重试次数过多"
        last_category = "retry_exhausted"

        while attempts_used < MAX_FREESHIPPING_ATTEMPTS:
            attempts_used += 1
            logger.info(
                f"【{self.cookie_id}】开始自动免拼发货，订单ID: {order_id}，"
                f"第{attempts_used}次尝试"
            )
            params, data = self._build_freeshipping_request(order_id, item_id, buyer_id)

            try:
                res_json = await self._post_freeshipping(params, data)
            except Exception as e:
                # 网络/传输异常：可重试类，走指数退避
                last_error = f"网络异常: {type(e).__name__}"
                last_category = "network_error"
                logger.error(f"【{self.cookie_id}】免拼发货API请求异常: {type(e).__name__}")
                if attempts_used >= MAX_FREESHIPPING_ATTEMPTS:
                    break
                delay = _compute_backoff_seconds(attempts_used - 1)
                logger.info(f"【{self.cookie_id}】网络异常，{delay:.1f}秒后重试...")
                await asyncio.sleep(delay)
                continue

            category, ret_text = classify_freeshipping_ret(res_json.get('ret'))
            error_code = _ret_error_code(ret_text)

            if category == "success":
                logger.info(f"【{self.cookie_id}】✅ 自动免拼发货成功，订单ID: {order_id}")
                return {"success": True, "order_id": order_id}

            if category == "already_shipped":
                # 平台已处于发货状态（重复操作），按幂等成功处理，不重试
                logger.info(
                    f"【{self.cookie_id}】订单已是发货状态，按幂等成功处理，订单ID: {order_id}"
                )
                return {"success": True, "order_id": order_id, "already_shipped": True}

            if category in ("session_invalid", "human_intervention"):
                # 会话失效/风控：重试只会加重风控，立即失败关闭并交人工处理
                logger.warning(
                    f"【{self.cookie_id}】❌ 免拼发货需要人工处理，"
                    f"分类={category}，错误码={error_code}，不再重试"
                )
                return {
                    "error": f"免拼发货失败: {error_code}",
                    "order_id": order_id,
                    "category": category,
                }

            if category in ("rate_limited", "platform_unavailable"):
                # 限流/平台暂时不可用：退避后重试，直到次数上限
                last_error = f"免拼发货失败: {error_code}"
                last_category = category
                logger.warning(
                    f"【{self.cookie_id}】免拼发货被平台暂时拒绝，"
                    f"分类={category}，错误码={error_code}"
                )
                if attempts_used >= MAX_FREESHIPPING_ATTEMPTS:
                    break
                delay = _compute_backoff_seconds(attempts_used - 1)
                logger.info(
                    f"【{self.cookie_id}】{delay:.1f}秒后进行第{attempts_used + 1}次尝试..."
                )
                await asyncio.sleep(delay)
                continue

            # 未知业务失败：默认失败关闭，不连打平台
            # （常见于把普通单当拼单，交由上层自愈回退到确认发货）
            logger.warning(
                f"【{self.cookie_id}】❌ 免拼发货返回未知失败，"
                f"错误码={error_code}，失败关闭不重试"
            )
            return {
                "error": f"免拼发货失败: {error_code}",
                "order_id": order_id,
                "category": "unknown_failure",
            }

        logger.error(f"【{self.cookie_id}】免拼发货失败，重试次数过多")
        return {
            "error": last_error,
            "order_id": order_id,
            "category": last_category,
            "retry_exhausted": True,
        }
