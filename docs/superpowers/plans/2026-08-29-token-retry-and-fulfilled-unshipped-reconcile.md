# 发货令牌过期就地重试 + 已履约未发货对账兜底 实施计划

> 日期：2026-08-29
> 来源调研：`会话-2026-08-29.md`「领券 2.79 元订单发货偏慢调研」
> 前置结论：2.79 领券单发货慢与优惠券/金额无关，真因是发货确认瞬间撞上
> `_m_h5_tk` 令牌过期（`FAIL_SYS_TOKEN_EXPIRED`）→ 平台发货失败且无任何自动补救。
> 本计划修两个缺口，均为通用链路缺陷，与商品/价格无关。

## 目标

1. **缺口一（治本）**：`SecureConfirm.auto_confirm` / `SecureFreeshipping.auto_freeshipping`
   遇到令牌过期时，利用平台已随失败响应下发的新令牌（`Set-Cookie` 已被
   `_post_confirm` 合并进 `self.cookies_str`）**就地立即重试一次**，而不是立即失败关闭。
2. **缺口二（兜底）**：邀请桥轮询器的发货对账目前只覆盖「本地已发货 × 平台待发货」。
   扩展 `_note_ship_drift`，把「**兑换码已发成功（fulfillment-message 已 succeeded）
   × 本地与平台都还是待发货**」的订单也列入对账队列，由既有
   `_reconcile_shipped_drift` 自动补发货，消灭人工介入。

## 非目标

- 不改会话失效（`FAIL_SYS_SESSION_EXPIRED`）、小二介入、风控等其它失败分类的行为——
  这些仍然立即失败关闭。
- 不动账号重登/会话刷新链路（`session-refresh-fix` 已在并行会话处理）。
- 不给对账器新增发货阶段打点（现有 `对账补发货成功` WARNING 日志已够观测）。

## 现状锚点（写代码前先读）

| 位置 | 现状 |
| --- | --- |
| `secure_confirm_decrypted.py` `SESSION_INVALID_MARKERS`（约 L60-70） | 把 `fail_sys_token_expired`、`fail_sys_token_exoired`、`token过期` 等令牌类标记与会话类标记混在一起，统一归 `session_invalid` → 立即失败不重试 |
| `secure_confirm_decrypted.py` `auto_confirm`（L257-362） | while 循环，每轮重新 `_build_confirm_request`（从 `self.cookies_str` 取 `_m_h5_tk` 令牌）；`_post_confirm`（L216-255）每次都把响应 `Set-Cookie` 合并回 `self.cookies_str`。即：**令牌过期失败的那一刻，新令牌已经在手上**，循环里 `continue` 一次即可用新令牌重签 |
| `secure_freeshipping_decrypted.py` | 与 confirm 同源镜像：`classify_freeshipping_ret`（L81 起）分类语义一致，`auto_freeshipping`（L225 起）循环结构一致，需做完全对称的修改 |
| `invite_bridge_poller.py` `_note_ship_drift`（L445-486） | 仅当 `locally_shipped and platform_pending` 才入对账队列；履约成功但平台发货失败的单（本地 `pending_ship`）落不进来 |
| `invite_bridge_poller.py` `_reconcile_shipped_drift`（L487-581） | 对账器本身逻辑通用：复核平台状态→补发货→本地置 shipped；候选来源扩展后**无需改动** |
| `invite_bridge.py` `invite_bridge_operations` 表 | `operation_key` 形如 `fulfillment-message-{order_id}`，`status='succeeded'` 即兑换码已发到买家。表由 `invite_bridge.py` 直接经 `db_manager.lock/conn` 读写（无专用访问器，沿用此模式） |
| `invite_bridge_poller.py` L18-25 | 已从 `invite_bridge` 导入 `_execute_platform_ship` 等私有辅助，新辅助函数照此导入 |
| `tests/test_secure_confirm_retry_policy.py` | `test_session_and_token_expired` 现断言令牌过期→`session_invalid`，需拆分；`_FakeResponse.headers = {}`，需要支持 `getall` 的假 headers |
| `tests/test_invite_bridge.py` L3400-3601 | 已有 `_ReconcileDatabase` 假件与对账测试组，新测试照此编写 |

## 验收标准

- 令牌过期单测：过期响应携带新令牌 → 第二次请求成功，总请求数 2，无退避等待；
  新令牌未变化/二次仍过期 → 失败关闭，对外 `category` 仍为 `session_invalid`。
- 对账单测：兑换码已发×两边待发货 → 候选入队且不再走补发码流程；无成功
  fulfillment-message 记录的待发货单不入队（行为与现状一致）。
- 全量回归 `python3 -m pytest tests/ -q` 通过（基线 677 passed）。
- 部署后观察：日志出现 `令牌已换新，立即重试` 即缺口一生效；构造/等到一笔
  漂移单看到 `对账补发货成功` 即缺口二生效。

---

## Stage 1：令牌过期就地重试（confirm + freeshipping）

### Task 1.1 先写失败测试（confirm）

文件：`tests/test_secure_confirm_retry_policy.py`

1. 给假件加可用 headers：新增
   ```python
   class _FakeHeaders(dict):
       def getall(self, key, default=None):
           value = self.get(key)
           return [value] if value is not None else (default or [])
       def __contains__(self, key):
           return any(str(k).lower() == str(key).lower() for k in self.keys())
   ```
   并让 `_FakeResponse` 接受可选 `headers` 参数（默认空 `_FakeHeaders()`）。
   注意 `_post_confirm` 用小写 `'set-cookie' in response.headers` 探测后
   `getall('Set-Cookie')`，假件需大小写兼容（如上 `__contains__` 忽略大小写，
   `getall` 同样忽略大小写取值）。
2. 拆分 `test_session_and_token_expired`：
   - 会话类（`FAIL_SYS_SESSION_EXPIRED::Session过期`、`FAIL_SYS_MINI_LOGIN...` 等）
     保持断言 `category == "session_invalid"`、单次请求即失败；
   - 令牌类（`FAIL_SYS_TOKEN_EXPIRED::令牌过期`、`FAIL_SYS_TOKEN_EXOIRED::令牌过期`、
     `FAIL_SYS_TOKEN_EMPTY::令牌为空`）改为断言 `classify_confirm_ret` 返回
     `"token_expired"`。
3. 新增 `test_token_expired_retries_once_with_fresh_token`：
   - 第 1 个响应 `ret=["FAIL_SYS_TOKEN_EXPIRED::令牌过期"]` 且 headers 带
     `Set-Cookie: _m_h5_tk=newtoken123_1756500000000; Path=/`；
   - 第 2 个响应 `ret=["SUCCESS::调用成功"]`；
   - 断言：`result["success"] is True`、`session.post_calls == 2`、
     `sleep_calls == []`（就地重试不退避）、`"newtoken123" in confirm.cookies_str`。
4. 新增 `test_token_expired_without_fresh_token_fails_closed`：
   - 单个过期响应、**不带** Set-Cookie；
   - 断言：失败、`category == "session_invalid"`、`post_calls == 1`。
5. 新增 `test_token_expired_twice_fails_closed`：
   - 两个过期响应，各自都带新令牌（令牌值不同）；
   - 断言：失败、`post_calls == 2`（令牌重试只做一次）。

运行 `python3 -m pytest tests/test_secure_confirm_retry_policy.py -q`，确认新测试红。

### Task 1.2 实现（confirm）

文件：`secure_confirm_decrypted.py`

1. 拆分类标记：从 `SESSION_INVALID_MARKERS` 移出令牌类，新增
   ```python
   TOKEN_EXPIRED_MARKERS = (
       'fail_sys_token_expired',
       'fail_sys_token_exoired',
       'fail_sys_token_empty',
       '令牌过期',
       '令牌为空',
       'token过期',
   )
   ```
   `classify_confirm_ret` 在 session 判定**之前**先匹配令牌类，返回
   `("token_expired", ret_text)`；docstring 补一行分类说明。
2. `SecureConfirm` 加私有小工具：
   ```python
   def _current_token(self):
       cookies = trans_cookies(self.cookies_str or "")
       return str(cookies.get('_m_h5_tk') or '').split('_', 1)[0]
   ```
   （`trans_cookies` 顶部已导入，如无则补。）
3. `auto_confirm` 循环内：请求前记 `attempt_token = self._current_token()`；
   循环外初始化 `token_retry_used = False`；在 `already_shipped` 分支后新增：
   ```python
   if category == "token_expired":
       # 平台随失败响应已下发新 _m_h5_tk（_post_confirm 已合并进 cookies_str）。
       # 令牌确实换新且本单未重试过 → 立即原地重试一次；否则按会话失效失败关闭。
       fresh_token = self._current_token()
       if (not token_retry_used and fresh_token
               and fresh_token != attempt_token
               and attempts_used < MAX_CONFIRM_ATTEMPTS):
           token_retry_used = True
           logger.warning(
               f"【{self.cookie_id}】确认发货遇令牌过期，令牌已换新，立即重试，"
               f"订单ID: {order_id}"
           )
           continue
       logger.warning(
           f"【{self.cookie_id}】❌ 确认发货令牌过期且无法换新，失败关闭，"
           f"错误码={error_code}"
       )
       return {
           "error": f"自动确认发货失败: {error_code}",
           "order_id": order_id,
           "category": "session_invalid",
       }
   ```
   对外失败 `category` 保持 `session_invalid`，上游（邀请桥/自动发货）零改动。

跑 Task 1.1 测试至绿，再全量跑该文件。

提交：`fix: 确认发货令牌过期时用响应新令牌就地重试一次`

### Task 1.3 镜像修改（freeshipping）

文件：`tests/test_secure_freeshipping_retry_policy.py`（先看现有结构，照 Task 1.1 出三个对称测试）、`secure_freeshipping_decrypted.py`（照 Task 1.2：`TOKEN_EXPIRED_MARKERS`、`classify_freeshipping_ret` 前置匹配、`auto_freeshipping` 循环加 `token_expired` 分支与 `_current_token`）。

注意两文件同源但独立维护（仓库既有模式），**不要**抽公共模块。

提交：`fix: 免拼确认令牌过期时用响应新令牌就地重试一次`

---

## Stage 2：已履约未发货对账兜底

### Task 2.1 辅助查询 + 失败测试

文件：`invite_bridge.py`（新增函数）、`tests/test_invite_bridge.py`（新测试）

1. `invite_bridge.py` 新增模块级函数（放 `_load_operation` 附近，沿用其
   `db_manager.lock/conn` 直查模式）：
   ```python
   def _has_succeeded_fulfillment_message(cookie_id: str, order_id: str) -> bool:
       """兑换码是否已确认送达买家（fulfillment-message 操作已 succeeded）。"""
       try:
           with db_manager.lock:
               cursor = db_manager.conn.cursor()
               cursor.execute(
                   """
                   SELECT 1 FROM invite_bridge_operations
                   WHERE cookie_id = ? AND order_id = ?
                     AND operation_key = ? AND status = 'succeeded'
                   LIMIT 1
                   """,
                   (cookie_id, order_id, f"fulfillment-message-{order_id}"),
               )
               return cursor.fetchone() is not None
       except Exception as exc:
           logger.warning(f"查询履约消息状态失败 {cookie_id}/{order_id}: {exc}")
           return False
   ```
   查询失败返回 `False`（宁可漏兜底，不可误补发货）。
2. 测试先行：
   - 直接对该函数写正/反用例（插入 succeeded / failed / 无记录三种数据）；
   - 轮询器用例 `test_note_ship_drift_registers_fulfilled_unshipped`：
     平台 `pending_ship` × 本地 `pending_ship` × monkeypatch
     `poller_module._has_succeeded_fulfillment_message` 返回 True →
     断言 `_note_ship_drift` 返回 True、候选进 `poller._ship_drift`、
     不再走补发码/暂存流程；
   - 反例 `test_note_ship_drift_skips_unfulfilled_pending`：同状态但辅助函数
     返回 False → 返回 False、不入队（行为与现状一致）。

### Task 2.2 扩展 `_note_ship_drift`

文件：`invite_bridge_poller.py`

1. L18-25 导入列表加 `_has_succeeded_fulfillment_message`。
2. `_note_ship_drift` 中，在既有 `locally_shipped and platform_pending` 分支
   **之后**追加：
   ```python
   if platform_pending and not locally_shipped:
       if _has_succeeded_fulfillment_message(cookie_id, order_id):
           # 码已发到买家但平台发货当时失败（如令牌过期）：
           # 只欠平台一步确认发货，列入对账补发，不再重复发码。
           self._ship_drift.setdefault(key, candidate_payload)
           logger.warning(
               f"邀请桥发现已履约未发货订单，已列入对账补发 "
               f"cookie_id={cookie_id} order_id={order_id}"
           )
           return True
   ```
   （`candidate_payload` 结构与既有分支一致：cookie_id/order_id/item_id/
   buyer_id/is_bargain 等字段，照抄既有构造。）
3. `_reconcile_shipped_drift` 的 docstring 与「平台状态漂移」汇总日志措辞改为
   覆盖两类来源：「本地已发×平台待发 / 码已发×两边待发」。逻辑零改动。

跑 Task 2.1 测试至绿；随后全量 `python3 -m pytest tests/test_invite_bridge.py -q`。

提交：`fix: 邀请桥对账兜底覆盖兑换码已发但平台发货失败的订单`

---

## Stage 3：回归与部署

### Task 3.1 全量回归

```bash
python3 -m pytest tests/ -q
```
基线 677 passed，允许只增不减。有挂必须修复后再进入部署。

### Task 3.2 部署（派生镜像，沿用 22:20 session-refresh-fix 流程）

1. `ssh` 服务器，`docker inspect xianyu-app-a --format '{{.Config.Image}}'`
   确认现役镜像 tag（应为 `...:session-refresh-fix-20260829-2220`）。
2. scp 四个改动文件（`secure_confirm_decrypted.py`、
   `secure_freeshipping_decrypted.py`、`invite_bridge.py`、
   `invite_bridge_poller.py`）到服务器构建目录，写派生 Dockerfile：
   `FROM <现役镜像>` + `COPY` 四文件，build tag
   `token-retry-reconcile-20260829-<HHMM>`。
3. 更新 compose 中 app-a 镜像 tag → `docker compose up -d app-a`；
   B 容器（app-b）观察 A 稳定后同法跟进。
4. 验证：
   - `docker logs xianyu-app-a --since 10m` 无新增 ERROR；
   - 若期间恰有令牌过期：应看到 `令牌已换新，立即重试` 且随后发货成功；
   - 有漂移单时应看到 `已列入对账补发` → ≤10 分钟内 `对账补发货成功`。
5. 回滚预案：`docker compose` 镜像 tag 改回
   `session-refresh-fix-20260829-2220` 并 `up -d`，数据库无 schema 变更、零迁移。

### Task 3.3 收尾

- `会话-2026-08-29.md` 补记：改动文件、镜像 tag、验证结果。
- 观察期一晚：次日复查 `DELIVERY_STAGE_SUMMARY` 中 shipped 缺失率是否归零。

## 风险与权衡

- **就地重试会多打一次平台接口**：仅限令牌确实换新且每单一次，风控风险可忽略；
  会话类失败仍严格失败关闭。
- **对账兜底误判**：入队条件要求 fulfillment-message 明确 `succeeded`，且对账器
  发货前还会二次复核平台状态（`_reconcile_shipped_drift` 既有 fail-closed 逻辑），
  双保险杜绝盲发。
- **令牌重试后仍失败**：对外 category 不变（`session_invalid`），上游行为与今天
  完全一致，不会引入新状态。
