# 发货链路提速 + 平台状态自锁修复（2026-08-29 开工回执）

- 目标：①同买家多单即时发现（fan-out）＋付款核验 order_not_observed 短退避重试（2/4/8s），消除 30 秒兜底轮询造成的 p90 61-96 秒尾巴；②mark-fulfilled 幂等护栏回查平台真实状态破自锁；③对账重发器自动补「本地已发×平台待发货」并告警（默认 600s 一轮、每账号 ≤5 笔）。
- 顺序：任务 0 基线核验 → 3a 破自锁（invite_bridge.py）→ 1 定向发现 + 3b 对账重发器（invite_bridge_poller.py）→ 2 核验重试（XianyuAutoAsync.py）→ 3c 测试补齐 → 全量回归。
- 基线：三文件（test_invite_bridge / test_invite_bridge_delivery_fallback / test_delivery_payment_verification）75 passed + 2 subtests 已亲测；全量基线 N=1083 passed + 205 subtests（tmux 全量跑完，exit 0，614.61s）。
- 最大风险：mark-fulfilled 契约变更（本地已发不再直接返回成功）会改变既有用例 test_mark_fulfilled_already_shipped_without_platform_call 的断言前提——该用例断言的正是本次修复的缺陷行为，按新契约重写并加平台回查失败 fail-closed 用例兜底；其余门禁（ordinary+pending_ship、fail-closed）一律不放宽。
- 范围铁律：只改 XianyuAutoAsync.py / invite_bridge_poller.py / invite_bridge.py / order_sync_service.py(只读 helper) / delivery_stage_metrics.py(常量) / tests/；不部署、不动生产数据、不修存量卡单。

## 实现进展（2026-08-29）

- 3a 破自锁（invite_bridge.py）：mark-fulfilled 在本地已发时不再直接返回 succeeded——先 `_fetch_platform_order_status` 回查平台详情（fail-closed：查询失败一律 needs_review 不盲发）；平台已推进才算成功，平台仍待发货则走 `_execute_platform_ship`（免拼成团 + 虚拟发货，平台侧幂等）补发货。共享发货逻辑抽为模块级 helper 供对账重发器复用。
- 任务 1 定向发现（invite_bridge_poller.py `scan_buyer_orders` + XianyuAutoAsync.py 热路径钩子）：热路径完成一笔可信投递后，用一次 NOT_SHIP 待发货页定向查同买家其余待发货单（候选上限 5、同买家冷却 15s、复用 get_order_sync_lock），逐笔走与热路径一致的 `_verify_paid_order_for_delivery` fail-closed 门禁 + stage_order + scan_trusted_order；查不到静默交还 30 秒兜底轮询。
- 任务 2 核验重试（XianyuAutoAsync.py）：仅 error_code=order_not_observed（平台列表/详情暂未返回该订单）时按 2/4/8s 短退避重试，其余失败（requires_login/lead/身份不符等）保持立即放弃；`invite_delivery_latency` 埋点新增 buyer_fanout_ms / buyer_fanout_sent。
- 3b 对账重发器（invite_bridge_poller.py）：平台发现顺带登记「本地已发×平台 pending_ship」漂移单（零额外列表请求）+ logger.warning 告警；每 600s（env XIANYU_SHIP_RECONCILE_INTERVAL_SECONDS）一轮，逐笔回查平台详情双确认后补免拼+虚拟发货，每轮每账号 ≤5 笔，查询失败保留候选下轮再试（fail-closed）。
- 3c 测试：旧缺陷用例 test_mark_fulfilled_already_shipped_without_platform_call 按新契约重写为 3 条（平台已推进直接成功 / 平台待发货补发货 / 回查失败 fail-closed）；任务 1 新增 4 条、任务 2 新增 3 条、3b 新增 4 条（含反向验证：制造本地已发×平台待发样本，证明重发器补发货并告警，见 test_ship_reconciler_repairs_local_shipped_platform_pending_drift）。
- 验收结果（均亲测）：三文件 88 passed + 2 subtests（基线 75，重写 1、新增 14）；全量 `pytest tests/ -q` = 1096 passed + 205 subtests，exit 0（基线 1083，1096 = 1083 − 1 重写 + 14 新增），skipped 无新增。反向验证输出：`test_ship_reconciler_repairs_local_shipped_platform_pending_drift PASSED`——样本为本地 shipped/system_shipped=1 + 平台发现 pending_ship，重发器回查双确认后补免拼+虚拟发货一次（is_bargain/buyer 透传正确）、本地收敛 shipped、漂移队列清空，且捕获到「平台状态漂移」与「对账补发货成功」两条 WARNING 告警。
- 未动文件说明：order_sync_service.py / delivery_stage_metrics.py 在白名单内但本次无需改动（回查复用既有 fetch_xianyu_order_detail + parse_order_detail_payload，埋点复用既有阶段常量）。按任务铁律未部署、未动生产数据、未修存量卡单。

# 扫码账号免密自动续签（五阶段）：已发布上线

- 目标：扫码账号留下持久浏览器记忆（L3），cookie 失效后免密自动续签，不必再绑密码。
- 顺序：0 基线 → 1 扫码落 profile/DB 标记 → 2 免密续签优先于账密 → 3 放开门槛 → 4 分层错误 → 5 CDP 接管。全部完成并于 2026-08-25 18:42 部署生产（用户明确指令将「部署前真人 canary」调整为「部署后首次真实观察」）。
- 发布内容：五阶段 `7055de7` + 审查优化 `330ab1c`（免密续签防假成功 session_not_renewed、快速进入按钮缺失判 fast_entry_unavailable、CDP 建档失败不虚标 L3、启动失败分类收窄）。
- 最大风险不变：闲鱼 passport「快速进入」和 CfT 指纹会漂移；代码测试不是真机证据，首次真实免密续签（约 10h 后 L2 过期时）才是真机验证。

# 当前生产状态

- 最终复核时间：2026-08-26 01:52（Asia/Shanghai）。`com.cxywjx.xianyu-manager` 正在运行，PID `6309`（00:59:02 随性能优化发布的受控重载启动）；`8091` 只有一个 listener，本地与公网 `/health/ready` 均为 `ready`，迁移 `2026082502`。
- 当前生产已包含 Bundle A、滑块隐身账密自愈、扫码免密续签、Toast、UI 一致性、仪表盘图表重组、运营概览精修、前端关键路径性能优化（00:57 发布，含 `reply_server.py` 订单图片单飞/负缓存/四并发背压），以及营收趋势图恢复早上版本（01:43 纯静态发布）。公网前端入口为 `assets/index-B3QLJzmi.js` / `assets/index-Dt1bU_Op.css`，与生产 `static/index.html` 一致。
- 维护源 `main@b329c44` 已推送 origin/main 并与生产对齐：生产静态构建自 `b329c44` 对应工作树，生产后端与 `fcd1182` 一致（本次恢复为纯前端）。
- listener 注册隔离仍有效；`runtime_sessions` 只记录带 TTL 的临时操作，不代表 listener 数量。
- 维护源是 `/Users/mac/Documents/咸鱼监控台`，运行目录是 `/Users/mac/Library/Application Support/XianyuManager`；两棵树不得整树互相覆盖。工作树中的 `.cursor/` 用户资产继续保留，不清理、不覆盖。

## 部署后观察（替代真人 canary）

- 下次任一扫码账号登录成功 → 应写入 `browser_data/user_<unb>` 并置 `has_l3_memory=1`（账号列表出现 L3 标识、`auto_refresh_supported=true`）。
- 约 10 小时后 L2 过期 → 观察 `account_session_refresh`：`浏览器记忆免密续签成功`=目标；`session_not_renewed`=网络类可重试；`fast_entry_unavailable`=单向 manual + 一次性告警邮件（需重新扫码）。
- CDP 接管（任务 5）代码级失败关闭测试全过；本机真实 Chrome 冒烟需开 `--remote-debugging-port` 后另行执行，未配置 `XIANYU_CHROME_CDP_ENDPOINT` 时该路径不启用。

## 证据路由

- 营收趋势图恢复早上版本发布（08-26 01:43）：`outputs/trend-restore-20260826T014313+0800/`（verification-record.md、post-deploy-verify.txt；回滚在生产 `_rollback/trend-restore-20260826T014313+0800/`）。
- 前端关键路径性能优化发布（08-26 00:57）：`outputs/frontend-critical-20260826T005740+0800/`（verification-record.md、source-head.txt；回滚在生产 `_rollback/` 同名目录）。
- 运营概览精修发布（22:28，含过载事故记录）：`outputs/hero-refine-20260825T222457+0800/`（evidence/verification-record.md、original/、rollback/rollback.sh + static-original）。
- 仪表盘图表区重组发布（20:59）：`outputs/dashboard-refine-20260825T205258+0800/`。
- L3 发布：`outputs/l3-login-deploy-20260825T183702+0800/`（evidence/verification-record.md、original/、candidate/、rollback/rollback.sh + db-backup + static-original）。
- Toast 静态发布：`outputs/knowledge-toast-20260825T190639+0800/`（并行会话，纯前端）。
- 上一发布：`outputs/bundle-a-20260825T133612+0800/`（evidence/verification-record.md、patch、rollback）。
- 当前发布、历史发布与回滚：`docs/handoff.md`。
- 尚未闭合的外部门禁：`BLOCKED.md`。
