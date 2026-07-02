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
from utils.qr_verification_browser import QRVerificationBrowser, remove_public_screenshot


def generate_headers():
    """生成请求头"""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
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

    def __init__(self):
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
        self.verification_browser = QRVerificationBrowser(headless=True)

    def _cookie_marshal(self, cookies: dict) -> str:
        """将Cookie字典转换为字符串"""
        return "; ".join([f"{k}={v}" for k, v in cookies.items()])

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

    async def _get_mh5tk(self, session: QRLoginSession) -> dict:
        """获取m_h5_tk和m_h5_tk_enc"""
        data = {"bizScene": "home"}
        data_str = json.dumps(data, separators=(',', ':'))
        t = str(int(time.time() * 1000))
        app_key = "34839810"

        # 先发一次 GET 请求，获取 cookie 中的 m_h5_tk
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, proxy=self.proxy) as client:
            try:
                resp = await client.get(self.api_h5_tk, headers=self.headers)
                cookies = {k: v for k, v in resp.cookies.items()}
                session.cookies.update(cookies)

                m_h5_tk = cookies.get("m_h5_tk", "")
                token = m_h5_tk.split("_")[0] if "_" in m_h5_tk else ""

                # 生成签名
                sign_input = f"{token}&{t}&{app_key}&{data_str}"
                sign = hashlib.md5(sign_input.encode()).hexdigest()

                # 构造最终请求参数
                params = {
                    "jsv": "2.7.2",
                    "appKey": app_key,
                    "t": t,
                    "sign": sign,
                    "v": "1.0",
                    "type": "originaljson",
                    "dataType": "json",
                    "timeout": 20000,
                    "api": "mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get",
                    "data": data_str,
                }

                # 发请求正式获取数据，确保 token 有效
                await client.post(self.api_h5_tk, params=params, headers=self.headers, cookies=session.cookies)

                return cookies
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
                    headers=self.headers
                )
                logger.debug(f"[调试] 获取二维码接口原始响应: {resp.text}")

                try:
                    results = resp.json()
                    logger.debug(f"[调试] 获取二维码接口解析后: {json.dumps(results, ensure_ascii=False)}")
                except Exception as e:
                    logger.exception("二维码接口返回不是JSON")
                    raise GetLoginQRCodeError(f"二维码接口返回异常: {resp.text}")

                if results.get("content", {}).get("success") == True:
                    # 更新会话参数
                    session.params.update({
                        "t": results["content"]["data"]["t"],
                        "ck": results["content"]["data"]["ck"],
                    })

                    # 获取二维码内容
                    qr_content = results["content"]["data"]["codeContent"]
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

        except httpx.ConnectTimeout as e:
            logger.error(f"连接超时: {e}")
            return {'success': False, 'message': f'连接超时，请检查网络或尝试使用代理'}
        except httpx.ReadTimeout as e:
            logger.error(f"读取超时: {e}")
            return {'success': False, 'message': f'读取超时，服务器响应过慢'}
        except httpx.ConnectError as e:
            logger.error(f"连接错误: {e}")
            return {'success': False, 'message': f'连接错误，请检查网络或代理设置'}
        except Exception as e:
            logger.exception("二维码生成过程中发生异常")
            return {'success': False, 'message': f'生成二维码失败: {str(e)}'}

    async def _poll_qrcode_status(self, session: QRLoginSession) -> httpx.Response:
        """获取二维码扫描状态"""
        async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout, proxy=self.proxy) as client:
            resp = await client.post(
                self.api_scan_status,
                data=session.params,
                cookies=session.cookies,
                headers=self.headers,
            )
            return resp

    async def _monitor_qr_status(self, session_id: str, max_wait_time: int = 300, preserve_verification: bool = False):
        """监控二维码状态"""
        try:
            session = self.sessions.get(session_id)
            if not session:
                return

            logger.info(f"开始监控二维码状态: {session_id}")

            # 监控登录状态
            start_time = time.time()

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
                            iframe_url = (
                                resp.json()
                                .get("content", {})
                                .get("data", {})
                                .get("iframeRedirectUrl")
                            )
                            session.verification_url = iframe_url
                            session.verification_browser_status = 'starting' if iframe_url else 'failed'
                            session.verification_error = None if iframe_url else '未获取到安全验证链接'
                            self._ensure_verification_browser(session_id)
                            logger.warning(f"账号被风控，需要手机验证: {session_id}, 已保存验证链接")
                            break
                        else:
                            # 登录成功
                            session.status = 'success'

                            # 保存Cookie
                            for k, v in resp.cookies.items():
                                session.cookies[k] = v
                                if k == 'unb':
                                    session.unb = v

                            logger.info(f"扫码登录成功: {session_id}, UNB: {session.unb}")
                            break

                    elif qrcode_status == "NEW":
                        # 二维码未被扫描，继续轮询
                        continue

                    elif qrcode_status == "EXPIRED":
                        if preserve_verification or session.verification_url:
                            session.status = 'verification_required'
                            logger.info(f"二维码查询显示过期，保留安全验证状态: {session_id}")
                            break
                        # 二维码已过期
                        session.status = 'expired'
                        logger.info(f"二维码已过期: {session_id}")
                        break

                    elif qrcode_status == "SCANED":
                        # 二维码已被扫描，等待确认
                        if session.status == 'waiting':
                            session.status = 'scanned'
                            logger.info(f"二维码已扫描，等待确认: {session_id}")
                    else:
                        # 用户取消确认
                        session.status = 'cancelled'
                        logger.info(f"用户取消登录: {session_id}")
                        break

                    await asyncio.sleep(0.8)  # 每0.8秒检查一次

                except Exception as e:
                    logger.error(f"监控二维码状态异常: {e}")
                    await asyncio.sleep(2)

            # 超时处理
            if session.status not in ['success', 'expired', 'cancelled', 'verification_required']:
                if preserve_verification and session.verification_url:
                    session.status = 'verification_required'
                    logger.info(f"未检测到安全验证通过，保留安全验证状态: {session_id}")
                else:
                    session.status = 'expired'
                    logger.info(f"二维码监控超时，标记为过期: {session_id}")

        except Exception as e:
            logger.error(f"监控二维码状态失败: {e}")
            if session_id in self.sessions:
                if preserve_verification and self.sessions[session_id].verification_url:
                    self.sessions[session_id].status = 'verification_required'
                else:
                    self.sessions[session_id].status = 'expired'

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
            logger.error(f"启动扫码二次验证浏览器任务失败: {session_id}, 错误: {exc}")

    def _apply_verification_browser_update(self, session_id: str, update: Dict[str, str]):
        """接收浏览器线程回传的安全验证页面状态。"""
        session = self.sessions.get(session_id)
        if not session:
            return

        screenshot_path = update.get('verification_screenshot_path')
        if screenshot_path and screenshot_path != session.verification_screenshot_path:
            remove_public_screenshot(session.verification_screenshot_path)
            session.verification_screenshot_path = screenshot_path

        browser_status = update.get('verification_browser_status')
        if browser_status:
            session.verification_browser_status = browser_status

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
        )

        session = self.sessions.get(session_id)
        if not session:
            remove_public_screenshot(result.get('screenshot_path'))
            return

        status = result.get('status')
        if status == 'success':
            cookies = result.get('cookies') or {}
            unb = result.get('unb') or cookies.get('unb')
            if cookies and unb:
                session.cookies.update(cookies)
                session.unb = unb
                session.status = 'success'
                session.verification_browser_status = 'success'
                session.verification_error = None
                remove_public_screenshot(session.verification_screenshot_path)
                session.verification_screenshot_path = None
                logger.info(
                    f"扫码二次验证登录成功: {session_id}, "
                    f"cookie_count={len(cookies)}, has_unb={bool(unb)}"
                )
            else:
                session.status = 'verification_required'
                session.verification_browser_status = 'failed'
                session.verification_error = '安全验证完成后未获取到可用登录 Cookie'
                logger.warning(f"扫码二次验证未获取到可用登录 Cookie: {session_id}")
        elif status == 'timeout':
            session.status = 'expired'
            session.verification_browser_status = 'timeout'
            session.verification_error = result.get('message') or '等待安全验证超时'
            remove_public_screenshot(session.verification_screenshot_path)
            session.verification_screenshot_path = None
        elif status == 'cancelled':
            session.verification_browser_status = 'cancelled'
        else:
            session.status = 'verification_required'
            session.verification_browser_status = 'failed'
            session.verification_error = result.get('message') or '安全验证浏览器处理失败'
            logger.warning(f"扫码二次验证浏览器处理失败: {session_id}, 状态: {status}")

    def _cleanup_verification_artifacts(self, session: QRLoginSession):
        remove_public_screenshot(session.verification_screenshot_path)
        session.verification_screenshot_path = None
        task = session.verification_task
        if task and not task.done():
            task.cancel()

    def continue_after_verification(self, session_id: str) -> Dict[str, Any]:
        """用户完成安全验证后，检查浏览器托管验证结果。"""
        session = self.sessions.get(session_id)
        if not session:
            return {'status': 'not_found', 'message': '二维码会话不存在或已过期'}

        if session.status not in ['verification_required', 'verification_checking']:
            return self.get_session_status(session_id)

        if session.is_verification_expired():
            session.status = 'expired'
            return {'status': 'expired', 'message': '安全验证会话已过期，请重新生成二维码'}

        self._ensure_verification_browser(session_id)
        return self.get_session_status(session_id)

    def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """获取会话状态"""
        session = self.sessions.get(session_id)
        if not session:
            return {'status': 'not_found'}

        if session.status in ['verification_required', 'verification_checking']:
            if session.is_verification_expired():
                session.status = 'expired'
        elif session.is_expired() and session.status != 'success':
            session.status = 'expired'

        public_status = 'processing' if session.status == 'verification_checking' else session.status
        result = {
            'status': public_status,
            'session_id': session_id
        }
        logger.info(f"获取会话状态: {result}")

        if session.status in ['verification_required', 'verification_checking'] and session.verification_url:
            result['verification_screenshot_path'] = session.verification_screenshot_path
            result['verification_browser_status'] = session.verification_browser_status
            if session.verification_browser_status == 'failed':
                result['message'] = session.verification_error or '安全验证浏览器处理失败，请重新生成二维码'
            elif session.verification_browser_status == 'starting':
                result['message'] = '正在打开闲鱼安全验证页面，请稍候'
            elif session.verification_screenshot_path:
                result['message'] = '请用手机版闲鱼扫描图中的身份验证二维码，完成后系统会自动检测'
            else:
                result['message'] = '闲鱼要求完成安全验证，正在获取验证页面'

        # 如果登录成功，返回Cookie信息
        if session.status == 'success' and session.cookies and session.unb:
            result['cookies'] = self._cookie_marshal(session.cookies)
            result['unb'] = session.unb

        return result

    def cleanup_expired_sessions(self):
        """清理过期会话"""
        expired_sessions = []
        for session_id, session in self.sessions.items():
            if session.status in ['verification_required', 'verification_checking']:
                if session.is_verification_expired():
                    expired_sessions.append(session_id)
            elif session.is_expired():
                expired_sessions.append(session_id)

        for session_id in expired_sessions:
            self._cleanup_verification_artifacts(self.sessions[session_id])
            del self.sessions[session_id]
            logger.info(f"清理过期会话: {session_id}")

    def get_session_cookies(self, session_id: str) -> Optional[Dict[str, str]]:
        """获取会话Cookie"""
        session = self.sessions.get(session_id)
        if session and session.status == 'success':
            return {
                'cookies': self._cookie_marshal(session.cookies),
                'unb': session.unb
            }
        return None

# 全局二维码登录管理器实例
qr_login_manager = QRLoginManager()
