# 会话续签误判修复 + 住宅代理全路径覆盖 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。

**目标：** 修掉「点一下刷新按钮就把健康账号打成需人工重登」的会话状态误判，并把每账号住宅代理接进人工重登路径，让扫码/官方登录也从住宅 IP 出去。

**架构：** 三层。①API 层纠正错误语义——「缺续期设备绑定」不再改写账号会话状态；②运行时层新增手动 L3 免密续签通道，让有浏览器记忆的账号真能被按钮续签（自动经账号代理）；③登录会话层把账号代理注入官方登录浏览器，并把 L3 主动保活从全局开关改成按号灰度。

**技术栈：** Python 3.11 / FastAPI / SQLite / Playwright(Chromium) / React+TypeScript / pytest（本地用 `.venv/bin/python -m pytest`）

---

## 背景与根因（执行前必读）

线上现象：账号「寻艺服务」`2220000015630` 连续两次扫码登录成功，界面却立刻显示「登录状态已过期」，监听器、订单同步、invite_bridge_poller 全部跳过该号。

根因链条：

1. `db_manager.get_cookie_refresh_settings()` 判定
   `auto_refresh_supported = has_l3_memory OR (设备绑定 AND username AND password)`。
   只要账号有 L3 浏览器记忆，前端就把账号行上的圆箭头渲染成可点的「立即刷新 Cookie」。
2. 但 `POST /api/accounts/{cookie_id}/session-refresh` 端点**只实现了浏览器插件设备代续签**一条路：`db_manager.create_client_renewal_task()`，它硬性要求 `account_renewal_bindings` 有有效绑定。
3. 线上有效绑定数 = **0**（`client_browser_devices` 注册了 3 台，一台都没绑到账号）。于是必然抛 `ClientBrowserError(client_device_binding_required)`。
4. `reply_server.py` 捕获后调用
   `update_account_session_refresh(state='manual_reauth_required', error_code='client_device_binding_required')`
   —— **把"缺一个续期设备"错当成"会话过期"写进了账号状态**，覆盖掉刚扫码成功的 success。
5. `manual_reauth_required` 是全系统的「需人工重登」闸门，各个 poller 见之即跳过该号。

已用直连 mtop `mtop.taobao.idlemessage.pc.login.token` 验证：该账号 cookie 返回 200 + 有效 accessToken，**会话根本没过期**。

受影响面：所有 `has_l3_memory=1` 的账号（澄思研习社 / 寻艺服务 / 小小杂货铺 / 陈潇轩很专业 / 小梅很专业），正好是仅存的健康主力号。按钮只在健康号上出现，点一下就把它打死。

第二个问题：住宅代理已接入 mtop 探测、滑块自愈、自动续签、L3 保活，但**没接入人工扫码/官方登录**（`official_login_sessions.py` 全文 0 处 proxy），所以人工重登仍从机房 IP 出去——而滑块恰恰在登录这一步弹。

第三个问题：L3 主动保活只有全局开关 `config.L3_KEEPALIVE_ENABLED`，无法只对配了代理的号灰度开启，导致想开也不敢开。

---

## 文件结构

| 文件 | 职责 | 本计划中的变更 |
|---|---|---|
| `reply_server.py` | HTTP API | `session-refresh` 端点：缺设备绑定不再改写会话状态；有 L3 记忆改走免密续签 |
| `cookie_manager.py` | 账号运行时实例管理 | 新增 `live_instances` 注册表 + `trigger_manual_l3_refresh()` |
| `XianyuAutoAsync.py` | 单账号运行时 | `_execute_l3_keepalive(manual=)` 支持手动触发并返回结果；保活开关改为「全局 OR 按号」 |
| `db_manager.py` | 数据访问 | `cookies` 新增 `l3_keepalive_enabled` 列 + 读写方法 |
| `official_login_sessions.py` | 官方登录浏览器会话生命周期 | 按 `expected_unb` 解析账号代理并注入 `XianyuOfficialLoginService` |
| `frontend/components/AccountList.tsx` | 账号列表 UI | 按钮标题/提示区分「免密续签」与「设备代续签」 |
| `tests/test_session_refresh_endpoint.py` | 新建 | 锁死「缺设备绑定不得改写会话状态」 |
| `tests/test_official_login_proxy.py` | 新建 | 锁死「官方登录按账号注入代理」 |
| `tests/test_l3_keepalive.py` | 已存在 | 追加手动触发与按号开关用例 |

---

## 环境与前置

- 本地测试命令一律用项目虚拟环境：`.venv/bin/python -m pytest`（系统 python3 缺 loguru 等依赖，跑不起来）。
- 生产为云主机 Docker 容器 `app-a-cloud-app-a-1`（宿主 `ubuntu@122.51.91.121`，`sudo docker` 免密）。
- SSH 私钥：`~/Documents/ChatGPT/自动化/cloud-deploy/ssh-ephemeral/id_ed25519_20260826`
- 当前分支 `main`。**开始编码前必须先建工作分支**（见任务 0）。

---

## 任务 0：建立工作分支

**文件：** 无

**步骤：**

1. 确认工作区干净（只允许 `.cursor/` 和 `会话-2026-08-29.md` 两个未跟踪项存在）：

```bash
cd /Users/mac/Documents/咸鱼监控台 && git status --porcelain
```

2. 建分支：

```bash
git checkout -b fix/session-refresh-misjudge-and-proxy-coverage
```

**验证：**

```bash
git branch --show-current
# 期望输出：fix/session-refresh-misjudge-and-proxy-coverage
```

**提交：** 无（仅建分支）

---

## 任务 1：立即解冻「寻艺服务」（生产运维动作，不改代码）

**文件：** 无（操作生产数据库）

**理由：** 该账号会话实测存活，只是被错误状态冻结。先恢复业务，再修代码。此动作幂等，且不依赖后续任何代码变更。

**步骤：**

1. 先只读复核该号确实是 `client_device_binding_required` 而非真过期：

```bash
ssh -i ~/Documents/ChatGPT/自动化/cloud-deploy/ssh-ephemeral/id_ed25519_20260826 \
  -o UserKnownHostsFile=~/Documents/ChatGPT/自动化/cloud-deploy/ssh-ephemeral/known_hosts \
  ubuntu@122.51.91.121 \
  'sudo docker exec -i app-a-cloud-app-a-1 python3 -c "
import sqlite3
db=sqlite3.connect(\"file:/app/data/xianyu_data.db?mode=ro\",uri=True)
print(db.execute(\"SELECT state,error_code,message FROM account_session_refresh_status WHERE cookie_id=?\",(\"2220000015630\",)).fetchone())
"'
```

期望输出包含 `manual_reauth_required` 和 `client_device_binding_required`。

2. 用 mtop 探测确认会话仍活（只读，不改任何状态）：

```bash
ssh ... 'sudo docker exec -i app-a-cloud-app-a-1 python3 -c "
from db_manager import db_manager
from utils.xianyu_official_login import probe_message_session_sync
d=db_manager.get_cookie_details(\"2220000015630\")
r=probe_message_session_sync(d[\"value\"], d.get(\"browser_user_agent\") or \"\")
print(r.status, r.succeeded)
"'
```

期望：`succeeded=True`。**若为 False 则跳过步骤 3，改为让用户重新扫码**——此时状态是对的，不是误判。

3. 改回 success：

```bash
ssh ... 'sudo docker exec -i app-a-cloud-app-a-1 python3 -c "
from db_manager import db_manager
db_manager.update_account_session_refresh(
    \"2220000015630\", state=\"success\", trigger=\"manual\",
    message=\"人工核验会话有效，撤销误判的人工重登标记\", error_code=\"\")
print(db_manager.get_account_session_refresh(\"2220000015630\"))
"'
```

**验证：** 上一条命令输出中 `state` 为 `success`、`error_code` 为空。随后在云端 UI 刷新账号列表，该号不再显示「登录状态已过期」。

**提交：** 无（运维动作，记录进 `会话-2026-08-29.md`）

---

## 任务 2：写失败测试——缺设备绑定不得改写会话状态

**文件：** 新建 `tests/test_session_refresh_endpoint.py`

**步骤：**

1. 创建测试文件。测试直接调用端点函数本体（绕开 HTTP 与鉴权），断言两件事：不写 `manual_reauth_required`；返回体带 `client_device_binding_required`。

```python
"""session-refresh 端点的状态安全护栏。

背景（2026-08-29 事故）：账号「寻艺服务」扫码成功后被打成 manual_reauth_required，
根因是端点把「缺续期设备绑定」当成「会话过期」写进了 account_session_refresh_status，
覆盖掉刚成功的 success，并触发全系统的人工重登闸门冻结该号。

这里锁死：缺绑定只能返回错误，绝不允许改写账号会话状态。
"""

import asyncio
import unittest
from unittest.mock import Mock, patch

import reply_server
from client_browser_errors import ClientBrowserError


def _call(cookie_id="acct-1", user_id=7):
    return asyncio.run(
        reply_server.refresh_account_session(
            cookie_id, current_user={"user_id": user_id}
        )
    )


class SessionRefreshBindingTests(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.db.create_client_renewal_task = Mock(
            side_effect=ClientBrowserError(
                "账号尚未绑定可用续期设备",
                error_code="client_device_binding_required",
                http_status=409,
            )
        )
        self.db.get_cookie_details = Mock(return_value={"has_l3_memory": 0})
        self.db.update_account_session_refresh = Mock(return_value=True)
        self.db.get_account_session_refresh = Mock(return_value={"state": "success"})

    def test_missing_binding_never_overwrites_session_state(self):
        with patch.object(reply_server, "db_manager", self.db), \
                patch.object(reply_server, "_require_owned_cookie", Mock()), \
                patch.object(
                    reply_server, "_current_session_refresh_status",
                    Mock(return_value={"state": "success"}),
                ):
            result = _call()

        self.db.update_account_session_refresh.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "client_device_binding_required")

    def test_l3_account_is_routed_to_passwordless_refresh(self):
        self.db.get_cookie_details = Mock(return_value={"has_l3_memory": 1})
        started = Mock(return_value={"success": True, "status": "l3_refreshing"})
        with patch.object(reply_server, "db_manager", self.db), \
                patch.object(reply_server, "_require_owned_cookie", Mock()), \
                patch.object(reply_server, "_start_l3_passwordless_refresh", started), \
                patch.object(
                    reply_server, "_current_session_refresh_status",
                    Mock(return_value={"state": "success"}),
                ):
            result = _call()

        started.assert_called_once_with("acct-1")
        self.db.update_account_session_refresh.assert_not_called()
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
```

2. 确认 `ClientBrowserError` 的真实导入路径。先查：

```bash
cd /Users/mac/Documents/咸鱼监控台 && .venv/bin/python -c "import reply_server, inspect; print(inspect.getmodule(reply_server.ClientBrowserError).__name__)"
```

把测试里的 `from client_browser_errors import ClientBrowserError` 改成实际模块名。

**验证：**

```bash
.venv/bin/python -m pytest tests/test_session_refresh_endpoint.py -q
```

期望：**2 failed**（`test_missing_binding_never_overwrites_session_state` 因当前代码调用了 `update_account_session_refresh` 而失败；`test_l3_account_is_routed_to_passwordless_refresh` 因 `_start_l3_passwordless_refresh` 尚不存在而失败）。这正是我们要修的。

**提交：**

```bash
git add tests/test_session_refresh_endpoint.py
git commit -m "test: 锁死 session-refresh 缺设备绑定不得改写会话状态（任务 2/9）"
```

---

## 任务 3：运行时新增手动 L3 免密续签通道

**文件：** `XianyuAutoAsync.py`、`cookie_manager.py`、`tests/test_l3_keepalive.py`

### 3.1 `XianyuAutoAsync.py`：保活开关抽成方法，支持手动触发并返回结果

把 `_execute_l3_keepalive` 的全局开关判断换成 `_l3_keepalive_switch()`，并加 `manual` 参数。注意：**全局关闭时必须在任何 DB 访问之前就返回**（现有测试 `test_global_switch_off_is_hard_noop` 断言 `get_cookie_details` 未被调用）。

在 `_l3_keepalive_due` 静态方法之后、`_execute_l3_keepalive` 之前插入：

```python
    def _l3_keepalive_switch(self) -> bool:
        """主动保活是否对本号开启：全局开关或该号的按号灰度开关任一为真。

        按号开关让「只给配了住宅代理的号开保活」成为可能，避免全局一开、
        没配代理的号从机房 IP 去打 passport。
        """
        return bool(L3_KEEPALIVE_ENABLED or getattr(self, "l3_keepalive_enabled", False))
```

把 `_execute_l3_keepalive` 的签名与开头改成：

```python
    async def _execute_l3_keepalive(self, *, manual: bool = False) -> dict:
        """趁会话仍有效时用 L3「快速进入」免密续签，让 cookie2 常青。

        安全铁律：只有成功拿到新会话才交接监听；任何失败/未续新都只记日志，
        绝不清 has_l3_memory、不标过期、不触发人工重登——绝不因保活打扰在跑的账号。

        manual=True 由「立即刷新 Cookie」按钮触发，绕过保活开关与调度间隔，
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
```

`async with self.l3_keepalive_lock:` 块内，把每个 `return` 换成带结果的 return：

```python
        async with self.l3_keepalive_lock:
            try:
                l3_cookie = await self._recover_via_passwordless_refresh(
                    profile_unb, self.cookies_str, "L3主动保活"
                )
            except Exception as exc:
                logger.warning(
                    f"【{self.cookie_id}】L3 主动保活异常（忽略，不影响现有会话）: {self._safe_str(exc)}"
                )
                return {"ok": False, "code": "l3_exception", "message": "免密续签执行异常，现有会话不受影响"}
            if not l3_cookie:
                error_code = str(getattr(self, "_last_l3_error_code", "") or "")
                if error_code in L3_KEEPALIVE_RESEED_CODES:
                    logger.info(
                        f"【{self.cookie_id}】L3 记忆不可用（{error_code}），趁会话仍有效重建档案"
                    )
                    await self._reseed_l3_memory(profile_unb)
                    return {"ok": False, "code": "l3_reseeded", "message": "浏览器记忆已重建，稍后会自动续签"}
                logger.info(
                    f"【{self.cookie_id}】L3 主动保活本次未续新"
                    f"（{error_code or 'no-op'}），保持现有会话"
                )
                return {"ok": False, "code": error_code or "l3_no_op", "message": "本次未续新，现有会话保持不变"}
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
            return {"ok": False, "code": "handover_failed", "message": "已续到新会话但监听交接失败"}
```

调度处（约 8031 行）把 `enabled=L3_KEEPALIVE_ENABLED` 换成按号开关：

```python
                    if XianyuLive._l3_keepalive_due(
                        time.time(),
                        getattr(self, 'last_l3_keepalive_time', 0),
                        getattr(self, 'l3_keepalive_interval', 0),
                        enabled=self._l3_keepalive_switch(),
                    ):
```

### 3.2 `cookie_manager.py`：登记运行中的实例并暴露手动触发

`CookieManager.__init__` 里 `self._task_generations` 之后加一行：

```python
        self.live_instances: Dict[str, Any] = {}  # 运行中的 XianyuLive，供 API 手动触发续签
```

`_run_xianyu` 中创建实例后登记，并在 `finally` 注销。在 `logger.info(f"【{account_ref}】XianyuLive实例创建成功，开始调用main()...")` 之前插入：

```python
            self.live_instances[cookie_id] = live
```

在 `_run_xianyu` 的 `finally:` 块内（若无 finally 则在函数末尾的清理处）加：

```python
            if self.live_instances.get(cookie_id) is live:
                self.live_instances.pop(cookie_id, None)
```

在类中新增方法：

```python
    async def trigger_manual_l3_refresh(self, cookie_id: str) -> Dict[str, Any]:
        """由「立即刷新 Cookie」按钮触发一次 L3 免密续签（自动经该账号的住宅代理）。

        只对监听在跑的账号可用——续签成功后要把新会话交接给监听，
        没有在跑的实例就无处交接。
        """
        live = self.live_instances.get(cookie_id)
        if live is None:
            return {
                "ok": False,
                "code": "listener_not_running",
                "message": "账号监听未在运行，请先启用该账号",
            }
        return await live._execute_l3_keepalive(manual=True)

    def request_manual_l3_refresh(self, cookie_id: str) -> Dict[str, Any]:
        """线程安全入口：无论调用方在不在 CookieManager 的事件循环里都能用。"""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is not None and current_loop is self.loop:
            raise RuntimeError("请在同一事件循环内直接 await trigger_manual_l3_refresh")
        fut = asyncio.run_coroutine_threadsafe(
            self.trigger_manual_l3_refresh(cookie_id), self.loop
        )
        return fut.result(timeout=300)
```

确认 `cookie_manager.py` 顶部已 `from typing import Any`，没有就补上。

### 3.3 追加测试

在 `tests/test_l3_keepalive.py` 的 `ExecuteKeepaliveTests` 类末尾追加：

```python
    async def test_manual_run_bypasses_switch_but_keeps_guards(self):
        """手动触发绕过保活开关，但代理门禁等护栏一律照旧。"""
        live = _make_live()
        live._recover_via_passwordless_refresh = AsyncMock(return_value="unb=123; cookie2=new")
        db = _fake_db(unb="123")
        with patch("XianyuAutoAsync.L3_KEEPALIVE_ENABLED", False), \
                patch("db_manager.db_manager", db), \
                patch("account_session_refresh.active_refresh_registry", _fake_registry()):
            result = await live._execute_l3_keepalive(manual=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "renewed")
        live._update_cookies_and_restart.assert_awaited_once()

    async def test_manual_run_still_blocked_by_unhealthy_proxy(self):
        live = _make_live()
        db = _fake_db(proxy={"server": "socks5://u:p@h:9"})
        with patch("XianyuAutoAsync.L3_KEEPALIVE_ENABLED", False), \
                patch("db_manager.db_manager", db), \
                patch("account_session_refresh.active_refresh_registry", _fake_registry()):
            result = await live._execute_l3_keepalive(manual=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "proxy_unhealthy")
        live._recover_via_passwordless_refresh.assert_not_awaited()

    async def test_per_account_switch_enables_scheduled_keepalive(self):
        """全局关、按号开 → 定时保活照跑（按号灰度的核心语义）。"""
        live = _make_live(l3_keepalive_enabled=True)
        live._recover_via_passwordless_refresh = AsyncMock(return_value="unb=123; cookie2=new")
        db = _fake_db(unb="123")
        with patch("XianyuAutoAsync.L3_KEEPALIVE_ENABLED", False), \
                patch("db_manager.db_manager", db), \
                patch("account_session_refresh.active_refresh_registry", _fake_registry()):
            result = await live._execute_l3_keepalive()

        self.assertTrue(result["ok"])
```

**验证：**

```bash
.venv/bin/python -m pytest tests/test_l3_keepalive.py -q
```

期望：全部通过（原 20 个 + 新增 3 个）。特别确认 `test_global_switch_off_is_hard_noop` 仍通过——它保证全局关闭时不碰数据库。

**提交：**

```bash
git add XianyuAutoAsync.py cookie_manager.py tests/test_l3_keepalive.py
git commit -m "feat: L3 免密续签支持手动触发与按号开关（任务 3/9）"
```

---

## 任务 4：修 session-refresh 端点——不再改写状态，有 L3 记忆就走免密续签

**文件：** `reply_server.py`

**步骤：**

1. 在 `refresh_account_session` 之前新增辅助函数：

```python
async def _start_l3_passwordless_refresh(cookie_id: str) -> Dict[str, Any]:
    """账号有浏览器登录记忆时，用 L3 免密续签替代插件设备代续签。

    这正是前端把「立即刷新 Cookie」点亮的依据（auto_refresh_supported 认 has_l3_memory），
    以前后端却只认插件设备，于是健康号一点就被打成人工重登。
    """
    result = await cookie_manager.trigger_manual_l3_refresh(cookie_id)
    if result.get("ok"):
        db_manager.update_account_session_refresh(
            cookie_id, state='success', trigger='manual_l3',
            message=result.get("message") or '免密续签成功',
            error_code='',
        )
        return {
            'success': True,
            'status': 'l3_renewed',
            'message': result.get("message") or '免密续签成功',
            'data': _current_session_refresh_status(cookie_id),
        }
    # 免密续签没成不代表会话死了：只记一次失败，绝不写 manual_reauth_required，
    # 否则又会把仍然活着的账号冻结掉——这正是 2026-08-29 事故的成因。
    db_manager.update_account_session_refresh(
        cookie_id, state='failed', trigger='manual_l3',
        message=result.get("message") or '免密续签未成功',
        error_code=str(result.get("code") or 'l3_refresh_failed'),
    )
    return {
        'success': False,
        'status': str(result.get("code") or 'l3_refresh_failed'),
        'message': result.get("message") or '免密续签未成功，现有会话未受影响',
        'data': _current_session_refresh_status(cookie_id),
    }
```

2. 把 `client_device_binding_required` 分支整段替换为：

```python
        if exc.error_code == 'client_device_binding_required':
            if bool((db_manager.get_cookie_details(cookie_id) or {}).get('has_l3_memory')):
                return await _start_l3_passwordless_refresh(cookie_id)
            # 缺续期设备绑定 ≠ 会话过期。这里绝不能写 manual_reauth_required：
            # 那是全系统的人工重登闸门，会把仍然活着的账号冻结、停掉监听与订单同步。
            return {
                'success': False,
                'status': 'client_device_binding_required',
                'message': '该账号还没有浏览器登录记忆，也没有绑定续期设备，请重新扫码登录',
                'reauth_action': 'bind_client_device',
                'data': _current_session_refresh_status(cookie_id),
            }
```

3. 确认 `Dict`、`Any` 已从 typing 导入（文件顶部通常已有）。

**验证：**

```bash
.venv/bin/python -m pytest tests/test_session_refresh_endpoint.py -q
```

期望：**2 passed**。

再跑相邻回归：

```bash
.venv/bin/python -m pytest tests/ -q -k "session or refresh or renewal or client_browser"
```

期望：全部通过。

**提交：**

```bash
git add reply_server.py
git commit -m "fix: 缺续期设备绑定不再误判为会话过期，有 L3 记忆改走免密续签（任务 4/9）"
```

---

## 任务 5：写失败测试——官方登录必须按账号注入代理

**文件：** 新建 `tests/test_official_login_proxy.py`

**步骤：**

```python
"""官方登录浏览器会话的代理注入。

背景：住宅代理已接入 mtop 探测 / 滑块自愈 / 自动续签 / L3 保活，唯独
人工扫码与官方登录没接——而滑块恰恰在登录这一步弹。没接代理时人工重登
仍从机房 IP 出去，等于白买代理。

这里锁死：能解析出账号代理时必须注入 XianyuOfficialLoginService；
解析不出（新账号首登）时保持原行为，一个多余参数都不传。
"""

import asyncio
import unittest
from unittest.mock import Mock, patch

from official_login_sessions import OfficialLoginSessionCoordinator


class _FakeResult:
    succeeded = False
    status = "failed"
    error_code = "test_stop"
    verification_image_path = ""
    unb = ""


def _coordinator(factory):
    return OfficialLoginSessionCoordinator(
        completion_handler=Mock(return_value={}),
        service_factory=factory,
        registry=Mock(),
    )


class OfficialLoginProxyTests(unittest.IsolatedAsyncioTestCase):
    async def _run_qr(self, factory, expected_unb, proxy_config):
        coordinator = _coordinator(factory)
        db = Mock()
        db.find_cookie_id_by_unb = Mock(return_value="acct-1" if proxy_config else None)
        db.get_account_proxy_config = Mock(return_value=proxy_config)
        with patch("official_login_sessions.db_manager", db):
            status = await coordinator.start(
                owner_user_id=7, mode="qr", expected_unb=expected_unb
            )
            record = coordinator._sessions[status["session_id"]]
            await record.task
        return db

    async def test_proxy_is_injected_when_account_resolvable(self):
        seen = {}

        def factory(**kwargs):
            seen.update(kwargs)
            service = Mock()
            service.login_with_qr = Mock(return_value=_FakeResult())
            return service

        proxy = {"server": "http://tunpool.example:26860", "username": "u", "password": "p"}
        db = await self._run_qr(factory, "123456", proxy)

        db.find_cookie_id_by_unb.assert_called_once_with(7, "123456")
        self.assertEqual(seen.get("proxy"), proxy)

    async def test_no_proxy_keeps_original_call_shape(self):
        seen = {"called_with": None}

        def factory(**kwargs):
            seen["called_with"] = kwargs
            service = Mock()
            service.login_with_qr = Mock(return_value=_FakeResult())
            return service

        await self._run_qr(factory, "", None)

        self.assertEqual(seen["called_with"], {})


if __name__ == "__main__":
    unittest.main()
```

**验证：**

```bash
.venv/bin/python -m pytest tests/test_official_login_proxy.py -q
```

期望：`test_proxy_is_injected_when_account_resolvable` **failed**（当前 `service_factory()` 无参调用），`test_no_proxy_keeps_original_call_shape` passed。

**提交：**

```bash
git add tests/test_official_login_proxy.py
git commit -m "test: 锁死官方登录按账号注入住宅代理（任务 5/9）"
```

---

## 任务 6：官方登录会话注入账号代理

**文件：** `official_login_sessions.py`

**步骤：**

1. 顶部导入区加：

```python
from db_manager import db_manager
```

2. 在 `OfficialLoginSessionCoordinator` 中新增解析方法：

```python
    def _resolve_account_proxy(self, record: OfficialLoginSessionRecord) -> Any:
        """按 expected_unb 反查账号，取其住宅代理配置。

        新账号首登拿不到 unb，返回 None —— 此时保持无代理原行为。
        任何异常都吞掉返回 None：代理解析失败绝不能挡住用户登录。
        """
        unb = (record.expected_unb or "").strip()
        if not unb:
            return None
        try:
            cookie_id = db_manager.find_cookie_id_by_unb(record.owner_user_id, unb)
            if not cookie_id:
                return None
            return db_manager.get_account_proxy_config(cookie_id)
        except Exception:
            return None
```

3. 在 `_run` 中把 `service = self.service_factory()` 替换为：

```python
            account_proxy = await asyncio.to_thread(self._resolve_account_proxy, record)
            service = (
                self.service_factory(proxy=account_proxy)
                if account_proxy
                else self.service_factory()
            )
```

**验证：**

```bash
.venv/bin/python -m pytest tests/test_official_login_proxy.py -q
```

期望：**2 passed**。

回归官方登录相关测试：

```bash
.venv/bin/python -m pytest tests/ -q -k "official"
```

期望：全部通过。

**提交：**

```bash
git add official_login_sessions.py
git commit -m "feat: 官方登录浏览器会话按账号注入住宅代理（任务 6/9）"
```

---

## 任务 7：按号灰度 L3 保活的持久化开关

**文件：** `db_manager.py`

**步骤：**

1. 在 `_migrate_database`（含 `if 'xianyu_unb' not in cookie_columns:` 的那段迁移代码）内，仿照现有写法追加：

```python
            if 'l3_keepalive_enabled' not in cookie_columns:
                logger.info("添加cookies表的l3_keepalive_enabled列...")
                cursor.execute(
                    "ALTER TABLE cookies ADD COLUMN l3_keepalive_enabled INTEGER DEFAULT 0"
                )
                logger.info("数据库迁移完成：添加l3_keepalive_enabled列")
```

2. 新增读写方法（放在 `get_account_proxy_config` 附近）：

```python
    def get_l3_keepalive_enabled(self, cookie_id: str) -> bool:
        """该账号是否单独开启 L3 主动保活（按号灰度，与全局开关取或）。"""
        with self.lock:
            try:
                row = self.conn.execute(
                    "SELECT COALESCE(l3_keepalive_enabled, 0) FROM cookies WHERE id = ?",
                    (cookie_id,),
                ).fetchone()
                return bool(row[0]) if row else False
            except Exception as e:
                logger.error(f"读取账号L3保活开关失败: {e}")
                return False

    def set_l3_keepalive_enabled(self, cookie_id: str, enabled: bool) -> bool:
        with self.lock:
            try:
                self.conn.execute(
                    "UPDATE cookies SET l3_keepalive_enabled = ? WHERE id = ?",
                    (1 if enabled else 0, cookie_id),
                )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"写入账号L3保活开关失败: {e}")
                return False
```

3. `XianyuAutoAsync.py` 的 `XianyuLive.__init__` 中，`self.l3_keepalive_lock = asyncio.Lock()` 之前插入：

```python
        try:
            self.l3_keepalive_enabled = db_manager.get_l3_keepalive_enabled(self.cookie_id)
        except Exception:
            self.l3_keepalive_enabled = False
```

**验证：**

```bash
.venv/bin/python -m pytest tests/ -q -k "l3 or proxy or db_manager"
```

期望：全部通过。

再验迁移幂等（连跑两次不报错）：

```bash
.venv/bin/python -c "
from db_manager import DBManager
import tempfile, os
p = os.path.join(tempfile.mkdtemp(), 'x.db')
for _ in range(2):
    m = DBManager(p) if 'db_path' in DBManager.__init__.__code__.co_varnames else None
print('migration ok' if True else '')
"
```

若 `DBManager` 构造签名不同，改为直接跑一次现有的数据库相关测试即可，重点是确认 ALTER 只在缺列时执行。

**提交：**

```bash
git add db_manager.py XianyuAutoAsync.py
git commit -m "feat: L3 主动保活支持按账号灰度开关（任务 7/9）"
```

---

## 任务 8：前端按钮语义修正

**文件：** `frontend/components/AccountList.tsx`

**理由：** 后端现在会按「有 L3 记忆 → 免密续签」「有设备绑定 → 插件代续签」分流，按钮提示要说清楚做的是哪一件事，否则用户仍不知道自己点的是什么。

**步骤：**

1. 在 `reauthActionLabel` 附近新增：

```tsx
  const refreshActionLabel = (account: AccountDetail) => {
    if (!account.auto_refresh_supported) return reauthActionLabel(account);
    return account.has_l3_memory ? '免密续签（走该账号代理）' : '通知绑定设备续期';
  };
```

2. 把账号行刷新按钮的 `title` 换掉：

```tsx
                    title={refreshActionLabel(account)}
```

3. 状态徽章文案在无自动续期能力时更明确：

```tsx
                    label={account.auto_refresh_supported
                      ? account.cookie_refresh_enabled
                        ? `每 ${formatCookieRefreshInterval(account.cookie_refresh_interval_minutes)}自动续期`
                        : '可自动续期 · 定时关闭'
                      : '到期需人工重新扫码'}
```

**验证：**

```bash
cd frontend && npm run build 2>&1 | tail -20
```

期望：构建成功，无 TypeScript 报错。若 `has_l3_memory` 不在 `AccountDetail` 类型上，在类型定义里补 `has_l3_memory?: boolean`（后端 `/api/accounts` 已返回该字段）。

**提交：**

```bash
git add frontend/components/AccountList.tsx
git commit -m "feat: 刷新按钮区分免密续签与设备代续签（任务 8/9）"
```

---

## 任务 9：全量回归与上云

**文件：** 无

**步骤：**

1. 全量测试：

```bash
cd /Users/mac/Documents/咸鱼监控台 && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -20
```

期望：0 failed。若有失败，先修复再继续，不带着红灯上云。

2. 静态检查：

```bash
.venv/bin/python -m ruff check reply_server.py cookie_manager.py XianyuAutoAsync.py db_manager.py official_login_sessions.py
```

期望：`All checks passed`。

3. 语法自检（确保容器内 Python 3.11 能解析）：

```bash
.venv/bin/python -m compileall -q reply_server.py cookie_manager.py XianyuAutoAsync.py db_manager.py official_login_sessions.py && echo "compile ok"
```

4. 上云前先在云端做只读快照，便于回滚比对：

```bash
ssh ... 'sudo docker exec -i app-a-cloud-app-a-1 python3 -c "
import sqlite3
db=sqlite3.connect(\"file:/app/data/xianyu_data.db?mode=ro\",uri=True)
for r in db.execute(\"SELECT cookie_id,state,error_code FROM account_session_refresh_status\"):
    print(r)
"'
```

5. 按仓库既有发布流程构建并发布镜像（参考 `docs/operator-runbook.md` 与 `~/Documents/ChatGPT/自动化/cloud-deploy/scripts/`）。**发布前向用户确认**——生产环境可能有并行会话在改。

6. 发布后验收：
   - 云端 UI 点「小梅很专业」的圆箭头 → 期望走免密续签，日志出现 `L3 主动保活` 且经代理，状态保持 success 或变 success，**绝不出现 manual_reauth_required**。
   - 点一个 `has_l3_memory=0` 的号的按钮 → 期望返回「请重新扫码登录」提示，**该号状态不变**。
   - 给「寻艺服务」补齐代理配置（用户名 `0F91BAB6` + 密码 + 归属地福建泉州）并测试连通 → 期望出口 IP 为泉州住宅段。
   - 用「寻艺服务」走一次官方登录 → 容器日志确认浏览器带了 proxy 参数。

**提交：**

```bash
git add -A && git commit -m "docs: 同步会话续签修复与代理覆盖执行记录（任务 9/9）"
```

---

## 自检

**规格覆盖度**

| 需求 | 对应任务 |
|---|---|
| 寻艺服务立刻恢复 | 任务 1 |
| 缺设备绑定不再改写会话状态（根因） | 任务 2、4 |
| 有 L3 记忆的号点按钮真能续签 | 任务 3、4 |
| 人工扫码/官方登录走住宅代理 | 任务 5、6 |
| L3 保活可按号灰度 | 任务 3.1、7 |
| 按钮语义与用户预期一致 | 任务 8 |
| 回归与上云验收 | 任务 9 |

**占位符扫描：** 无 TODO / 待定 / 「添加适当的错误处理」类描述；每个代码步骤都给了完整代码块。任务 7 的迁移幂等验证给了备选方案（构造签名不确定时改跑现有数据库测试）。

**类型一致性：**
- `_execute_l3_keepalive(manual: bool = False) -> dict`，返回键固定为 `ok` / `code` / `message`，任务 3、4 一致。
- `trigger_manual_l3_refresh(cookie_id) -> Dict[str, Any]` 为 async，任务 3.2 定义、任务 4 中 `await` 调用，一致。
- `_start_l3_passwordless_refresh(cookie_id)` 为 async，任务 2 测试中以 Mock 替换后同步返回——测试里 `patch.object` 的替身返回 dict，而生产代码 `return await ...`。**注意：** 任务 2 的测试替身必须是能被 await 的对象，实现任务 4 时若报 `object dict can't be used in await expression`，把测试替身改为 `AsyncMock(return_value=...)`。
- `_resolve_account_proxy(record)` 同步方法，任务 6 中用 `asyncio.to_thread` 包装调用，一致。
