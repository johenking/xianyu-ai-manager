"""每账号稳定指纹派生 + 登录路径接线的守卫测试。

核心性质：稳定（同号一致）、互异（不同号不同）、自洽（UA/platform/WebGL 同 OS）、
关闭态字节级不变（real-browser 路径默认不注入）。
"""

from __future__ import annotations

import pytest

from utils.account_fingerprint import (
    account_fingerprint_enabled,
    build_browser_fingerprint,
    derive_fingerprint,
    slider_fingerprint,
)


def test_fingerprint_is_stable_for_same_account():
    a = derive_fingerprint("2217422055234")
    b = derive_fingerprint("2217422055234")
    assert a == b
    assert a.user_agent == b.user_agent
    assert (a.screen_width, a.screen_height) == (b.screen_width, b.screen_height)
    assert a.webgl_renderer == b.webgl_renderer
    assert a.canvas_noise == b.canvas_noise


def test_fingerprint_differs_across_accounts():
    keys = [str(1000 + i) for i in range(24)]
    seeds = {derive_fingerprint(k).seed for k in keys}
    # 种子必须全互异（哈希空间足够大，24 个不该撞）。
    assert len(seeds) == len(keys)
    # 可观测指纹维度组合也应高度分散（防聚类串号），不要求全异但要明显多样。
    combos = {
        (
            fp.screen_width,
            fp.screen_height,
            fp.webgl_renderer,
            fp.hardware_concurrency,
            fp.device_memory,
        )
        for fp in (derive_fingerprint(k) for k in keys)
    }
    assert len(combos) >= len(keys) // 2


def test_slider_fingerprint_is_windows_self_consistent():
    fp = slider_fingerprint("account-A")
    feats = fp.slider_features()
    # slider 路径钉死 Win32 platform，UA 必须也是 Windows —— 杜绝 Mac-UA+Win32 矛盾。
    assert "Windows" in feats["user_agent"]
    assert feats["platform"] == "Win32"
    assert feats["timezone_id"] == "Asia/Shanghai"
    assert feats["locale"] == "zh-CN"
    # 返回形态与旧 _get_random_browser_features 对齐的关键键都在。
    for key in (
        "window_size",
        "lang",
        "accept_lang",
        "user_agent",
        "viewport_width",
        "viewport_height",
        "device_scale_factor",
        "is_mobile",
        "has_touch",
    ):
        assert key in feats
    w, h = feats["window_size"].split(",")
    assert (int(w), int(h)) == (feats["viewport_width"], feats["viewport_height"])


def test_slider_features_stable_per_account():
    assert slider_fingerprint("X").slider_features() == slider_fingerprint("X").slider_features()
    assert (
        slider_fingerprint("X").slider_features()["user_agent"]
        != slider_fingerprint("Y").slider_features()["user_agent"]
        or slider_fingerprint("X").slider_features()["window_size"]
        != slider_fingerprint("Y").slider_features()["window_size"]
    )


def test_webgl_matches_declared_os():
    win = derive_fingerprint("w", os_family="windows")
    assert win.platform == "Win32"
    assert "Direct3D11" in win.webgl_renderer or "D3D11" in win.webgl_renderer
    # 真实浏览器路径给真实 Linux UA → 推断 linux，WebGL 用 Mesa/OpenGL 系。
    linux_ua = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    lin = derive_fingerprint("l", base_user_agent=linux_ua)
    assert lin.os_family == "linux"
    assert lin.platform == "Linux x86_64"
    assert lin.user_agent == linux_ua


def test_context_options_exclude_user_agent_by_default():
    fp = derive_fingerprint("acc")
    opts = fp.context_options()
    assert "user_agent" not in opts  # 真实浏览器路径默认保留真实 UA
    assert opts["viewport"]["width"] == fp.viewport_width
    assert opts["screen"]["width"] == fp.screen_width
    assert opts["timezone_id"] == fp.timezone_id
    assert fp.context_options(include_user_agent=True)["user_agent"] == fp.user_agent


def test_viewport_never_exceeds_screen():
    for i in range(40):
        fp = derive_fingerprint(f"acct-{i}")
        assert fp.viewport_width <= fp.screen_width
        assert fp.viewport_height <= fp.screen_height


def test_init_script_pins_declared_values_and_is_guarded():
    fp = derive_fingerprint("acc")
    script = fp.init_script()
    assert str(fp.hardware_concurrency) in script
    assert str(fp.device_memory) in script
    assert fp.webgl_renderer in script
    assert "37445" in script and "37446" in script  # UNMASKED_VENDOR / RENDERER
    assert "try" in script and "catch" in script  # 注入失败不得抛错污染登录


def test_region_timezone_alignment():
    assert derive_fingerprint("a", region="cn").timezone_id == "Asia/Shanghai"
    assert derive_fingerprint("a", region="hk").timezone_id == "Asia/Hong_Kong"
    assert derive_fingerprint("a", timezone_id="Asia/Chongqing").timezone_id == "Asia/Chongqing"


def test_build_browser_fingerprint_gated_off_by_default(monkeypatch):
    monkeypatch.delenv("XIANYU_ACCOUNT_FINGERPRINT", raising=False)
    # 显式关闭 → None（real-browser 路径保持原行为、字节级不变）。
    assert build_browser_fingerprint("acc", enabled=False) is None


def test_build_browser_fingerprint_enabled_returns_fingerprint():
    fp = build_browser_fingerprint("2217422055234", enabled=True)
    assert fp is not None
    assert fp.seed == derive_fingerprint("2217422055234", os_family="linux").seed


def test_build_browser_fingerprint_blank_key_returns_none():
    assert build_browser_fingerprint("   ", enabled=True) is None


def test_enabled_flag_reads_env(monkeypatch):
    monkeypatch.setenv("XIANYU_ACCOUNT_FINGERPRINT", "1")
    assert account_fingerprint_enabled() is True
    assert build_browser_fingerprint("acc") is not None
    monkeypatch.setenv("XIANYU_ACCOUNT_FINGERPRINT", "0")
    assert account_fingerprint_enabled() is False
    assert build_browser_fingerprint("acc") is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
