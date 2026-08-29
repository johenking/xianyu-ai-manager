#!/usr/bin/env python3
"""
扫码登录二次验证浏览器托管流程。

API 扫码遇到风控时，只会返回一个验证页 URL；真正给用户扫描的是该页面
内部渲染的身份验证二维码。因此这里用 Playwright 打开验证页，保存真实页面
截图，并在同一个浏览器上下文里等待验证完成后的 Cookie。
"""

import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from loguru import logger
from utils.account_fingerprint import build_browser_fingerprint
from utils.browser_interaction import BrowserInteractionChannel
from utils.browser_runtime import (
    chromium_runtime_options,
    classify_browser_launch_error,
)
from utils.verification_images import (
    ensure_private_verification_root,
    remove_private_verification_image,
)
from utils.xianyu_session_probe import (
    SessionProbeResult,
    detect_default_browser_user_agent,
    probe_message_session_sync,
)


BrowserUpdateCallback = Callable[[Dict[str, object]], None]
StopCallback = Callable[[], bool]


def remove_verification_screenshot(path: Optional[str]) -> None:
    remove_private_verification_image(path)


class QRVerificationBrowser:
    """在后台浏览器中承载扫码二次验证。"""

    def __init__(
        self,
        profile_root: Path | str = "browser_data",
        verification_root: Path | str | None = None,
        *,
        playwright_factory: Optional[Callable[[], Any]] = None,
        session_validator: Optional[
            Callable[[str, str], SessionProbeResult]
        ] = probe_message_session_sync,
        proxy: Any = None,
    ):
        self.profile_root = Path(profile_root)
        self.verification_root = ensure_private_verification_root(verification_root)
        self.playwright_factory = playwright_factory or self._default_playwright_factory
        self.session_validator = session_validator
        self._proxy = proxy

    @staticmethod
    def _default_playwright_factory():
        from playwright.sync_api import sync_playwright

        return sync_playwright()

    @staticmethod
    def _add_fingerprint_script(context: Any, script: str) -> None:
        """Best-effort context-level fingerprint init script; never break verification."""
        adder = getattr(context, "add_init_script", None)
        if not callable(adder):
            return
        try:
            adder(script)
        except Exception as exc:  # noqa: BLE001
            logger.warning("注入账号指纹脚本失败（不影响验证）: {}", type(exc).__name__)

    @staticmethod
    def _safe_key(value: str) -> str:
        normalized = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in "._-")
        return normalized.strip("._") or uuid.uuid4().hex

    def profile_path_for_session(self, session_id: str) -> Path:
        return self.profile_root / f".qr_{self._safe_key(session_id)[:48]}"

    def discard_profile(self, session_id: str) -> None:
        shutil.rmtree(self.profile_path_for_session(session_id), ignore_errors=True)

    def promote_profile(self, session_id: str, unb: str) -> Path:
        source = self.profile_path_for_session(session_id)
        target = self.profile_root / f"user_{self._safe_key(unb)}"
        backup = target.with_name(f"{target.name}.backup-{uuid.uuid4().hex}")
        target.parent.mkdir(parents=True, exist_ok=True)
        moved_existing = False
        try:
            if target.exists():
                os.replace(target, backup)
                moved_existing = True
            os.replace(source, target)
        except Exception:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            if moved_existing and backup.exists():
                os.replace(backup, target)
            raise
        else:
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
        return target

    def run(
        self,
        session_id: str,
        verification_url: str,
        initial_cookies: Optional[Dict[str, str]] = None,
        max_wait_time: int = 450,
        on_update: Optional[BrowserUpdateCallback] = None,
        should_stop: Optional[StopCallback] = None,
        interaction_channel: Optional[BrowserInteractionChannel] = None,
        *,
        proxy: Any = None,
    ) -> Dict[str, object]:
        """打开验证页并等待用户完成身份验证。

        proxy 为本次扫码会话绑定的账号代理：验证页必须与扫码接口走同一
        出口 IP，否则风控侧看到的 IP 跳变会加剧验证失败。缺省沿用实例级
        代理（None=直连原行为）。
        """
        if not verification_url:
            return {
                "status": "failed",
                "message": "缺少安全验证链接",
            }

        safe_session_id = session_id.replace("-", "")[:12]
        user_data_dir = self.profile_path_for_session(session_id)
        user_data_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path: Optional[str] = None
        preserve_profile = False

        try:
            parsed = urlparse(verification_url)
            logger.info(
                f"扫码二次验证浏览器启动: session={session_id}, "
                f"host={parsed.netloc or 'unknown'}"
            )

            with self.playwright_factory() as playwright:
                context_kwargs: dict[str, Any] = {
                    "viewport": {"width": 1280, "height": 860},
                    "locale": "zh-CN",
                }
                fp_key = str((initial_cookies or {}).get("unb") or "").strip() or session_id
                fingerprint = build_browser_fingerprint(fp_key)
                if fingerprint:
                    context_kwargs.update(fingerprint.context_options())
                context = playwright.chromium.launch_persistent_context(
                    str(user_data_dir),
                    headless=False,
                    **chromium_runtime_options(proxy if proxy is not None else self._proxy),
                    args=self._browser_args(),
                    **context_kwargs,
                )
                if fingerprint:
                    self._add_fingerprint_script(context, fingerprint.init_script())
                page = self._active_page(context)

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
                verification_kind = self._classify_verification(page)
                required_action = (
                    "scan_image"
                    if verification_kind == "mobile_scan"
                    else "interact_in_console"
                )
                frame_revision = self._capture_interaction_frame(
                    interaction_channel,
                    page,
                )
                if on_update:
                    on_update({
                        "verification_screenshot_path": screenshot_path,
                        "verification_browser_status": "waiting",
                        "verification_kind": verification_kind,
                        "required_action": required_action,
                        **self._interaction_update(
                            interaction_channel,
                            frame_revision,
                        ),
                    })

                started_at = time.time()
                last_screenshot_at = time.time()
                last_frame_at = time.time()
                last_probe_at = 0.0
                redirected_after_success_hint = False

                while time.time() - started_at < max_wait_time:
                    if should_stop and should_stop():
                        logger.info(f"扫码二次验证浏览器收到停止信号: session={session_id}")
                        return {
                            "status": "cancelled",
                            "screenshot_path": screenshot_path,
                            "message": "验证会话已停止",
                        }

                    try:
                        page = self._active_page(
                            context,
                            page,
                            recover_url=verification_url,
                        )
                        drained = (
                            interaction_channel.drain(page)
                            if interaction_channel is not None
                            else 0
                        )
                        now = time.time()
                        if drained or now - last_frame_at >= 0.75:
                            verification_kind = self._classify_verification(page)
                            required_action = (
                                "scan_image"
                                if verification_kind == "mobile_scan"
                                else "interact_in_console"
                            )
                            frame_revision = self._capture_interaction_frame(
                                interaction_channel,
                                page,
                            )
                            if on_update:
                                on_update({
                                    "verification_screenshot_path": screenshot_path,
                                    "verification_browser_status": "waiting",
                                    "verification_kind": verification_kind,
                                    "required_action": required_action,
                                    **self._interaction_update(
                                        interaction_channel,
                                        frame_revision,
                                    ),
                                })
                            last_frame_at = now

                        cookies = self._cookies_to_dict(context.cookies())
                        if (
                            self._has_login_cookie(cookies)
                            and self.session_validator is not None
                            and now - last_probe_at >= 2.0
                        ):
                            last_probe_at = now
                            probe = self.session_validator(
                                self._cookies_to_string(cookies),
                                self._browser_user_agent(page),
                            )
                            if probe.succeeded:
                                verified_cookies = probe.cookies or cookies
                                verified_unb = str(
                                    verified_cookies.get("unb") or ""
                                ).strip()
                                if verified_unb:
                                    preserve_profile = True
                                    logger.info(
                                        f"扫码二次验证已通过消息 Token 校验: session={session_id}, "
                                        f"cookie_count={len(verified_cookies)}, has_unb=True"
                                    )
                                    return {
                                        "status": "success",
                                        "cookies": verified_cookies,
                                        "unb": verified_unb,
                                        "access_token": probe.access_token,
                                        "screenshot_path": screenshot_path,
                                    }

                        if (
                            not redirected_after_success_hint
                            and self._has_success_hint(page)
                        ):
                            logger.info(
                                f"扫码二次验证页面提示成功，尝试进入闲鱼页面换取 Cookie: session={session_id}"
                            )
                            page.goto(
                                "https://www.goofish.com/im",
                                wait_until="domcontentloaded",
                                timeout=45000,
                            )
                            redirected_after_success_hint = True

                        if now - last_screenshot_at >= 8:
                            updated_screenshot = self._capture_screenshot(
                                page,
                                session_id,
                            )
                            if updated_screenshot:
                                screenshot_path = updated_screenshot
                            last_screenshot_at = now
                    except Exception as exc:
                        if not self._is_page_closed_error(exc):
                            raise

                    time.sleep(0.5)

                logger.warning(f"扫码二次验证等待超时: session={session_id}")
                return {
                    "status": "timeout",
                    "screenshot_path": screenshot_path,
                    "message": "等待安全验证超时，请重新生成二维码",
                }

        except Exception as exc:
            error_code = classify_browser_launch_error(exc)
            logger.error(
                f"扫码二次验证浏览器异常: session={session_id}, "
                f"错误类别: {error_code}"
            )
            return {
                "status": "failed",
                "error_code": error_code,
                "screenshot_path": screenshot_path,
                "message": (
                    "安全验证浏览器档案正在使用，请稍后重试"
                    if error_code == "profile_in_use"
                    else "安全验证浏览器处理失败，请重新生成二维码"
                ),
            }
        finally:
            if interaction_channel is not None:
                interaction_channel.close()
            if not preserve_profile:
                try:
                    shutil.rmtree(user_data_dir, ignore_errors=True)
                except Exception as exc:
                    logger.debug(
                        f"清理扫码二次验证浏览器目录失败: {type(exc).__name__}"
                    )

    def _browser_args(self) -> list:
        return [
            "--lang=zh-CN",
            "--window-size=1280,860",
            "--window-position=-32000,-32000",
        ]

    def _active_page(
        self,
        context: Any,
        current_page: Any = None,
        *,
        recover_url: str = "",
    ) -> Any:
        pages = list(getattr(context, "pages", []) or [])
        active_pages = [
            candidate
            for candidate in pages
            if not self._page_is_closed(candidate)
        ]
        page = active_pages[-1] if active_pages else context.new_page()
        if not active_pages and recover_url:
            page.goto(
                recover_url,
                wait_until="domcontentloaded",
                timeout=60000,
            )
        return page

    @staticmethod
    def _page_is_closed(page: Any) -> bool:
        if page is None:
            return True
        is_closed = getattr(page, "is_closed", None)
        if callable(is_closed):
            try:
                return bool(is_closed())
            except Exception:
                return True
        return False

    @staticmethod
    def _is_page_closed_error(exc: Exception) -> bool:
        text = f"{type(exc).__name__}: {exc}".lower()
        return (
            "targetclosed" in text
            or "target page, context or browser has been closed" in text
            or "page has been closed" in text
        )

    @staticmethod
    def _capture_interaction_frame(
        interaction_channel: Optional[BrowserInteractionChannel],
        page: Any,
    ) -> int:
        if interaction_channel is None:
            return 0
        try:
            return interaction_channel.capture(page)
        except Exception:
            return 0

    @staticmethod
    def _interaction_update(
        interaction_channel: Optional[BrowserInteractionChannel],
        frame_revision: int,
    ) -> Dict[str, object]:
        if interaction_channel is None:
            return {
                "interaction_supported": False,
                "frame_revision": 0,
                "viewport_width": 0,
                "viewport_height": 0,
            }
        snapshot = interaction_channel.snapshot()
        if frame_revision and not snapshot.get("frame_revision"):
            snapshot["frame_revision"] = frame_revision
        return snapshot

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
            logger.debug(
                f"扫码二次验证浏览器注入初始 Cookie 失败: {type(exc).__name__}"
            )

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

    def _classify_verification(self, page) -> str:
        if self._has_qr_content(page):
            return "mobile_scan"

        interactive_selectors = (
            "#nc_1_n1z",
            ".nc-container",
            ".nc_scale",
            "#nocaptcha",
            "[class*='slider']",
            "input[type='text']",
            "input[type='tel']",
            "input[type='number']",
            "video",
            "[class*='camera']",
        )
        scopes = [page]
        try:
            scopes.extend(page.frames)
        except Exception:
            pass
        for scope in scopes:
            for selector in interactive_selectors:
                try:
                    element = scope.query_selector(selector)
                    if element is not None and element.is_visible():
                        return "interactive"
                except Exception:
                    continue
        return "unknown"

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
        full_path = self.verification_root / filename

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
                target.screenshot(path=str(full_path))
            else:
                page.screenshot(path=str(full_path), full_page=False)

            logger.info(f"扫码二次验证截图已保存: {filename}")
            return str(full_path)
        except Exception as exc:
            logger.warning(
                f"扫码二次验证截图失败: session={session_id}, "
                f"错误类型: {type(exc).__name__}"
            )
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

    @staticmethod
    def _cookies_to_string(cookies: Dict[str, str]) -> str:
        return "; ".join(f"{name}={value}" for name, value in cookies.items())

    @staticmethod
    def _browser_user_agent(page) -> str:
        try:
            value = str(page.evaluate("navigator.userAgent") or "").strip()
            if value:
                return value
        except Exception:
            pass
        return detect_default_browser_user_agent()

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
