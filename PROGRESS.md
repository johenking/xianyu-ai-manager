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
