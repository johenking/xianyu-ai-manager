# 云端登录代理全覆盖 + 发货兜底 实现计划

> **面向 AI 代理的工作者：** 用 `executing-plans` 逐任务实现；每任务先写失败测试再实现，频繁 commit，不部署等用户拍板。

**目标：** 让云端闲鱼账号的**所有**登录/续签/重登路径都从住宅代理出口（根治机房 IP 滑块），死号能自助扫码重登且走住宅 IP；给自动发货补上「令牌过期重试」与「发货失败对账补救」两道兜底，消灭需人工救的卡单。

**架构：** 账号住宅代理已在 mtop 探测 / 滑块自愈 / 自动续签 / L3 保活 / 官方登录五条路生效；本计划补齐最后一条 **UI 扫码路径**（`QRLoginManager` 本就支持 proxy，只差 generate 时按 cid 注入），并在发货链补两处兜底。所有改动**默认无代理 = 原行为**（零回归），三处代码改动凑一次原子部署（单次停机约 1 分钟）。

**技术栈：** Python（FastAPI / httpx / Playwright）、SQLite、React + TypeScript、云 Docker 原子部署。

---

## 基线（已完成，勿重做）
- 5 号（小梅/寻艺/chen/强子/ELANAI）已切正式泉州电信住宅代理 `tunpool-q6keg.qg.net:27349`；陈潇轩按拍板不绑。
- session-refresh 根因修复（缺设备绑定不再误标过期）已上云；fix 分支 6 提交已合 main 推 origin（全量 1237 绿）。
- 寻艺 `l3_keepalive_enabled=True`（L3 保活按号试点第一个号）。
- 新正式代理验收通过：出口 110.81.23.150 = 真泉州电信家宽住宅、mtop 200、出口稳定。

## 任务 A｜扫码登录接账号代理〔死号重登的前提〕
**文件**
- `utils/qr_login.py`：`QRLoginSession` 增 `proxy` 字段；`QRLoginManager.generate_qr_code(proxy=None)` 生成 session 时记录该次 proxy；`_poll_qrcode_status` / verification 相关 httpx 由读 `self.proxy` 改为 `session.proxy or self.proxy`（per-session 代理，兼容全局单例）。
- `reply_server.py`：`generate_qr_code` 增可选 `cid`；有 cid → `db_manager.get_account_proxy_config(cid)` → `qr_login_manager.generate_qr_code(proxy=cfg)`；无 cid / 无代理 = 原行为。会话 registry 归属校验不变。
- `frontend/services/api/accounts.ts`：`generateLoginQr(cid?: string)` 透传。
- `frontend/components/AccountList.tsx`：重登（`reauth_action==='qr_login'`）时带该账号 cid；新增账号不带 cid。
**测试** `tests/test_qr_login_proxy.py`：①配代理→session 记录 proxy、httpx 走该出口 ②无代理→直连（字节级原行为）③`generate_qr_code(cid=)` 查到并注入账号代理 ④代理解析异常绝不挡住登录。
**验证** `pytest tests/ -q`（≥1237 + 新增全绿）+ `ruff check .` + 前端 `vitest` / `tsc --noEmit`。
**提交** `feat: 扫码登录按账号注入住宅代理`。

## 任务 B+C｜两个发货缺口（直接执行既有细计划，勿重新调研）
发货调查会话已产出**更细且已核实关键事实**的实施计划：`docs/superpowers/plans/2026-08-29-token-retry-and-fulfilled-unshipped-reconcile.md`（当时用户拍板留档不实施，现已改拍板实施）。按该计划 Stage 1→3 执行，要点：
- **缺口 1**（令牌过期重试）：`secure_confirm_decrypted` / `secure_freeshipping_decrypted` 对称改——分类拆出 `token_expired`，失败响应的 Set-Cookie 本就合并回 `cookies_str`（新令牌已在手），循环内「令牌确实换新且本单未重试过→原地重试一次」，最终失败对外仍报 `session_invalid`（上游零改动）。
- **缺口 2**（履约未发货对账）：`invite_bridge.py` 新增 `_has_succeeded_fulfillment_message`（查 `invite_bridge_operations`，宁漏勿误）；`invite_bridge_poller._note_ship_drift` 追加分支「平台待发×本地待发×码已发→入 `_ship_drift`」；对账器零改动（发货前二次复核平台状态，fail-closed，绝不重发码）。
- 涉改 4 文件 + 测试 2 文件，无 schema 变更零迁移。
**验证** `pytest tests/ -q` + `ruff check .`。
**提交** 按该计划的 Stage 拆分提交。

## 任务 D｜原子部署（A+B+C 一次上云）
分层补丁：基线 = 当前运行镜像，覆盖改动的 3 个后端文件 + static 整代前端 → 构建候选镜像 → 备份 manifest → 切 `APP_A_IMAGE` → `compose up -d --wait` → 核验（healthy / 迁移号不变 / 双公网 ready 200 / 启动切片 Traceback=0 / 账号监听心跳恢复）→ 回滚脚本就绪。停机约 1 分钟，只碰 app-a。

## 运维 / 观察项（非开发，按序推进）
1. **青果通道**：后台优化 get 提取（隧道账密已可用、不阻塞；提取报 NO_AVAILABLE_CHANNEL 是通道数 1 被占/限流）。
2. **寻艺 L3 保活盯 72h**：前端对寻艺点「免密续签」可即时验证一次 fast_entry 是否成功（BLOCKED 已久的核心验收）；否则等 ≤8h 保活周期自动跑。
3. **死号错峰重登**（任务 A 上线后）：强子 / ELANAI，一天 ≤2、间隔 ≥2h，走泉州住宅 IP——这也是「住宅 IP 能否过滑块」的最终验收。
