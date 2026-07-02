#!/usr/bin/env python3
"""
扫码登录二次验证浏览器托管流程。

API 扫码遇到风控时，只会返回一个验证页 URL；真正给用户扫描的是该页面
内部渲染的身份验证二维码。因此这里用 Playwright 打开验证页，保存真实页面
截图，并在同一个浏览器上下文里等待验证完成后的 Cookie。
"""

import os
import shutil
import tempfile
import time
from datetime import datetime
from typing import Callable, Dict, Optional
from urllib.parse import urlparse

from loguru import logger


BrowserUpdateCallback = Callable[[Dict[str, str]], None]
StopCallback = Callable[[], bool]


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(PROJECT_ROOT, "static", "uploads", "images")


def remove_public_screenshot(public_path: Optional[str]) -> None:
    """删除由浏览器验证流程生成的公开截图。"""
    if not public_path or not public_path.startswith("/static/uploads/images/"):
        return

    filename = os.path.basename(public_path)
    full_path = os.path.join(UPLOAD_DIR, filename)
    try:
        if os.path.exists(full_path):
            os.remove(full_path)
            logger.info(f"已删除扫码二次验证截图: {filename}")
    except Exception as exc:
        logger.warning(f"删除扫码二次验证截图失败: {filename}, 错误: {exc}")


class QRVerificationBrowser:
    """在后台浏览器中承载扫码二次验证。"""

    def __init__(self, headless: bool = True):
        self.headless = headless
        os.makedirs(UPLOAD_DIR, exist_ok=True)

    def run(
        self,
        session_id: str,
        verification_url: str,
        initial_cookies: Optional[Dict[str, str]] = None,
        max_wait_time: int = 450,
        on_update: Optional[BrowserUpdateCallback] = None,
        should_stop: Optional[StopCallback] = None,
    ) -> Dict[str, object]:
        """打开验证页并等待用户完成身份验证。"""
        if not verification_url:
            return {
                "status": "failed",
                "message": "缺少安全验证链接",
            }

        safe_session_id = session_id.replace("-", "")[:12]
        user_data_dir = tempfile.mkdtemp(prefix=f"xianyu_qr_verify_{safe_session_id}_")
        screenshot_path: Optional[str] = None

        try:
            from playwright.sync_api import sync_playwright

            parsed = urlparse(verification_url)
            logger.info(
                f"扫码二次验证浏览器启动: session={session_id}, "
                f"host={parsed.netloc or 'unknown'}"
            )

            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir,
                    headless=self.headless,
                    args=self._browser_args(),
                    viewport={"width": 1280, "height": 860},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    locale="zh-CN",
                    ignore_https_errors=True,
                    extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.add_init_script(
                    """
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    window.chrome = window.chrome || { runtime: {} };
                    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
                    """
                )

                self._add_initial_cookies(context, initial_cookies or {})

                page.goto(verification_url, wait_until="domcontentloaded", timeout=60000)
                ready_for_screenshot = self._wait_for_verification_content(
                    page,
                    session_id,
                    timeout=30,
                )
                if not ready_for_screenshot:
                    logger.warning(f"扫码二次验证页面30秒内未检测到二维码，保存当前页面用于诊断: session={session_id}")

                screenshot_path = self._capture_screenshot(page, session_id)
                if screenshot_path and on_update:
                    on_update({
                        "verification_screenshot_path": screenshot_path,
                        "verification_browser_status": "waiting",
                    })

                started_at = time.time()
                last_screenshot_at = time.time()
                redirected_after_success_hint = False

                while time.time() - started_at < max_wait_time:
                    if should_stop and should_stop():
                        logger.info(f"扫码二次验证浏览器收到停止信号: session={session_id}")
                        return {
                            "status": "cancelled",
                            "screenshot_path": screenshot_path,
                            "message": "验证会话已停止",
                        }

                    cookies = self._cookies_to_dict(context.cookies())
                    if self._has_login_cookie(cookies):
                        logger.info(
                            f"扫码二次验证已获取登录 Cookie: session={session_id}, "
                            f"cookie_count={len(cookies)}, has_unb={bool(cookies.get('unb'))}"
                        )
                        return {
                            "status": "success",
                            "cookies": cookies,
                            "unb": cookies.get("unb"),
                            "screenshot_path": screenshot_path,
                        }

                    if not redirected_after_success_hint and self._has_success_hint(page):
                        redirected_after_success_hint = True
                        logger.info(f"扫码二次验证页面提示成功，尝试进入闲鱼页面换取 Cookie: session={session_id}")
                        try:
                            page.goto("https://www.goofish.com/im", wait_until="domcontentloaded", timeout=45000)
                            time.sleep(3)
                        except Exception as exc:
                            logger.debug(f"扫码二次验证成功后跳转闲鱼页面失败: session={session_id}, 错误: {exc}")

                    if self._looks_logged_in(page):
                        cookies = self._cookies_to_dict(context.cookies())
                        if cookies:
                            logger.info(
                                f"扫码二次验证检测到页面登录态: session={session_id}, "
                                f"cookie_count={len(cookies)}, has_unb={bool(cookies.get('unb'))}"
                            )
                            return {
                                "status": "success",
                                "cookies": cookies,
                                "unb": cookies.get("unb"),
                                "screenshot_path": screenshot_path,
                            }

                    if time.time() - last_screenshot_at >= 8:
                        updated_screenshot = self._capture_screenshot(page, session_id)
                        if updated_screenshot:
                            screenshot_path = updated_screenshot
                            if on_update:
                                on_update({
                                    "verification_screenshot_path": screenshot_path,
                                    "verification_browser_status": "waiting",
                                })
                        last_screenshot_at = time.time()

                    time.sleep(2)

                logger.warning(f"扫码二次验证等待超时: session={session_id}")
                return {
                    "status": "timeout",
                    "screenshot_path": screenshot_path,
                    "message": "等待安全验证超时，请重新生成二维码",
                }

        except Exception as exc:
            logger.error(f"扫码二次验证浏览器异常: session={session_id}, 错误: {exc}")
            return {
                "status": "failed",
                "screenshot_path": screenshot_path,
                "message": str(exc),
            }
        finally:
            try:
                shutil.rmtree(user_data_dir, ignore_errors=True)
            except Exception as exc:
                logger.debug(f"清理扫码二次验证浏览器目录失败: {exc}")

    def _browser_args(self) -> list:
        return [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-web-security",
            "--disable-features=VizDisplayCompositor,TranslateUI",
            "--disable-blink-features=AutomationControlled",
            "--disable-extensions",
            "--disable-plugins",
            "--disable-sync",
            "--disable-translate",
            "--disable-popup-blocking",
            "--disable-notifications",
            "--lang=zh-CN",
            "--window-size=1280,860",
            "--force-color-profile=srgb",
        ]

    def _add_initial_cookies(self, context, cookies: Dict[str, str]) -> None:
        if not cookies:
            return

        cookies_to_add = []
        domains = ["passport.goofish.com", ".goofish.com"]
        for name, value in cookies.items():
            if not name or value is None:
                continue
            for domain in domains:
                cookies_to_add.append({
                    "name": str(name),
                    "value": str(value),
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                    "httpOnly": False,
                    "sameSite": "Lax",
                })

        try:
            context.add_cookies(cookies_to_add)
            logger.info(f"扫码二次验证浏览器已注入初始 Cookie 字段数: {len(cookies)}")
        except Exception as exc:
            logger.debug(f"扫码二次验证浏览器注入初始 Cookie 失败: {exc}")

    def _wait_for_verification_content(self, page, session_id: str, timeout: int = 30) -> bool:
        """等待阿里身份验证页把二维码或验证内容真正渲染出来。"""
        started_at = time.time()
        while time.time() - started_at < timeout:
            elapsed = int(time.time() - started_at)
            if self._has_qr_content(page):
                logger.info(f"扫码二次验证页面已检测到验证内容: session={session_id}, elapsed={elapsed}s")
                # 给二维码图片/canvas 最后一点绘制时间，避免截到半渲染状态。
                time.sleep(1)
                return True
            if elapsed >= 8 and self._has_verification_keywords(page):
                logger.info(f"扫码二次验证页面已检测到验证文案: session={session_id}, elapsed={elapsed}s")
                time.sleep(1)
                return True
            time.sleep(1)
        return False

    def _has_qr_content(self, page) -> bool:
        if self._scope_has_qr_signal(page):
            return True

        for frame in page.frames:
            try:
                if self._scope_has_qr_signal(frame):
                    return True
            except Exception:
                continue

        return False

    def _has_verification_keywords(self, page) -> bool:
        if self._scope_has_verification_keywords(page):
            return True

        for frame in page.frames:
            try:
                if self._scope_has_verification_keywords(frame):
                    return True
            except Exception:
                continue

        return False

    def _scope_has_qr_signal(self, scope) -> bool:
        qr_selectors = [
            'img[alt*="二维码"]',
            'img[alt*="扫码"]',
            'img[src*="qrcode"]',
            'img[src*="qr"]',
            'canvas[class*="qrcode"]',
            'canvas[id*="qrcode"]',
            'canvas[class*="qr"]',
            'canvas[id*="qr"]',
            '.qr-code',
            '#qr-code',
            '.qrcode',
            '#qrcode',
            '[class*="qr-code"]',
            '[id*="qr-code"]',
            '[class*="qrcode"]',
            '[id*="qrcode"]',
        ]

        for selector in qr_selectors:
            try:
                elements = scope.query_selector_all(selector)
                for element in elements:
                    if self._is_visible_qr_sized(element):
                        return True
            except Exception:
                continue

        for selector in ["canvas", "img"]:
            try:
                elements = scope.query_selector_all(selector)
                for element in elements:
                    if self._is_visible_qr_sized(element):
                        return True
            except Exception:
                continue

        return False

    def _is_visible_qr_sized(self, element) -> bool:
        try:
            if not element.is_visible():
                return False
            box = element.bounding_box()
            if not box:
                return False
            width = box.get("width", 0)
            height = box.get("height", 0)
            if width < 120 or height < 120:
                return False
            ratio = width / height if height else 0
            if not (0.55 <= ratio <= 1.8):
                return False
            return self._element_has_rendered_content(element)
        except Exception:
            return False

    def _element_has_rendered_content(self, element) -> bool:
        try:
            return bool(element.evaluate(
                """
                (el) => {
                  const tag = el.tagName ? el.tagName.toLowerCase() : '';
                  if (tag === 'img') {
                    return Boolean(el.complete && el.naturalWidth >= 80 && el.naturalHeight >= 80);
                  }
                  if (tag === 'canvas') {
                    const width = el.width || el.clientWidth;
                    const height = el.height || el.clientHeight;
                    if (!width || !height) return false;
                    const ctx = el.getContext && el.getContext('2d');
                    if (!ctx) return false;
                    try {
                      const sampleWidth = Math.min(width, 160);
                      const sampleHeight = Math.min(height, 160);
                      const data = ctx.getImageData(0, 0, sampleWidth, sampleHeight).data;
                      let dark = 0;
                      let light = 0;
                      let opaque = 0;
                      for (let i = 0; i < data.length; i += 16) {
                        const r = data[i];
                        const g = data[i + 1];
                        const b = data[i + 2];
                        const a = data[i + 3];
                        if (a > 20) opaque++;
                        const sum = r + g + b;
                        if (a > 20 && sum < 420) dark++;
                        if (a > 20 && sum > 650) light++;
                      }
                      return opaque > 40 && dark > 10 && light > 10;
                    } catch (error) {
                      return true;
                    }
                  }
                  return true;
                }
                """
            ))
        except Exception:
            return True

    def _scope_has_verification_keywords(self, scope) -> bool:
        try:
            body_text = scope.locator("body").inner_text(timeout=1000)
        except Exception:
            return False

        keywords = [
            "请用手机版闲鱼扫描二维码",
            "手机版闲鱼扫描二维码",
            "闲鱼扫描二维码",
            "扫描二维码",
            "身份验证",
            "拍摄脸部",
            "扫码完成后",
        ]
        return any(keyword in body_text for keyword in keywords)

    def _capture_screenshot(self, page, session_id: str) -> Optional[str]:
        safe_session_id = session_id.replace("-", "")[:12]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"qr_verify_{safe_session_id}_{timestamp}.png"
        full_path = os.path.join(UPLOAD_DIR, filename)

        try:
            target = None
            target = self._find_ready_iframe_element(page)
            if target is None:
                for selector in [
                    "iframe#alibaba-login-box",
                    "iframe[src*='mini_login']",
                    "iframe[src*='havana']",
                ]:
                    try:
                        element = page.query_selector(selector)
                        if element and element.is_visible():
                            target = element
                            break
                    except Exception:
                        continue

            if target:
                target.screenshot(path=full_path)
            else:
                page.screenshot(path=full_path, full_page=False)

            logger.info(f"扫码二次验证截图已保存: {filename}")
            return f"/static/uploads/images/{filename}"
        except Exception as exc:
            logger.warning(f"扫码二次验证截图失败: session={session_id}, 错误: {exc}")
            return None

    def _find_ready_iframe_element(self, page):
        try:
            iframes = page.query_selector_all("iframe")
        except Exception:
            return None

        for iframe in iframes:
            try:
                if not iframe.is_visible():
                    continue
                frame = iframe.content_frame()
                if frame and (
                    self._scope_has_qr_signal(frame)
                    or self._scope_has_verification_keywords(frame)
                ):
                    return iframe
            except Exception:
                continue

        return None

    def _cookies_to_dict(self, cookies_list) -> Dict[str, str]:
        cookies: Dict[str, str] = {}
        for cookie in cookies_list or []:
            name = cookie.get("name")
            value = cookie.get("value")
            if name and value is not None:
                cookies[name] = value
        return cookies

    def _has_login_cookie(self, cookies: Dict[str, str]) -> bool:
        return bool(cookies.get("unb"))

    def _has_success_hint(self, page) -> bool:
        try:
            body_text = page.locator("body").inner_text(timeout=1000)
        except Exception:
            return False

        success_keywords = [
            "验证成功",
            "身份验证成功",
            "已完成验证",
            "验证已完成",
            "登录成功",
        ]
        return any(keyword in body_text for keyword in success_keywords)

    def _looks_logged_in(self, page) -> bool:
        try:
            element = page.query_selector(".rc-virtual-list-holder-inner")
            if not element or not element.is_visible():
                return False
            child_count = element.evaluate("el => el.children.length")
            return child_count > 0
        except Exception:
            return False
