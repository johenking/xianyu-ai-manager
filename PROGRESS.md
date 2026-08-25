# 当前生产状态

- 最终只读复核时间：2026-08-25 00:17（Asia/Shanghai）。`com.cxywjx.xianyu-manager` 正在运行，PID `88845`；`8091` 只有一个 listener，本地与公网 `/health/ready` 均为 `ready`，迁移 `2026082401`，SQLite `integrity_check=ok`。
- 当前生产是 2026-08-24 Delivery Center 最终候选：三页工作台与迁移已加载，生产后端和 74 个静态文件均与 `release-candidate-v4` 一致；公网入口为 `assets/index-DgjfWuPd.js` 与 `assets/index-CrHg7Yfi.css`。
- listener 注册隔离仍有效：长期 `XianyuLive` 默认注册；全量与分页商品同步显式使用 `register_instance=False`。启动后日志窗口没有新增 `listener_unavailable` 或 `Traceback`。
- 普通账号 `pending_only` 待发货回退与 `skipped_reauth` 隔离已在生产落盘并由当前进程加载；剩余的是逐账号观察和低金额真人订单金丝雀，不是待部署代码。
- `runtime_sessions` 只记录带 TTL 的登录、训练和刷新操作，不代表账号 WebSocket listener 数量。
- 维护源是 `/Users/mac/Documents/咸鱼监控台`，运行目录是 `/Users/mac/Library/Application Support/XianyuManager`；两棵树不得整树互相覆盖。当前工作树含大量未提交和未跟踪用户资产，禁止 reset、checkout 覆盖、clean 或未经确认的清理。

## 维护源待发布

- 2026-08-19 告警策略收窄仍只在维护源：普通客户聊天和自动发货成功保持静默，异常与人工复核继续告警。当前生产 `XianyuAutoAsync.py` 仍有客户消息通知调用，生产继续按旧策略运行。证据位于 `outputs/notification-policy-20260819T001335+0800/`；若后续授权发布，只应用该单文件差异并受控 reload 一次。

## 证据路由

- 当前发布、历史发布与回滚：`docs/handoff.md`。
- Delivery Center 最终证据：`outputs/delivery-center-20260824T192237+0800/release-candidate-v4/`。
- listener 隔离证据：`outputs/listener-registry-fix-20260823T210631+0800/`。
- 尚未闭合的外部门禁：`BLOCKED.md`。
