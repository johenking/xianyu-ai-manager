#!/usr/bin/env python3
"""
闲鱼扫码登录工具
基于API接口实现二维码生成和Cookie获取（参照myfish-main项目）
"""

import asyncio
import time
import uuid
import json
import re
from random import random
from typing import Optional, Dict, Any
import httpx
import qrcode
import qrcode.constants
from loguru import logger
import hashlib
from utils.browser_interaction import BrowserInteractionChannel
from utils.qr_verification_browser import (
    QRVerificationBrowser,
    remove_verification_screenshot,
)
from utils.verification_images import resolve_private_verification_image
from utils.xianyu_session_probe import (
    PROBE_EXPIRED,
    PROBE_RETRYABLE_ERROR,
    PROBE_SUCCESS,
    PROBE_VERIFICATION_REQUIRED,
    cookies_to_string,
    detect_default_browser_user_agent,
    has_core_session_cookies,
    is_allowed_verification_url,
    probe_message_session_async,
)

GOOFISH_IM_URL = "https://www.goofish.com/im"


def generate_headers():
    """生成请求头"""
    return {
        'User-Agent': detect_default_browser_user_agent(),
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'Referer': 'https://passport.goofish.com/',
        'Origin': 'https://passport.goofish.com',
    }


class GetLoginParamsError(Exception):
    """获取登录参数错误"""


class GetLoginQRCodeError(Exception):
    """获取登录二维码失败"""


class NotLoginError(Exception):
    """未登录错误"""


class QRLoginSession:
    """二维码登录会话"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.status = 'waiting'  # waiting, scanned, success, expired, cancelled, verification_required, verification_checking
        self.qr_code_url = None
        self.qr_content = None
        self.cookies = {}
        self.unb = None
        self.created_time = time.time()
        self.expire_time = 300  # 5分钟过期
        self.verification_expire_time = 900  # 安全验证最多保留15分钟
        self.params = {}  # 存储登录参数
        self.verification_url = None  # 风控验证URL
        self.verification_screenshot_path = None  # 风控验证页面截图
        self.verification_browser_status = None  # starting, waiting, success, failed, timeout
        self.verification_error = None
        self.verification_task = None
        self.verification_kind = ""
        self.required_action = ""
        self.error_code = None
        self.message = None
        self.validated = False
        self.terminal_at = None
        self.ended_by = ""
        self.interaction_channel = BrowserInteractionChannel()

    def is_expired(self) -> bool:
        """检查是否过期"""
        return time.time() - self.created_time > self.expire_time

    def is_verification_expired(self) -> bool:
        """检查安全验证会话是否过期"""
        return time.time() - self.created_time > self.verification_expire_time

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'session_id': self.session_id,
            'status': self.status,
            'qr_code_url': self.qr_code_url,
            'created_time': self.created_time,
            'is_expired': self.is_expired()
        }


class QRLoginManager:
    """二维码登录管理器"""

    def __init__(
        self,
        *,
        verification_browser=None,
        session_validator=None,
        terminal_retention_seconds: float = 300.0,
    ):
        self.sessions: Dict[str, QRLoginSession] = {}
        self.headers = generate_headers()
        self.host = "https://passport.goofish.com"
        self.api_mini_login = f"{self.host}/mini_login.htm"
        self.api_generate_qr = f"{self.host}/newlogin/qrcode/generate.do"
        self.api_scan_status = f"{self.host}/newlogin/qrcode/query.do"
        self.api_h5_tk = "https://h5api.m.goofish.com/h5/mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get/1.0/"

        # 配置代理（如果需要的话，取消注释并修改代理地址）
        # self.proxy = "http://127.0.0.1:7890"
        self.proxy = None

        # 配置超时时间
        self.timeout = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=60.0)
        self.verification_browser = verification_browser or QRVerificationBrowser()
        self.session_validator = session_validator or probe_message_session_async
        self.terminal_retention_seconds = max(300.0, float(terminal_retention_seconds))

    @staticmethod
    def _mark_terminal(
        session: QRLoginSession,
        status: str,
        message: Optional[str] = None,
        *,
        now: Optional[float] = None,
        ended_by: Optional[str] = None,
    ) -> None:
        session.status = status
        if message is not None:
            session.message = message
        if ended_by is not None:
            session.ended_by = ended_by
        elif not session.ended_by:
            session.ended_by = {
                "expired": "expired",
                "cancelled": "user_cancelled",
                "error": "validation_failed",
            }.get(status, "")
        if session.terminal_at is None:
            session.terminal_at = time.time() if now is None else now

    def _cookie_marshal(self, cookies: dict) -> str:
        """将Cookie字典转换为字符串"""
        return "; ".join([f"{k}={v}" for k, v in cookies.items()])

    @staticmethod
    def _verification_entry_url(value: Any) -> str:
        candidate = str(value or "").strip()
        return candidate if is_allowed_verification_url(candidate) else GOOFISH_IM_URL

    def _make_qr_data_url(self, content: str) -> str:
        """生成二维码Data URL"""
        from io import BytesIO
        import base64

        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=2,
        )
        qr.add_data(content)
        qr.make()

        qr_img = qr.make_image()
        buffer = BytesIO()
        qr_img.save(buffer, format='PNG')
        qr_base64 = base64.b64encode(buffer.getvalue()).decode()
        return f"data:image/png;base64,{qr_base64}"

    @staticmethod
    def _merge_response_cookies(
        session: QRLoginSession,
        client: Any,
        response: Optional[httpx.Response] = None,
    ) -> None:
        """Merge the complete redirect-aware client jar without logging values."""
        sources = []
        client_cookies = getattr(client, "cookies", None)
        if client_cookies is not None:
            sources.append(client_cookies)
        if response is not None:
            sources.extend(
                [getattr(item, "cookies", None) for item in response.history]
            )
            sources.append(response.cookies)
        for source in sources:
            if source is None:
                continue
            if not isinstance(source, (httpx.Cookies, dict)):
                continue
            items = list(source.items())
            for name, value in items:
                if name and value is not None:
                    session.cookies[str(name)] = str(value)

    @staticmethod
    def _h5_token_value(cookies: Dict[str, str]) -> str:
        raw = str(cookies.get("_m_h5_tk") or "")
        return raw.split("_", 1)[0].strip()

    async def _get_mh5tk(self, session: QRLoginSession) -> dict:
        """获取m_h5_tk和m_h5_tk_enc"""
        data = {"bizScene": "home"}
        data_str = json.dumps(data, separators=(',', ':'))
        app_key = "34839810"

        # 先发一次 GET 请求，获取 cookie 中的 m_h5_tk
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, proxy=self.proxy) as client:
            try:
                resp = await client.get(
                    self.api_h5_tk,
                    headers=self.headers,
                    cookies=session.cookies,
                )
                self._merge_response_cookies(session, client, resp)
                token = self._h5_token_value(session.cookies)

                def signed_params(signing_token: str) -> dict:
                    request_time = str(int(time.time() * 1000))
                    sign_input = (
                        f"{signing_token}&{request_time}&{app_key}&{data_str}"
                    )
                    return {
                        "jsv": "2.7.2",
                        "appKey": app_key,
                        "t": request_time,
                        "sign": hashlib.md5(sign_input.encode()).hexdigest(),
                        "v": "1.0",
                        "type": "originaljson",
                        "dataType": "json",
                        "timeout": 20000,
                        "api": "mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get",
                        "data": data_str,
                    }

                # 发请求正式获取数据；若服务端刷新 Token，只用新 Token 重签一次。
                posted = await client.post(
                    self.api_h5_tk,
                    params=signed_params(token),
                    headers=self.headers,
                    cookies=session.cookies,
                )
                self._merge_response_cookies(session, client, posted)
                refreshed_token = self._h5_token_value(session.cookies)
                if refreshed_token and refreshed_token != token:
                    posted = await client.post(
                        self.api_h5_tk,
                        params=signed_params(refreshed_token),
                        headers=self.headers,
                        cookies=session.cookies,
                    )
                    self._merge_response_cookies(session, client, posted)

                return dict(session.cookies)
            except httpx.ConnectTimeout:
                logger.error("获取m_h5_tk时连接超时")
                raise
            except httpx.ReadTimeout:
                logger.error("获取m_h5_tk时读取超时")
                raise
            except httpx.ConnectError:
                logger.error("获取m_h5_tk时连接错误")
                raise

    async def _get_login_params(self, session: QRLoginSession) -> dict:
        """获取二维码登录时需要的表单参数"""
        params = {
            "lang": "zh_cn",
            "appName": "xianyu",
            "appEntrance": "web",
            "styleType": "vertical",
            "bizParams": "",
            "notLoadSsoView": False,
            "notKeepLogin": False,
            "isMobile": False,
            "qrCodeFirst": False,
            "stie": 77,
            "rnd": random(),
        }

        async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout, proxy=self.proxy) as client:
            try:
                resp = await client.get(
                    self.api_mini_login,
                    params=params,
                    cookies=session.cookies,
                    headers=self.headers,
                )
                self._merge_response_cookies(session, client, resp)

                # 正则匹配需要的json数据
                pattern = r"window\.viewData\s*=\s*(\{.*?\});"
                match = re.search(pattern, resp.text)
                if match:
                    json_string = match.group(1)
                    view_data = json.loads(json_string)
                    data = view_data.get("loginFormData")
                    if data:
                        data["umidTag"] = "SERVER"
                        session.params.update(data)
                        return data
                    else:
                        raise GetLoginParamsError("未找到loginFormData")
                else:
                    raise GetLoginParamsError("获取登录参数失败")
            except httpx.ConnectTimeout:
                logger.error("获取登录参数时连接超时")
                raise
            except httpx.ReadTimeout:
                logger.error("获取登录参数时读取超时")
                raise
            except httpx.ConnectError:
                logger.error("获取登录参数时连接错误")
                raise

    async def generate_qr_code(self) -> Dict[str, Any]:
        """生成二维码"""
        try:
            # 创建新的会话
            session_id = str(uuid.uuid4())
            session = QRLoginSession(session_id)

            # 1. 获取m_h5_tk
            await self._get_mh5tk(session)
            logger.info(f"获取m_h5_tk成功: {session_id}")

            # 2. 获取登录参数
            login_params = await self._get_login_params(session)
            logger.info(f"获取登录参数成功: {session_id}")

            # 3. 生成二维码
            async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout, proxy=self.proxy) as client:
                resp = await client.get(
                    self.api_generate_qr,
                    params=login_params,
                    headers=self.headers,
                    cookies=session.cookies,
                )
                self._merge_response_cookies(session, client, resp)

                try:
                    results = resp.json()
                except (TypeError, ValueError):
                    logger.warning(
                        "二维码接口返回格式异常: status_code={}, content_type={}",
                        resp.status_code,
                        resp.headers.get("content-type", "unknown").split(";", 1)[0],
                    )
                    raise GetLoginQRCodeError("二维码接口返回格式异常") from None

                content = results.get("content", {}) if isinstance(results, dict) else {}
                data = content.get("data", {}) if isinstance(content, dict) else {}
                success = content.get("success") is True
                has_code_content = isinstance(data, dict) and bool(data.get("codeContent"))
                logger.debug(
                    "二维码接口响应摘要: status_code={}, success={}, has_code_content={}",
                    resp.status_code,
                    success,
                    has_code_content,
                )

                if success and has_code_content:
                    # 更新会话参数
                    session.params.update({
                        "t": data.get("t", ""),
                        "ck": data.get("ck", ""),
                    })

                    # 获取二维码内容
                    qr_content = data["codeContent"]
                    session.qr_content = qr_content

                    # 生成二维码图片（base64格式）
                    qr_data_url = self._make_qr_data_url(qr_content)

                    session.qr_code_url = qr_data_url
                    session.status = 'waiting'

                    # 保存会话
                    self.sessions[session_id] = session

                    # 启动状态检查任务
                    asyncio.create_task(self._monitor_qr_status(session_id))

                    logger.info(f"二维码生成成功: {session_id}")
                    return {
                        'success': True,
                        'session_id': session_id,
                        'qr_code_url': qr_data_url
                    }
                else:
                    raise GetLoginQRCodeError("获取登录二维码失败")

        except GetLoginQRCodeError as exc:
            logger.warning("二维码生成失败: {}", exc)
            return {
                'success': False,
                'error_code': 'qr_upstream_invalid_response',
                'retryable': True,
                'message': str(exc),
            }
        except httpx.ConnectTimeout:
            logger.error("二维码接口连接超时")
            return {
                'success': False, 'error_code': 'qr_upstream_connect_timeout',
                'retryable': True, 'message': '二维码服务连接超时，请原地重试',
            }
        except httpx.ReadTimeout:
            logger.error("二维码接口读取超时")
            return {
                'success': False, 'error_code': 'qr_upstream_read_timeout',
                'retryable': True, 'message': '二维码服务响应超时，请原地重试',
            }
        except httpx.ConnectError:
            logger.error("二维码接口连接错误")
            return {
                'success': False, 'error_code': 'qr_upstream_unreachable',
                'retryable': True, 'message': '二维码服务暂时不可达，请原地重试',
            }
        except Exception as exc:
            logger.error("二维码生成过程中发生异常: {}", type(exc).__name__)
            return {
                'success': False, 'error_code': 'qr_generation_failed',
                'retryable': True, 'message': '生成二维码失败，请原地重试',
            }

    async def _poll_qrcode_status(self, session: QRLoginSession) -> httpx.Response:
        """获取二维码扫描状态"""
        async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout, proxy=self.proxy) as client:
            resp = await client.post(
                self.api_scan_status,
                data=session.params,
                cookies=session.cookies,
                headers=self.headers,
            )
            self._merge_response_cookies(session, client, resp)
            return resp

    async def _validate_candidate_session(self, session: QRLoginSession) -> bool:
        """Validate a candidate Cookie through the real message-token API."""
        claimed_unb = str(session.unb or session.cookies.get("unb") or "").strip()
        if not claimed_unb or not has_core_session_cookies(session.cookies):
            self._mark_terminal(
                session,
                "error",
                "扫码结果缺少账号身份或核心会话字段，请重新扫码",
                ended_by="validation_failed",
            )
            session.error_code = "core_cookies_missing"
            session.validated = False
            return False

        result = await self.session_validator(
            cookies_to_string(session.cookies),
            detect_default_browser_user_agent(),
        )
        result_unb = str((result.cookies or {}).get("unb") or "").strip()
        if result_unb and result_unb != claimed_unb:
            self._mark_terminal(
                session,
                "error",
                "平台返回的账号身份与扫码会话不一致，已停止保存",
                ended_by="validation_failed",
            )
            session.error_code = "account_mismatch"
            session.validated = False
            return False

        if result.succeeded:
            session.cookies.update(result.cookies or {})
            session.unb = claimed_unb
            session.status = "success"
            session.error_code = None
            session.message = "扫码登录成功"
            session.validated = True
            session.ended_by = "session_validated"
            return True

        if result.status == PROBE_VERIFICATION_REQUIRED:
            session.cookies.update(result.cookies or {})
            session.verification_url = self._verification_entry_url(
                result.verification_url or session.verification_url
            )
            session.status = "verification_required"
            session.verification_browser_status = None
            session.verification_error = None
            session.verification_kind = ""
            session.required_action = "continue_in_client_browser"
            session.error_code = result.error_code
            session.message = "闲鱼要求安全验证，请在当前设备浏览器继续"
            session.validated = False
            return False

        self._mark_terminal(
            session,
            "error",
            ended_by="validation_failed",
        )
        session.error_code = result.error_code or (
            "session_expired" if result.status == PROBE_EXPIRED else "session_probe_retryable"
        )
        session.message = (
            "平台暂时未能确认登录状态，请重新生成二维码后再试"
            if result.status == PROBE_RETRYABLE_ERROR
            else result.message or "扫码登录态已失效，请重新扫码"
        )
        session.validated = False
        return False

    async def _monitor_qr_status(self, session_id: str, max_wait_time: int = 300, preserve_verification: bool = False):
        """监控二维码状态"""
        try:
            session = self.sessions.get(session_id)
            if not session:
                return

            logger.info(f"开始监控二维码状态: {session_id}")

            # 监控登录状态
            start_time = time.time()
            unknown_status_count = 0

            while time.time() - start_time < max_wait_time:
                try:
                    # 检查会话是否还存在
                    if session_id not in self.sessions:
                        break

                    # 轮询二维码状态
                    resp = await self._poll_qrcode_status(session)
                    qrcode_status = (
                        resp.json()
                        .get("content", {})
                        .get("data", {})
                        .get("qrCodeStatus")
                    )

                    if qrcode_status == "CONFIRMED":
                        unknown_status_count = 0
                        # 登录确认
                        if (
                            resp.json()
                            .get("content", {})
                            .get("data", {})
                            .get("iframeRedirect")
                            is True
                        ):
                            # 账号被风控，需要手机验证
                            session.status = 'verification_required'
                            raw_iframe_url = (
                                resp.json()
                                .get("content", {})
                                .get("data", {})
                                .get("iframeRedirectUrl")
                            )
                            session.verification_url = self._verification_entry_url(
                                raw_iframe_url
                            )
                            session.verification_browser_status = None
                            session.verification_error = None
                            session.verification_kind = ""
                            session.required_action = "continue_in_client_browser"
                            session.message = '闲鱼要求安全验证，请在当前设备浏览器继续'
                            logger.warning(f"账号被风控，需要客户设备继续验证: {session_id}")
                            break
                        else:
                            # 先收集扫码 Cookie，再通过真实消息会话接口校验。
                            for k, v in resp.cookies.items():
                                session.cookies[k] = v
                                if k == 'unb':
                                    session.unb = v
                            await self._validate_candidate_session(session)
                            logger.info(
                                f"扫码登录校验完成: {session_id}, "
                                f"status={session.status}, has_unb={bool(session.unb)}"
                            )
                            break

                    elif qrcode_status == "NEW":
                        unknown_status_count = 0
                        # 二维码未被扫描，继续轮询
                        await asyncio.sleep(0.8)

                    elif qrcode_status == "EXPIRED":
                        unknown_status_count = 0
                        if preserve_verification or session.verification_url:
                            session.status = 'verification_required'
                            logger.info(f"二维码查询显示过期，保留安全验证状态: {session_id}")
                            break
                        # 二维码已过期
                        self._mark_terminal(
                            session,
                            'expired',
                            '二维码已过期，请重新扫码',
                        )
                        logger.info(f"二维码已过期: {session_id}")
                        break

                    elif qrcode_status == "SCANED":
                        unknown_status_count = 0
                        # 二维码已被扫描，等待确认
                        if session.status == 'waiting':
                            session.status = 'scanned'
                            logger.info(f"二维码已扫描，等待确认: {session_id}")
                    elif qrcode_status in {"CANCELLED", "CANCELED", "CANCEL"}:
                        self._mark_terminal(session, 'cancelled', '扫码登录已取消')
                        logger.info(f"用户取消登录: {session_id}")
                        break
                    else:
                        unknown_status_count += 1
                        if unknown_status_count >= 5:
                            self._mark_terminal(
                                session,
                                "error",
                                "平台返回了未知扫码状态，请重新生成二维码",
                                ended_by="validation_failed",
                            )
                            session.error_code = "qr_status_unknown"
                            logger.warning(
                                "二维码状态连续异常，已停止轮询: session={}",
                                session_id,
                            )
                            break

                    await asyncio.sleep(0.8)  # 每0.8秒检查一次

                except Exception as exc:
                    logger.error(f"监控二维码状态异常: {type(exc).__name__}")
                    await asyncio.sleep(2)

            # 超时处理
            if session.status not in ['success', 'expired', 'cancelled', 'verification_required', 'error']:
                if preserve_verification and session.verification_url:
                    session.status = 'verification_required'
                    logger.info(f"未检测到安全验证通过，保留安全验证状态: {session_id}")
                else:
                    self._mark_terminal(
                        session,
                        'expired',
                        '二维码已过期，请重新扫码',
                    )
                    logger.info(f"二维码监控超时，标记为过期: {session_id}")

        except Exception as exc:
            logger.error(f"监控二维码状态失败: {type(exc).__name__}")
            if session_id in self.sessions:
                if preserve_verification and self.sessions[session_id].verification_url:
                    self.sessions[session_id].status = 'verification_required'
                else:
                    self._mark_terminal(
                        self.sessions[session_id],
                        'expired',
                        '二维码已过期，请重新扫码',
                    )

    def _ensure_verification_browser(self, session_id: str):
        """确保二次验证浏览器会话已启动。"""
        session = self.sessions.get(session_id)
        if not session or not session.verification_url:
            return

        task = session.verification_task
        if task and not task.done():
            return

        try:
            loop = asyncio.get_running_loop()
            session.verification_browser_status = 'starting'
            session.verification_error = None
            session.verification_task = loop.create_task(self._run_verification_browser(session_id))
            logger.info(f"扫码二次验证浏览器任务已启动: {session_id}")
        except RuntimeError as exc:
            session.verification_browser_status = 'failed'
            session.verification_error = '无法启动安全验证浏览器任务'
            logger.error(
                f"启动扫码二次验证浏览器任务失败: {session_id}, "
                f"错误类型: {type(exc).__name__}"
            )

    def _apply_verification_browser_update(
        self,
        session_id: str,
        update: Dict[str, object],
    ):
        """接收浏览器线程回传的安全验证页面状态。"""
        session = self.sessions.get(session_id)
        if not session or session.status in {'success', 'expired', 'cancelled', 'error'}:
            return

        screenshot_path = update.get('verification_screenshot_path')
        if screenshot_path and screenshot_path != session.verification_screenshot_path:
            remove_verification_screenshot(session.verification_screenshot_path)
            session.verification_screenshot_path = screenshot_path

        browser_status = update.get('verification_browser_status')
        if browser_status:
            session.verification_browser_status = browser_status

        verification_kind = update.get('verification_kind')
        if verification_kind in {'mobile_scan', 'interactive', 'unknown'}:
            session.verification_kind = verification_kind

        required_action = update.get('required_action')
        if required_action in {'scan_image', 'interact_in_console'}:
            session.required_action = str(required_action)

    def _should_stop_verification_browser(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if not session:
            return True
        if session.status not in ['verification_required', 'verification_checking']:
            return True
        return session.is_verification_expired()

    async def _run_verification_browser(self, session_id: str):
        """后台运行真实阿里验证页，并在验证完成后写回 Cookie。"""
        session = self.sessions.get(session_id)
        if not session or not session.verification_url:
            return

        result = await asyncio.to_thread(
            self.verification_browser.run,
            session_id,
            session.verification_url,
            dict(session.cookies),
            min(450, session.verification_expire_time),
            lambda update: self._apply_verification_browser_update(session_id, update),
            lambda: self._should_stop_verification_browser(session_id),
            session.interaction_channel,
        )

        session = self.sessions.get(session_id)
        if not session:
            remove_verification_screenshot(result.get('screenshot_path'))
            return

        status = result.get('status')
        if status == 'success':
            cookies = result.get('cookies') or {}
            unb = result.get('unb') or cookies.get('unb')
            if cookies and unb:
                session.cookies.update(cookies)
                session.unb = unb
                validated = await self._validate_candidate_session(session)
                if validated:
                    try:
                        self.verification_browser.promote_profile(session_id, str(unb))
                    except Exception as exc:
                        session.status = 'verification_required'
                        session.validated = False
                        session.verification_browser_status = 'failed'
                        session.verification_error = '官方登录完成，但专用浏览器档案保存失败'
                        logger.error(
                            f"扫码二次验证档案归档失败: {session_id}, "
                            f"error={type(exc).__name__}"
                        )
                        return
                    session.verification_browser_status = 'success'
                    session.verification_error = None
                    remove_verification_screenshot(session.verification_screenshot_path)
                    session.verification_screenshot_path = None
                    logger.info(
                        f"扫码二次验证登录成功: {session_id}, "
                        f"cookie_count={len(cookies)}, has_unb={bool(unb)}"
                    )
                elif session.status == 'verification_required':
                    session.verification_browser_status = 'failed'
                    session.verification_error = '官方窗口完成后，平台仍要求继续验证'
                else:
                    self.verification_browser.discard_profile(session_id)
            else:
                session.status = 'verification_required'
                session.verification_browser_status = 'failed'
                session.verification_error = '安全验证完成后未获取到可用登录 Cookie'
                logger.warning(f"扫码二次验证未获取到可用登录 Cookie: {session_id}")
        elif status == 'timeout':
            self._mark_terminal(
                session,
                'expired',
                '二维码已过期，请重新扫码',
            )
            session.verification_browser_status = 'timeout'
            session.verification_error = '等待安全验证超时'
            remove_verification_screenshot(session.verification_screenshot_path)
            session.verification_screenshot_path = None
        elif status == 'cancelled':
            remove_verification_screenshot(result.get('screenshot_path'))
            session.verification_browser_status = 'cancelled'
        else:
            session.status = 'verification_required'
            session.verification_browser_status = 'failed'
            session.verification_error = '安全验证浏览器处理失败'
            logger.warning(f"扫码二次验证浏览器处理失败: {session_id}, 状态: {status}")

    def _cleanup_verification_artifacts(self, session: QRLoginSession):
        session.interaction_channel.close()
        remove_verification_screenshot(session.verification_screenshot_path)
        session.verification_screenshot_path = None
        task = session.verification_task
        if not task or task.done():
            self.verification_browser.discard_profile(session.session_id)

    def continue_after_verification(self, session_id: str) -> Dict[str, Any]:
        """Compatibility entry that ensures the verification renderer is running."""
        session = self.sessions.get(session_id)
        if not session:
            return {'status': 'not_found', 'message': '二维码会话不存在或已过期'}

        if session.status not in ['verification_required', 'verification_checking']:
            return self.get_session_status(session_id)

        if session.is_verification_expired():
            self._mark_terminal(
                session,
                'expired',
                '二维码已过期，请重新扫码',
            )
            self._cleanup_verification_artifacts(session)
            return {'status': 'expired', 'message': '二维码已过期，请重新扫码'}

        session.required_action = "continue_in_client_browser"
        session.message = '请在当前设备浏览器继续完成安全验证'
        return self.get_session_status(session_id)

    def cancel_session(
        self,
        session_id: str,
        *,
        ended_by: str = "user_cancelled",
    ) -> Dict[str, Any]:
        session = self.sessions.get(session_id)
        if not session:
            return {'status': 'not_found', 'message': '二维码会话不存在或已过期'}
        if session.status in {'success', 'expired', 'cancelled', 'error'}:
            return self.get_session_status(session_id)
        message = (
            '已切换到你的 Chrome，原二维码会话已结束'
            if ended_by == 'switched_to_extension'
            else '扫码登录已取消'
        )
        self._mark_terminal(
            session,
            'cancelled',
            message,
            ended_by=ended_by,
        )
        session.error_code = 'cancelled'
        session.verification_browser_status = 'cancelled'
        self._cleanup_verification_artifacts(session)
        return self.get_session_status(session_id)

    def mark_persisted(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session and session.status == 'success' and session.validated:
            session.ended_by = 'validated_and_persisted'

    def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """获取会话状态"""
        session = self.sessions.get(session_id)
        if not session:
            return {'status': 'not_found'}

        if session.status in ['verification_required', 'verification_checking']:
            if session.is_verification_expired():
                self._mark_terminal(
                    session,
                    'expired',
                    '二维码已过期，请重新扫码',
                )
        elif session.is_expired() and session.status != 'success':
            self._mark_terminal(
                session,
                'expired',
                '二维码已过期，请重新扫码',
            )

        if session.status == 'expired':
            self._cleanup_verification_artifacts(session)

        public_status = 'processing' if session.status == 'verification_checking' else session.status
        result = {
            'status': public_status,
            'session_id': session_id,
            'verification_kind': session.verification_kind,
            'required_action': session.required_action,
            'browser_active': bool(
                session.verification_task and not session.verification_task.done()
            ),
            'ended_by': session.ended_by,
            **session.interaction_channel.snapshot(),
        }
        logger.info(f"获取二维码会话状态: status={public_status}")

        if session.status in ['verification_required', 'verification_checking'] and session.verification_url:
            result['verification_screenshot_path'] = (
                f"/qr-login/verification-image/{session_id}"
                if (
                    session.verification_screenshot_path
                    or session.interaction_channel.latest_frame() is not None
                )
                else None
            )
            result['verification_browser_status'] = session.verification_browser_status
            if session.verification_browser_status == 'failed':
                result['message'] = session.verification_error or '安全验证浏览器处理失败，请重新生成二维码'
            elif session.verification_browser_status == 'starting':
                result['message'] = '正在识别闲鱼安全验证方式，请稍候'
            elif session.required_action == 'interact_in_console':
                result['message'] = '请直接在监控台完成滑块、人脸或页面验证，系统会持续检测登录结果'
            elif session.required_action == 'scan_image' and session.verification_screenshot_path:
                result['message'] = '请用手机版闲鱼扫描图中的身份验证二维码，完成后系统会自动检测'
            else:
                result['message'] = session.message or '正在识别闲鱼安全验证方式'

        if session.status in {'error', 'expired', 'cancelled'} and session.message:
            result['message'] = session.message
            if session.error_code:
                result['error_code'] = session.error_code

        # 如果登录成功，返回Cookie信息
        if session.status == 'success' and session.validated and session.cookies and session.unb:
            result['cookies'] = self._cookie_marshal(session.cookies)
            result['unb'] = session.unb

        return result

    def get_interaction_frame(
        self,
        session_id: str,
    ) -> Optional[tuple[bytes, int]]:
        session = self.sessions.get(session_id)
        if session is None or session.status not in {
            'verification_required',
            'verification_checking',
        }:
            return None
        if session.required_action != 'interact_in_console':
            return None
        return session.interaction_channel.latest_frame()

    def submit_interaction(
        self,
        session_id: str,
        payload: Dict[str, Any],
    ) -> int:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        if session.status not in {
            'verification_required',
            'verification_checking',
        }:
            raise RuntimeError("二维码验证会话已结束")
        if session.required_action != 'interact_in_console':
            raise RuntimeError("当前二维码验证页面不需要远程操作")
        return session.interaction_channel.submit(payload)

    def get_verification_image_path(self, session_id: str) -> Optional[str]:
        session = self.sessions.get(session_id)
        if session is None or session.status not in {
            'verification_required',
            'verification_checking',
        }:
            return None
        resolved = resolve_private_verification_image(
            session.verification_screenshot_path
        )
        return str(resolved) if resolved is not None else None

    def cleanup_expired_sessions(self, *, now: Optional[float] = None):
        """Retain an explicit expired result before deleting terminal state."""
        current_time = time.time() if now is None else now
        sessions_to_delete = []
        for session_id, session in list(self.sessions.items()):
            if session.status in ['verification_required', 'verification_checking']:
                expired = current_time - session.created_time > session.verification_expire_time
            else:
                expired = current_time - session.created_time > session.expire_time

            if expired and session.status not in {'success', 'cancelled', 'error', 'expired'}:
                self._mark_terminal(
                    session,
                    'expired',
                    '二维码已过期，请重新扫码',
                    now=current_time,
                )
                self._cleanup_verification_artifacts(session)

            if session.status in {'expired', 'cancelled', 'error'}:
                if session.terminal_at is None:
                    session.terminal_at = current_time
                self._cleanup_verification_artifacts(session)
                if current_time - session.terminal_at > self.terminal_retention_seconds:
                    sessions_to_delete.append(session_id)

        for session_id in sessions_to_delete:
            del self.sessions[session_id]
            logger.info(f"清理二维码终态会话: {session_id}")

    def get_session_cookies(self, session_id: str) -> Optional[Dict[str, str]]:
        """获取会话Cookie"""
        session = self.sessions.get(session_id)
        if session and session.status == 'success' and session.validated:
            return {
                'cookies': self._cookie_marshal(session.cookies),
                'unb': session.unb
            }
        return None

    def remove_session(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is not None:
            self._cleanup_verification_artifacts(session)

# 全局二维码登录管理器实例
qr_login_manager = QRLoginManager()
