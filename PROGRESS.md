# 当前生产状态

- 最终复核时间：2026-08-25 14:55（Asia/Shanghai）。`com.cxywjx.xianyu-manager` 正在运行，PID `79093`（14:16:27 启动）；`8091` 只有一个 listener，本地与公网 `/health/ready` 均为 `ready`，health 报告迁移 `2026082401`（MAX 口径），补插迁移 `2026081801` 已于 14:16:28 应用，SQLite `integrity_check=ok`。
- 当前生产是 2026-08-25 Bundle A：在 08-24 Delivery Center 基础上叠加 AI 订单作用域对话/shadow 指标/模型调用预算、订单业务类型分类（非 `ordinary` 失败关闭）、告警策略收窄（普通聊天静默）以及当日上午的 device_id 探测热修；7 个后端文件与 `outputs/bundle-a-20260825T133612+0800/candidate/` 逐字节一致。滑块隐身账密自愈显式未部署（`account_session_refresh.py` 生产保持 `supports_automatic_refresh=False` 旧语义，`utils/xianyu_slider_stealth.py` 仅存维护源）。
- 前端静态资源本轮未动，公网入口仍为 `assets/index-ETBBt5BG.js` 与 `assets/index-B0KwPd4v.css`（2026-08-25 knowledge badge 发布产物）。
- listener 注册隔离仍有效：长期 `XianyuLive` 默认注册；全量与分页商品同步显式使用 `register_instance=False`。14:16 重启后 4 个账号 listener 重连，日志窗口无 `listener_unavailable`、无 `Traceback`。
- 普通账号 `pending_only` 待发货回退与 `skipped_reauth` 隔离继续在生产；剩余的是逐账号观察和低金额真人订单金丝雀，不是待部署代码。
- `runtime_sessions` 只记录带 TTL 的登录、训练和刷新操作，不代表账号 WebSocket listener 数量。
- 维护源是 `/Users/mac/Documents/咸鱼监控台`，运行目录是 `/Users/mac/Library/Application Support/XianyuManager`；两棵树不得整树互相覆盖。当前工作树含用户资产，禁止 reset、checkout 覆盖、clean 或未经确认的清理。

## 维护源待发布

- 无积压候选。维护源与生产的已知差异仅剩滑块自愈特性（`XianyuAutoAsync.py` 两个滑块 hunk、`account_session_refresh.py` 新语义、`utils/xianyu_slider_stealth.py`），该特性等待真机金丝雀后另行决策，不属于待发布队列。

## 证据路由

- 当前发布：`outputs/bundle-a-20260825T133612+0800/`（evidence/verification-record.md、patch、rollback）。
- 当前发布、历史发布与回滚：`docs/handoff.md`。
- Delivery Center 证据：`outputs/delivery-center-20260824T192237+0800/release-candidate-v4/`。
- 尚未闭合的外部门禁：`BLOCKED.md`。
