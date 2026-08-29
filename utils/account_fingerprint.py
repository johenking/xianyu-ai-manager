"""Per-account **stable** browser fingerprint derivation.

风控把「同一台机器跑很多号」当成强关联信号：云容器里所有账号如果共用同一套
UA / 屏幕 / WebGL / Canvas 指纹，会被聚类成"一批机器"连坐封号。本模块以账号身份
（unb / cookie_id）为种子，**确定性地**派生一套指纹，同时满足三条性质：

- **稳定**：同一账号每次拿到同一套指纹——设备"变脸"本身就是可疑信号，稳定才像真人；
- **互异**：不同账号落在指纹空间不同点（屏幕 / WebGL / Canvas 微偏移防聚类串号）；
- **自洽**：UA / platform / 屏幕 / 时区 / 语言 跨维度不自相矛盾（例如 Mac UA 配 Win32
  platform 这种一眼假的组合绝不出现）。

所有取值都从"真实存在的取值池"里按账号哈希挑选，取值范围与随机方案完全一致——
只是把"每次随机"换成"按账号确定"，不会比随机更容易被检测，只会更真实、更防聚类。

两个入口，对应两条登录路径：
- `slider_fingerprint(key)`：给 `XianyuSliderStealth` 这类**已在做全套指纹伪装**的路径，
  返回它原有的一份 features dict（键名/取值池一字不改），把"随机"换成"账号稳定"；
- `build_browser_fingerprint(key, ...)`：给官方登录 / L3 记忆 / 扫码验证这类**跑真实
  Chromium、原本不伪装**的路径，产出可合并进 `launch_persistent_context` 的 context 选项
  + 一段 `add_init_script`（只钉 WebGL/Canvas/硬件/屏幕这类"稳定可辨识"维度，**不**改真实
  UA，避免 Linux 容器上谎报 Windows 造成跨维度矛盾）。该路径**默认关闭**，需 1 账号
  云端灰度验证后再开（与住宅代理、L3 保活同一条灰度纪律）。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from dataclasses import dataclass, field
from typing import Any, Optional


# 每 OS 一组"真实 Chrome UA"取值池。slider 路径只用 Windows（与其脚本里钉死的
# navigator.platform=Win32 对齐，杜绝 Mac-UA+Win32 的自相矛盾）；真实浏览器路径不改 UA。
_WINDOWS_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)

# WebGL UNMASKED_VENDOR / UNMASKED_RENDERER：按 OS 分池，渲染器串与该 OS 的图形栈匹配
# （Windows→Direct3D11、Mac→Metal/OpenGL、Linux→Mesa/OpenGL）。
_WEBGL_BY_OS: dict[str, tuple[tuple[str, str], ...]] = {
    "windows": (
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ),
    "mac": (
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"),
        ("Google Inc. (Intel)", "ANGLE (Intel Inc., Intel(R) Iris(TM) Plus Graphics 655, OpenGL 4.1)"),
    ),
    "linux": (
        ("Google Inc. (Intel)", "ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)"),
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA Corporation, NVIDIA GeForce GTX 1050 Ti/PCIe/SSE2, OpenGL 4.6)"),
        ("Mesa/X.org", "llvmpipe (LLVM 15.0.7, 256 bits)"),
    ),
}

# 屏幕分辨率取值池（真实桌面常见档位）。
_SCREENS_BY_OS: dict[str, tuple[tuple[int, int], ...]] = {
    "windows": ((1920, 1080), (2560, 1440), (1680, 1050), (1600, 900), (1536, 864), (1366, 768)),
    "mac": ((1440, 900), (1680, 1050), (2560, 1600), (1512, 982)),
    "linux": ((1920, 1080), (2560, 1440), (1680, 1050), (1600, 900)),
}

_PLATFORM_BY_OS = {"windows": "Win32", "mac": "MacIntel", "linux": "Linux x86_64"}

# 归属地 → 时区。国内住宅代理全境同为 Asia/Shanghai，这里保留映射入口便于将来跨区扩展。
_REGION_TIMEZONES = {
    "cn": "Asia/Shanghai",
    "china": "Asia/Shanghai",
    "hk": "Asia/Hong_Kong",
    "tw": "Asia/Taipei",
    "us": "America/Los_Angeles",
}
_DEFAULT_TIMEZONE = "Asia/Shanghai"

_HARDWARE_CONCURRENCY = (4, 8, 12, 16)
_DEVICE_MEMORY = (4, 8, 16)
_COLOR_SCHEMES = ("light", "dark", "no-preference")
_DEVICE_SCALE_BY_OS = {
    "windows": (1.0, 1.25, 1.5),
    "mac": (2.0,),
    "linux": (1.0, 1.25),
}


def _seed_int(account_key: Any) -> int:
    """Stable 64-bit-ish seed derived from an account identifier (sha256)."""
    text = str(account_key if account_key is not None else "").strip() or "anonymous"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _rng(account_key: Any) -> random.Random:
    return random.Random(_seed_int(account_key))


def _os_from_user_agent(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    if "windows" in ua:
        return "windows"
    if "mac os x" in ua or "macintosh" in ua:
        return "mac"
    return "linux"


def _timezone_for(region: Optional[str], timezone_id: Optional[str]) -> str:
    if timezone_id:
        return timezone_id
    if region:
        return _REGION_TIMEZONES.get(str(region).strip().lower(), _DEFAULT_TIMEZONE)
    return _DEFAULT_TIMEZONE


@dataclass(frozen=True)
class AccountFingerprint:
    """A deterministic, self-consistent fingerprint for one account."""

    seed: int
    os_family: str
    user_agent: str
    platform: str
    locale: str
    accept_language: str
    languages: tuple[str, ...]
    timezone_id: str
    viewport_width: int
    viewport_height: int
    screen_width: int
    screen_height: int
    device_scale_factor: float
    color_scheme: str
    hardware_concurrency: int
    device_memory: int
    webgl_vendor: str
    webgl_renderer: str
    canvas_noise: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def context_options(self, *, include_user_agent: bool = False) -> dict[str, Any]:
        """Playwright ``launch_persistent_context`` overrides for this fingerprint.

        `include_user_agent=False`（真实浏览器路径默认）：**不**覆盖 UA，保留真实
        Chromium 的 UA，只钉屏幕 / 时区 / 缩放 / 配色等不与 OS 冲突的维度。
        """
        options: dict[str, Any] = {
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
            "screen": {"width": self.screen_width, "height": self.screen_height},
            "device_scale_factor": self.device_scale_factor,
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "color_scheme": self.color_scheme,
        }
        if include_user_agent:
            options["user_agent"] = self.user_agent
        return options

    def slider_features(self) -> dict[str, Any]:
        """Legacy features dict matching ``XianyuSliderStealth._get_random_browser_features``.

        键名与取值形态与原随机实现完全一致，仅把"随机"换成"账号稳定"，供该模块直接替换。
        """
        return {
            "window_size": f"{self.screen_width},{self.screen_height}",
            "lang": self.locale,
            "accept_lang": self.accept_language,
            "user_agent": self.user_agent,
            "locale": self.locale,
            "platform": self.platform,
            "viewport_width": self.screen_width,
            "viewport_height": self.screen_height,
            "device_scale_factor": self.device_scale_factor,
            "is_mobile": False,
            "has_touch": False,
            "timezone_id": self.timezone_id,
        }

    def init_script(self) -> str:
        """`add_init_script` body pinning consistent, per-account fingerprint dimensions.

        只钉「稳定可辨识」维度：navigator.hardwareConcurrency / deviceMemory / platform、
        screen 尺寸、WebGL UNMASKED_VENDOR/RENDERER、以及一丝确定性的 Canvas 噪声——
        每项都做特性检测 + try/catch，任何环境差异都不抛错污染登录流程。
        """
        payload = {
            "platform": self.platform,
            "hardwareConcurrency": self.hardware_concurrency,
            "deviceMemory": self.device_memory,
            "screenWidth": self.screen_width,
            "screenHeight": self.screen_height,
            "webglVendor": self.webgl_vendor,
            "webglRenderer": self.webgl_renderer,
            "canvasNoise": self.canvas_noise,
        }
        config_json = json.dumps(payload)
        return (
            "(() => {\n"
            f"  const FP = {config_json};\n"
            "  const pin = (obj, prop, value) => {\n"
            "    try { Object.defineProperty(obj, prop, { get: () => value, configurable: true }); } catch (_) {}\n"
            "  };\n"
            "  try { pin(navigator, 'platform', FP.platform); } catch (_) {}\n"
            "  try { pin(navigator, 'hardwareConcurrency', FP.hardwareConcurrency); } catch (_) {}\n"
            "  try { pin(navigator, 'deviceMemory', FP.deviceMemory); } catch (_) {}\n"
            "  try {\n"
            "    pin(screen, 'width', FP.screenWidth); pin(screen, 'height', FP.screenHeight);\n"
            "    pin(screen, 'availWidth', FP.screenWidth); pin(screen, 'availHeight', FP.screenHeight - 40);\n"
            "  } catch (_) {}\n"
            "  const patchGL = (proto) => {\n"
            "    if (!proto || !proto.getParameter) return;\n"
            "    const orig = proto.getParameter;\n"
            "    proto.getParameter = function (p) {\n"
            "      if (p === 37445) return FP.webglVendor;\n"
            "      if (p === 37446) return FP.webglRenderer;\n"
            "      return orig.call(this, p);\n"
            "    };\n"
            "  };\n"
            "  try { patchGL(window.WebGLRenderingContext && WebGLRenderingContext.prototype); } catch (_) {}\n"
            "  try { patchGL(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype); } catch (_) {}\n"
            "  try {\n"
            "    const proto = window.CanvasRenderingContext2D && CanvasRenderingContext2D.prototype;\n"
            "    if (proto && proto.getImageData) {\n"
            "      const orig = proto.getImageData;\n"
            "      proto.getImageData = function (...a) {\n"
            "        const data = orig.apply(this, a);\n"
            "        try {\n"
            "          const d = data.data; const n = (FP.canvasNoise % 8) + 1;\n"
            "          for (let i = 0; i < d.length; i += 4 * 997) { d[i] = (d[i] + n) % 256; }\n"
            "        } catch (_) {}\n"
            "        return data;\n"
            "      };\n"
            "    }\n"
            "  } catch (_) {}\n"
            "})();"
        )


def derive_fingerprint(
    account_key: Any,
    *,
    os_family: str = "linux",
    base_user_agent: Optional[str] = None,
    region: Optional[str] = None,
    timezone_id: Optional[str] = None,
) -> AccountFingerprint:
    """Deterministically derive a self-consistent fingerprint for ``account_key``.

    - `base_user_agent` 提供时（真实浏览器路径）：以真实 UA 为准，OS 从 UA 推断，
      其余维度按该 OS 的取值池选取，保证与真实 UA 自洽；
    - 未提供时：按 `os_family` 从对应 UA 池挑一个确定性 UA（slider 路径用 windows）。
    """
    rng = _rng(account_key)
    if base_user_agent:
        user_agent = base_user_agent
        resolved_os = _os_from_user_agent(base_user_agent)
    else:
        resolved_os = os_family if os_family in _WEBGL_BY_OS else "linux"
        if resolved_os == "windows":
            user_agent = rng.choice(_WINDOWS_USER_AGENTS)
        else:
            # 非 windows 且未给真实 UA：退回 linux 池的第一个作为占位（真实路径基本不会走到）。
            user_agent = _WINDOWS_USER_AGENTS[0]
            resolved_os = "windows"

    screen_w, screen_h = rng.choice(_SCREENS_BY_OS.get(resolved_os, _SCREENS_BY_OS["linux"]))
    webgl_vendor, webgl_renderer = rng.choice(_WEBGL_BY_OS.get(resolved_os, _WEBGL_BY_OS["linux"]))
    device_scale = rng.choice(_DEVICE_SCALE_BY_OS.get(resolved_os, _DEVICE_SCALE_BY_OS["linux"]))
    # viewport 略小于 screen（去掉浏览器 chrome/任务栏的合理留白），且不超过屏幕。
    viewport_w = min(screen_w, rng.choice((1280, 1366, 1440, 1536, 1600)))
    viewport_h = min(screen_h - 80, rng.choice((720, 768, 864, 900, 960)))

    return AccountFingerprint(
        seed=_seed_int(account_key),
        os_family=resolved_os,
        user_agent=user_agent,
        platform=_PLATFORM_BY_OS.get(resolved_os, "Win32"),
        locale="zh-CN",
        accept_language="zh-CN,zh;q=0.9",
        languages=("zh-CN", "zh"),
        timezone_id=_timezone_for(region, timezone_id),
        viewport_width=viewport_w,
        viewport_height=viewport_h,
        screen_width=screen_w,
        screen_height=screen_h,
        device_scale_factor=device_scale,
        color_scheme=rng.choice(_COLOR_SCHEMES),
        hardware_concurrency=rng.choice(_HARDWARE_CONCURRENCY),
        device_memory=rng.choice(_DEVICE_MEMORY),
        webgl_vendor=webgl_vendor,
        webgl_renderer=webgl_renderer,
        canvas_noise=rng.randrange(1, 251),
    )


def slider_fingerprint(account_key: Any) -> AccountFingerprint:
    """Windows-only, self-consistent fingerprint for the slider-stealth path."""
    return derive_fingerprint(account_key, os_family="windows")


def account_fingerprint_enabled() -> bool:
    """Whether to inject per-account fingerprint into the **real-browser** login paths.

    默认关闭（byte-identical 原行为），与住宅代理 / L3 保活同属"需 1 账号云端灰度后再开"。
    开启方式：环境变量 `XIANYU_ACCOUNT_FINGERPRINT=1`，或 config.json 里
    `ACCOUNT_FINGERPRINT_ENABLED: true`。
    """
    env = os.getenv("XIANYU_ACCOUNT_FINGERPRINT")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    try:
        from config import config as _config

        return bool(_config.get("ACCOUNT_FINGERPRINT_ENABLED", False))
    except Exception:
        return False


def build_browser_fingerprint(
    account_key: Any,
    *,
    region: Optional[str] = None,
    base_user_agent: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> Optional[AccountFingerprint]:
    """Return a fingerprint for the real-browser paths, or ``None`` when disabled.

    `enabled` 省略时读 `account_fingerprint_enabled()`；返回 None 时调用方应保持原行为
    （不合并 context 选项、不注入 init 脚本），确保关闭态字节级不变。
    """
    active = account_fingerprint_enabled() if enabled is None else enabled
    if not active:
        return None
    key = str(account_key or "").strip()
    if not key:
        return None
    return derive_fingerprint(
        key,
        os_family="linux",
        base_user_agent=base_user_agent,
        region=region,
    )


__all__ = [
    "AccountFingerprint",
    "derive_fingerprint",
    "slider_fingerprint",
    "build_browser_fingerprint",
    "account_fingerprint_enabled",
]
