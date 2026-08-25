# 扫码账号免密自动续签（五阶段）：已发布上线

- 目标：扫码账号留下持久浏览器记忆（L3），cookie 失效后免密自动续签，不必再绑密码。
- 顺序：0 基线 → 1 扫码落 profile/DB 标记 → 2 免密续签优先于账密 → 3 放开门槛 → 4 分层错误 → 5 CDP 接管。全部完成并于 2026-08-25 18:42 部署生产（用户明确指令将「部署前真人 canary」调整为「部署后首次真实观察」）。
- 发布内容：五阶段 `7055de7` + 审查优化 `330ab1c`（免密续签防假成功 session_not_renewed、快速进入按钮缺失判 fast_entry_unavailable、CDP 建档失败不虚标 L3、启动失败分类收窄）。
- 最大风险不变：闲鱼 passport「快速进入」和 CfT 指纹会漂移；代码测试不是真机证据，首次真实免密续签（约 10h 后 L2 过期时）才是真机验证。

# 当前生产状态

- 最终复核时间：2026-08-25 19:19（Asia/Shanghai）。`com.cxywjx.xianyu-manager` 正在运行，PID `17178`（18:41 kickstart）；`8091` 只有一个 listener，本地与公网 `/health/ready` 均为 `ready`，迁移 `2026082502`（`cookies.has_l3_memory/l3_memory_at` 加列），SQLite 迁移演练与部署均 integrity ok。
- 当前生产 = Bundle A + 滑块隐身账密自愈（`10fe06a`）+ 扫码免密续签五阶段（`7055de7`）+ 审查优化（`330ab1c`）+ 知识档案/商品 Toast 静态发布（`874a29a`）：后端与源码 `330ab1c` 对齐（替换 8 个后端文件、新增 `utils/xianyu_l3_memory.py` 与 `utils/xianyu_slider_stealth.py`、`global_config.yml` 的 `TOKEN_REFRESH_INTERVAL` 3600→1800），前端与源码 `874a29a` 对齐。
- 前端公网入口现为 `assets/index-DX2cSsFw.js` / `assets/index-BsM2xUcO.css`：L3 发布（18:42，worktree 检出 `330ab1c` 干净构建，入口 `index-B2WW7m1I.js`，AccountList 增加 L3 记忆标识与免密续签开关）之后，同日 19:09 并行会话以纯静态方式发布 Toast 反馈（零停机、PID 不变、L3 UI 保留）。
- 重启后两账号 listener 心跳与消息接收正常；重启窗口 8 处瞬时 WS 连接 ERROR 已自愈，无 Traceback。
- listener 注册隔离仍有效；`runtime_sessions` 只记录带 TTL 的临时操作，不代表 listener 数量。
- 维护源是 `/Users/mac/Documents/咸鱼监控台`，运行目录是 `/Users/mac/Library/Application Support/XianyuManager`；两棵树不得整树互相覆盖。并行会话的 Toast/ItemList 前端改动已随 `874a29a` 提交并发布，工作树现仅剩未跟踪的 `.cursor/`（用户配置资产，禁止清理或提交）。

## 部署后观察（替代真人 canary）

- 下次任一扫码账号登录成功 → 应写入 `browser_data/user_<unb>` 并置 `has_l3_memory=1`（账号列表出现 L3 标识、`auto_refresh_supported=true`）。
- 约 10 小时后 L2 过期 → 观察 `account_session_refresh`：`浏览器记忆免密续签成功`=目标；`session_not_renewed`=网络类可重试；`fast_entry_unavailable`=单向 manual + 一次性告警邮件（需重新扫码）。
- CDP 接管（任务 5）代码级失败关闭测试全过；本机真实 Chrome 冒烟需开 `--remote-debugging-port` 后另行执行，未配置 `XIANYU_CHROME_CDP_ENDPOINT` 时该路径不启用。

## 证据路由

- L3 发布：`outputs/l3-login-deploy-20260825T183702+0800/`（evidence/verification-record.md、original/、candidate/、rollback/rollback.sh + db-backup + static-original）。
- Toast 静态发布：`outputs/knowledge-toast-20260825T190639+0800/`（并行会话，纯前端）。
- 上一发布：`outputs/bundle-a-20260825T133612+0800/`（evidence/verification-record.md、patch、rollback）。
- 当前发布、历史发布与回滚：`docs/handoff.md`。
- 尚未闭合的外部门禁：`BLOCKED.md`。
