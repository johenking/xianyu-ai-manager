# Handoff

## 2026-08-26 运营概览营收趋势图恢复早上版本发布

依据用户反馈（早上版本观感优于晚间改版）把营收趋势图恢复到 2026-08-25 早上基线 `91bf92a`，其余晚间改版全部保留（hero 左列 flex 布局与 4 指标含客单价、商品成交榜合并、客单价分布、买家构成、账号贡献、BusinessInsights 静默跟刷不动）。恢复内容与 `91bf92a` 字节级一致：折线 linear→monotone（线宽 2.5、去 activeDot）、撤销 `ReferenceDot` 峰谷图上标注（连带 TrendMarkerLabel/anchorFor）、渐变 0.32、网格恢复虚线、柱 fillOpacity 0.28 圆角 [3,3]、图高 192→174px（Dashboard 的 Suspense 占位同步）；`getTrendHighlights` 移除 `peakAmount/lowAmount` 扩展，两个测试文件同步回退（峰谷标注用例删除、ResponsiveContainer mock 恢复简单版）。维护源提交 `b329c44` 已推送 `origin/main`。

纯前端静态发布、零停机、后端零动作零重启（PID `6309` 不变）：候选=生产基线 `fcd1182`（前端关键路径性能优化已在线）+ 仅本次恢复，代际链条核验维护源上一代==生产当前代；32 新资产 `rsync --ignore-existing` 增量落盘（assets 226→258、逐一 SHA-256 一致），`index.html` 与 `.asset-generations.json` cp+mv 原子替换（入口 `index-COZcVS0D.js`→`index-B3QLJzmi.js`）。门禁：tsc 零错误、定向 3 测试文件 33 passed（单 worker）、vite build + verify:build orphaned=0；全量 vitest 按共机重活禁令未跑。发布后本地/公网 readiness `ready`、迁移 `2026082502` 不变、单 `8091` listener、本地与公网 HTML 及入口 JS 哈希一致、`DashboardCharts-BDQ3GYMP.js` 公网 200、`rollback.sh --check=PASS`。证据位于 `outputs/trend-restore-20260826T014313+0800/`，回滚单元位于生产 `_rollback/trend-restore-20260826T014313+0800/`（纯静态回切、无需重启）。

## 2026-08-25 仪表盘图表区重组与账号贡献聚合发布

本轮把仪表盘优化发布到生产（20:52-20:59，晚于当日 L3 与 Toast 发布）：后端 `db_manager.py` 的 `get_order_analytics` 新增 account_stats 聚合（`6b2edbc`，按 `cookie_id` 分组、`COALESCE(NULLIF(c.remark,''), o.cookie_id)` 备注优先命名、金额降序 Top 20，沿用既有 from/where 子句继承用户隔离与净销售口径）；前端静态整代切换到维护源 HEAD `61437f7` 构建产物（入口 `index-CqWHdiEA.js` / `index-D3VyG5-z.css`）。仪表盘重组内容：商品销量排行与下单占比合并为「商品成交榜」（销售额/订单量切换）、新增客单价分布直方图（前端按订单实付分五档）、买家构成环形（复购/单次）、账号贡献 Top 6；地区分布下线（生产 30 天 7236 笔订单 `receiver_city` 全空）；HeroTrend 与 BusinessInsights 视觉中性化（图标统一灰、频次分布统一 ShareBars），BusinessInsights 在仪表盘摘要指纹变化时静默跟刷（15s 轮询数据不变则不发请求、失败保留旧内容）。整代构建同时带上并行会话当日完成的 UI 一致性批次 A（`08abe07`）与批次 B（`4117f14`，全站 ConfirmDialog 替代原生 confirm），均已过各自门禁。

部署为在线原子（未预卸载 LaunchAgent）：部署前归因确认生产 `db_manager.py` 与维护源 diff 精确等于 account_stats 单一 hunk、无生产热修被覆盖；新资产 `rsync --ignore-existing` 增量落盘（旧代资产保留），`index.html` 与 `db_manager.py` cp+mv 原子替换后一次受控 kickstart。门禁：后端 `1017 passed / 201 subtests`（≥ 基线，skipped=0）、Ruff 全绿、前端 tsc 零错误、vite build 通过、仪表盘相关 37 用例全过。生产核验：新 PID `69388`（20:55:58）、单 `8091` listener、本地/公网 readiness `ready`、迁移 `2026082502` 不变（本次无迁移无 DB 写入）、公网入口 SHA256 与本地一致、4 个账号 listener 心跳正常、日志无新增 ERROR（「缺少 cardList」为部署前既有的平台侧周期现象，频次未恶化）；account_stats 同构 SQL 在冷备副本返回多账号真实聚合行。证据与可执行回滚位于 `outputs/dashboard-refine-20260825T205258+0800/`（`rollback/rollback.sh --check=PASS`）。

同日 22:28 依据用户预览反馈完成运营概览跟进发布（`64c373b`，纯前端静态、零停机不重启）：hero 网格 `items-end`→`items-stretch`、左列 flex 上下撑满（营收数字顶对齐、指标行钉底消除左上空白），指标 3→4 列新增「客单价」，列宽 5:7→4.5:7.5；趋势折线 monotone→linear 去平滑波浪假象，`ReferenceDot` 图上直接标注营收最高点（黄）/最低点（灰）+ ¥金额（口径与峰谷条一致仅已结束时段，金额全 0 不标、全相等只标峰值，近边缘自动换锚点），图表高 174→192px；`getTrendHighlights` 扩展 `peakAmount/lowAmount`，测试 mock 改为透传宽高使 SVG 标注可断言（新增 5 用例）。部署=32 新资产增量落盘（assets 131→163）+ `index.html` 原子替换（入口 `index-CqWHdiEA.js`→`index-DoyWuvdx.js`），后端零动作、PID `69388` 不变；公网入口哈希与本地一致、新入口 JS 公网 200、`rollback.sh --check=PASS`。证据位于 `outputs/hero-refine-20260825T222457+0800/`。发布前 22:05 曾发生整机过载事故（多会话并行重活叠加本会话后台全量 vitest，load 峰值 199、swap 10.1G/11.2G 近耗尽，cloudflared 被饿死致公网站点全部超时）：杀测试进程 + 用户关闭虚拟机后 22:21 恢复（load 22、公网 200），后端全程未崩（PID/心跳不变）。经验固化：生产与开发共机，全量前端测试等高并发重活不得在本机后台运行，改动门禁以定向用例 + 构建为准。

## 2026-08-25 扫码免密自动续签（L3 登录五阶段）发布

本轮把扫码免密自动续签五阶段（`7055de7`）连同审查优化（`330ab1c`）发布到生产：扫码成功落持久浏览器档案 `browser_data/user_<unb>` 并在 DB 标记 L3 记忆（迁移 `2026082502` 为 `cookies` 加 `has_l3_memory/l3_memory_at` 两列）；续签失效时免密快速进入优先于账密路径；`supports_automatic_refresh` 与 `auto_refresh_supported` 接受 L3 记忆为合法条件；失败纳入既有分层错误；CDP 接管仅在配置 `XIANYU_CHROME_CDP_ENDPOINT` 时启用，连不上或身份不符一律失败关闭。审查优化堵住四个缺口：免密续签以进浏览器前 Cookie 为基线，`cookie2` 与 `_m_h5_tk` 均未换新判 `session_not_renewed`（可重试），杜绝断网时旧 Cookie 被当成续签成功；「快速进入」按钮先探测存在性，iframe 加载而按钮缺失判 `fast_entry_unavailable`（单向 manual + 一次性告警），点击超时才可重试；CDP 建档失败不再 `mark_profile_ready` 虚标 L3，`has_l3_memory` 如实回写；浏览器启动失败的 `profile_in_use` 分类收窄到 ProcessSingleton/SingletonLock。

发布替换 8 个后端文件、新增 `utils/xianyu_l3_memory.py` 与 `utils/xianyu_slider_stealth.py`（2026-08-19 的滑块隐身账密自愈 `10fe06a` 随本轮一并上线，结束 Bundle A 的显式排除状态），`global_config.yml` 的 `TOKEN_REFRESH_INTERVAL` 3600→1800；前端静态整代切换，产物由 git worktree 检出 `330ab1c` 干净构建，未混入并行工作树改动。门禁：后端全量 `1016 passed / 201 subtests`（含 5 个新增优化回归）、Ruff 全绿、前端 tsc 零错误、L3 定向三文件 57 passed；迁移在生产数据库在线副本演练 `2026082401→2026082502`，integrity ok、外键违规 0。生产核验：PID `17178`（18:41 kickstart）、单 `8091` listener、本地/公网 readiness `ready`、迁移 `2026082502`、两账号 listener 心跳正常、重启窗口 8 处瞬时 WS 连接 ERROR 自愈、无 Traceback。真人 canary 按用户指令调整为部署后首次真实观察（下次扫码建档 → 约 10 小时后首次免密续签），观察项见 `BLOCKED.md`。证据与可执行回滚位于 `outputs/l3-login-deploy-20260825T183702+0800/`（`rollback/rollback.sh` + `db-backup` + `static-original`）。

同日 19:09 并行会话完成商品管理/知识档案全局 Toast 反馈的纯静态发布（`874a29a`，零停机、不重启、无迁移）：公网入口前移至 `assets/index-DX2cSsFw.js` 与 `assets/index-BsM2xUcO.css`，PID 不变，L3 的 AccountList UI 保留；证据位于 `outputs/knowledge-toast-20260825T190639+0800/`。

## 2026-08-25 Bundle A：AI 订单作用域回复、订单业务分类与告警收窄发布

本轮把维护源积压的后端能力按 7 文件候选（`XianyuAutoAsync.py`、`ai_reply_engine.py`、`db_manager.py`、`reply_server.py`、`schema_migrations.py`、`order_sync_service.py`、`invite_bridge_poller.py`）发布到生产：AI 回复引擎新增订单作用域对话（迁移 `2026081801` 为 `ai_conversations` 补插 `order_id/source/delivery_state` 三列、3 个索引并把存量行标记 `legacy`）、shadow 双跑指标与模型调用预算；订单同步新增 `classify_order_business_type`，自动发货与邀请轮询对非 `ordinary` 订单失败关闭；2026-08-19 的告警策略收窄正式上线（普通客户聊天静默，失败/拦截/人工复核与会话终态告警保留）。

候选从生产原件派生并保留当日上午的生产热修：消息 Token 探测透传 listener `device_id`（根治 `device_id_or_appkey_is_not_equal` 401，热修后零复发）、AI 出站失败日志记录 `OutboundRequestError.code`、仪表盘状态校验统一 `DASHBOARD_ANALYTICS_STATUSES`。滑块隐身账密自愈重登按失败关闭原则显式排除：候选移除 `_recover_via_slider_password_login` 及调用点，`account_session_refresh.py` 保持生产旧语义，`utils/xianyu_slider_stealth.py` 不部署；该特性当时留在维护源等待真机金丝雀（同日晚间的 L3 发布已将其随 `10fe06a` 一并上线，见上一节）。热修已回写维护源（含 `utils/xianyu_session_probe.py` 显式 `device_id` 支持）并补 2 个回归测试。

门禁：维护源全量 `996 passed / 201 subtests`、Ruff 全绿；候选 `py_compile` 通过；隔离树（生产代码 + 候选 7 文件 + 维护源 tests）`985 passed / 11 failed`，11 个失败全部映射到有意排除的滑块特性，零意外失败。生产核验：新 PID `79093`（14:16:27）、单 `8091` listener、本地/公网 readiness `ready`、迁移 `2026081801` 已应用且 health 仍报 MAX 版本 `2026082401`、SQLite `integrity_check=ok`、外键违规 0、存量 37302 行 legacy 回填、新语义对话行已实时写入、4 个账号 listener 重连、`AI_SHADOW_METRIC` 指标流正常、日志无 Traceback。

证据、patch、候选/原件哈希与可执行回滚位于 `/Users/mac/Documents/咸鱼监控台/outputs/bundle-a-20260825T133612+0800/`（`rollback/rollback.sh --check=PASS expected=candidate`；数据库无需回滚，迁移为纯加列，灾备冷备 `data/backups/pre-schema-20260825-141628-815287`）。

## 2026-08-24 自动发货成熟标准版发布

本轮把自动发货收敛为商品配置、资源库、发货记录三页工作台。资源类型是固定资料、一次一密、图片和固定 HTTPS POST 的幂等 API v1；空资源不可创建，商品的 `off/resource/invite` 选择原子互斥，显式资源失效时失败关闭而不回落关键词或其他资源。一次一密沿用 `cards.data_content` 与既有预留，TXT/CSV/逐行补货会对当前库存和全部历史预留去重；API Token 由 `SystemSecretCipher` 加密，公开读取与记录只返回遮罩。

履约载荷先持久化再发送，API 同键最多四次且只重放同一键；未知/冲突结果进入 `manual_review`。发货记录可查看遮罩历史，原样重发复用 committed 原载荷，不扣库存、不调用供应方，并要求确认。绑定或履约历史存在时资源只能停用，不能硬删。迁移为 `2026082401`，新增 API 操作、不可变载荷和重发账本表。

本轮未改 `/Users/mac/Projects/wo-f`、邀请桥合同、付款核验、mid ACK、真实绑定、库存或订单。候选使用生产数据库副本且 8 个账号全部关闭；生产发布只替换四个后端文件和维护源生成的静态资源，静态资源先行、`index.html` 最后，随后一次受控 LaunchAgent reload。

生产证据：PID `13548 → 88845`；本地/公网 `/health/ready` 均为 `ready`、迁移 `2026082401`；`8091` 单 listener；SQLite `integrity_check=ok`、外键违规为零、新表结构完整；启动 listener 数与 reload 前一致，无新 Traceback/CRITICAL/HTTP 5xx，关键表指纹在切换前后保持。具体运行数据只保留在本地发布证据中。随后静态-only 触控命中区修正未 reload 后端；最终公网入口为 `assets/index-DgjfWuPd.js` 与 `assets/index-CrHg7Yfi.css`，与本地 SHA-256 一致。

完整命令、红→绿、候选/公网截图、最终原件冷备、patch、验证记录和回滚演练位于 `/Users/mac/Documents/咸鱼监控台/outputs/delivery-center-20260824T192237+0800/release-candidate-v4/`；可执行回滚为该目录的 `rollback.sh`。工作树既有未提交资产保持原样，未 commit/push/reset/clean。

## 2026-08-23 商品同步 listener 注册隔离发布

根因是 `XianyuLive.__init__` 曾无条件写入 `_instances[cookie_id]`：全量和分页商品同步创建的临时 HTTP 实例会覆盖 `cookie_manager.py` 创建的长期 WebSocket listener，随后邀请桥可能得到一个没有在线 WebSocket 的实例。构造器现保留 `register_instance=True` 默认值供长期 listener 使用；`POST /items/get-all-from-account` 与 `POST /items/get-by-page` 都显式传 `register_instance=False`，临时实例关闭自己的 HTTP session 而不改变注册表。

回归覆盖两个同步入口和“临时实例不能替换既有 listener”，定向结果为 `84 passed / 4 subtests`，当时完整后端为 `969 passed / 197 subtests`。生产候选从生产原件制作，只原子替换 `XianyuAutoAsync.py` 与 `reply_server.py` 并受控 reload 一次；本地/公网 readiness、单 `8091` listener、SQLite integrity、日志窗口和隔离回滚均通过。

后续 2026-08-24 Delivery Center 发布继续保留这一护栏。2026-08-25 的只读复核确认当前 PID `88845` 运行的生产源码仍有条件注册和两处 `False`，两份后端与最终 Delivery Center 候选逐字节一致；本地/公网均为 `ready`、迁移 `2026082401`、SQLite `integrity_check=ok`，启动后日志窗口没有 `listener_unavailable` 或 `Traceback`。原始证据位于 `outputs/listener-registry-fix-20260823T210631+0800/`，回滚单元位于 `/Users/mac/Library/Application Support/XianyuManager Rollbacks/listener-registry-fix-20260823T210631+0800/`。

## 2026-08-22 商品知识档案覆盖语义发布

AI 结构化草稿生成现在以本次概览、商品标题、价格和详情重建完整草稿；成功后整体替换旧 `draft_json`，失败时保留原草稿。档案复制固定覆盖目标草稿，不自动发布，也不改变目标 `published_json`、历史版本或独立训练规则。

生产候选定向回归 `25 passed`，后端全量 `967 passed / 197 subtests`，前端定向 `3 passed`；生产发布经后端先换位、受控 reload/readiness、再切换静态入口完成。最终证据为本地/公网 `ready`、迁移 `2026081601`、单 `8091` listener、18/18 稳定探针和新旧入口资源哈希一致；回滚脚本在隔离 fixture 中实跑恢复五个入口文件。

证据、补丁和回滚分别位于 `/Users/mac/Documents/咸鱼监控台/outputs/item-knowledge-overwrite-20260822T110235/deploy-live-20260822T121906+0800/verification.md`、`/Users/mac/Documents/咸鱼监控台/outputs/item-knowledge-overwrite-20260822T110235/patch.diff` 和 `/Users/mac/Library/Application Support/XianyuManager Rollbacks/item-knowledge-overwrite-20260822T121906+0800/rollback.sh`。后续 2026-08-24 交付中心发布已重载相关后端，但当前生产源码仍保留上述生成/复制语义。

## 2026-08-19 普通账号待发货回退与过期会话隔离发布

普通账号订单列表明确返回 `platform_permission_denied` 时，订单同步会进入受约束的 `pending_only` 回退，并逐项校验订单、商品、买家、金额、数量和待发货状态；`lead`、`unknown` 或任一关键字段不一致仍失败关闭。会话已进入人工重登状态时，订单同步和邀请轮询返回或记录 `skipped_reauth`，不继续触发平台请求。

该两文件候选已原子部署；生产 `order_sync_service.py` 与 `invite_bridge_poller.py` 的落盘时间早于当前 PID `88845` 的启动时间，当前进程已经加载。验证记录位于 `outputs/autodelivery-fallback-atomic-20260818T123246+0800/evidence/verification-record.md`。代码发布不替代逐账号线上观察和一笔低金额真实订单金丝雀，这两项继续列为外部门禁。

## Historical Seller Auto-Rate Backfill On 2026-08-18 (Read-Only Gate)

The production candidate added only `db_manager.py`'s opt-in `allow_historical=False` guard and the standalone `backfill_auto_rates.py` operator tool. The default command opens SQLite with `mode=ro`, scans each enabled account through the existing order-list client for 365 days (up to 100 pages/2000 orders), counts exact `RATE` actions, and never imports the rating submitter or writes a task. Normal scheduler callers retain the enable-time boundary.

The latest production read-only scan completed full, non-truncated coverage for two enabled accounts while one returned `session_expired`, so the aggregate remained `incomplete=true`, `applied=0`, and `apply_allowed=false`. No historical task was created and no platform review request was sent; the already-running scheduler's later ordinary work is independent of this read-only scan. Exact account and task counts remain only in the local evidence.

Deployment used atomic per-file replacement without LaunchAgent reload. PID `42728`, one `8091` listener, local/public readiness `ready`, migration `2026081601`, and public entry `assets/index-BPyBD1ot.js` were unchanged by this rollout. The observed later PID/static/order-file drift was external to the two-file change and was preserved. Full commands, literal output, hashes, and rollback are in `/Users/mac/Documents/咸鱼监控台/outputs/auto-rate-history-20260818T000412+0800/`; production originals are in `/Users/mac/Library/Application Support/XianyuManager Rollbacks/auto-rate-history-20260818T000412+0800/`.

The apply phase remains blocked until the expired account is restored and a fresh default scan reports complete coverage for all three accounts. After an explicit confirmation based on that report, `--apply` may schedule only currently rateable, untracked orders with the existing 5-15 minute delay; it never submits reviews directly. Buyer-to-seller rating and any successful or ambiguous platform review reversal remain outside this rollout.

## Invite Confirmation Latency Repair On 2026-08-17

The installed production now handles a verified paid invitation order without waiting behind account-wide order synchronization or the invitation poller's global discovery scan. Numeric platform order IDs try the single-order detail endpoint first and fall back to at most five order-list pages. After that payment check succeeds, the event invokes a target-only `scan_once(discover=False, trusted_order_ids={order_id})`; the internal send-message route still performs its independent payment verification and keeps the existing idempotency and message-ACK gate.

The pre-deploy same-account observation took `55.094s` from the payment-status event to confirmation-message ACK. After the controlled reload, the same boundary took `2.926s`. One complete production ledger recorded a single-attempt `send_confirmation` in `0.859s`, buyer confirmation, a distinct single-attempt `send_fulfillment_message` in `2.571s`, and single-attempt `mark_fulfilled` in `3.467s`, ending in local `fulfilled`. No target confirmation/fulfillment operation created after the deployment remained pending, ambiguous, failed, or in review at closeout.

Only `/Users/mac/Library/Application Support/XianyuManager/XianyuAutoAsync.py` and `invite_bridge_poller.py` were replaced, followed by one controlled LaunchAgent reload. Local and public readiness stayed `ready`, migration remained `2026081601`, and one process listened on `8091`; database content, static assets, configuration, keys, browser profiles, uploads, and the separate invitation-service deployment were preserved. The exact commands and literal outputs are in `outputs/invite-confirm-latency-20260817T203138+0800/verification-record.md`; the runnable rollback is `/Users/mac/Library/Application Support/XianyuManager Rollbacks/invite-confirm-latency-20260817T203138+0800/rollback.sh`.

## 2026-08-18 仪表盘实时发布

生产仪表盘现在区分单日时段视图和多日日视图。`GET /api/dashboard/summary` 对单日返回 `trend_granularity=hour` 与保存的东八区下单时间桶，对多日返回 `day` 与日桶。界面补齐缺失小时，在可见且在线时每 15 秒刷新；后台请求失败保留最后一次成功状态；数字用 `NumberFlow` 动画并遵守 reduced-motion 偏好。

付款事件会在普通履约路径继续前持久化可信金额、数量和下单时间。仪表盘净销售额保留 `refunding`，排除 `refunded`/`cancelled`，重新计入 `refund_cancelled`；订单明细在计数、金额或状态分布变化时刷新。生产候选从维护源前端和三个限定后端副本按文件发布；数据库、迁移、配置、密钥、浏览器 Profile、上传和无关脏改动均保留。

新鲜证据记录本地/公网 readiness 为 `ready`、迁移 `2026081601`、单个 `8091` listener、`today -> hour`、`7days -> day`、后端仪表盘回归 `147 passed + 25 subtests`、前端 `29` 个文件 `183` 个测试。验证记录、候选哈希、桌面/移动截图、补丁和可执行回滚均位于 `/Users/mac/Documents/咸鱼监控台/outputs/dashboard-realtime-20260818T000823+0800/`。

## 2026-08-18 今日经营脉搏 UI 重排

仪表盘默认范围改为“今天”，范围控件统一为今天、昨天、近 3 天、近 7 天、近 30 天和自定义，并提供 `aria-pressed` 与 44px 触控高度。今日图只展示东八区已发生到当前小时的数据，峰谷和涨跌排除未结束小时；跨日范围结束于今天时也排除未结束日期，数据不足显示 `--`。桌面保持 KPI/趋势左右结构，移动端重排后在 390x844 首屏露出浅色分析区。

动效沿用已有 NumberFlow 与 Recharts 650ms 过渡，数据指纹不变时不重播；`prefers-reduced-motion` 下关闭页面淡入、旋转、脉冲和图表过渡。前端全量为 `29` 个测试文件、`188 passed`，TypeScript、构建、静态代际、扩展校验和四个视口的 ego-browser 验收通过。

本次只把维护源生成的静态候选按资源优先、`index.html` 最后顺序发布，没有重载后端。生产 PID `51419`、单个 `8091` listener、readiness `ready`、迁移 `2026081601` 保持；本地和公网 HTML 均引用 `assets/index-D8NYx3ge.js` 与 `assets/index-DlLMEdhT.css`。候选、前端补丁、四视口截图、验证记录和可执行回滚位于 `/Users/mac/Documents/咸鱼监控台/outputs/dashboard-ui-20260818T132445+0800/`。

## Seller Auto-Rate And Frontend Source Correction On 2026-08-16

The installed production now exposes product-bound Auto Delivery and an account-level, default-off seller auto-rate switch. Auto-rating covers seller-to-buyer reviews only: it discovers orders created after enablement with the exact platform `RATE` action, delays each task by 5-15 minutes, generates one fact-conservative Chinese sentence with a fixed fallback, and submits serially. The merchant request uses `tradeIdList`; only `data.module.success` plus the target order in `successOrderIds` is accepted as success. Explicit rejection is `failed`; an inconclusive result or restart after the durable pre-POST marker is `needs_reconcile` and is not automatically replayed. Buyer-to-seller rating remains outside the active contract pending a real write-and-readback canary.

The frontend release identity is Auto Delivery present, Skill Center absent, and native-helper code plus user-facing “本机助手” wording absent. The maintained frontend under `/Users/mac/Documents/咸鱼监控台/frontend` is the only build source. The installed production checkout may contain task-specific changes and must not replace the maintained source or be copied wholesale back into it.

Final production evidence recorded `ready`, migration `2026081601`, three of three runtime sessions, one `8091` listener, and the same `index-DN0GYD_l.js` entry from local and public HTML. All account auto-rate switches and task counts were zero, so the deployment submitted no real review. The four invitation/fulfillment path files stayed byte-identical to the pre-deploy snapshot; the combined auto-rate and invitation/fulfillment regression passed 79 tests, and the production auto-rate module passed seven focused tests. One controlled backend reload loaded the corrected merchant schema; static publication itself stayed online.

The verified deployment diff, exact commands, literal outputs, manifest, reverse frontend patch, and runnable rollback are in `/Users/mac/Documents/咸鱼监控台/outputs/auto-rate-deploy-20260816/`. The full pre-deploy rollback snapshot remains at `/Users/mac/Library/Application Support/XianyuManager Rollbacks/auto-rate-20260816-165736-pre-deploy`.

## v1.10.1 Login Verification Hotfix Release On 2026-07-29

This hotfix keeps QR, SMS and password login in the same server-Chrome session
through slider, face, SMS and unknown interactive verification. Page or tab
replacement no longer ends monitoring. Browser shutdown still follows real
message-Token validation, identity matching and account persistence; a scanned
QR, page text, an ordinary Cookie or one Page closing is not success.

The web console now exposes owner-scoped, no-store browser frames plus bounded
gesture, wheel, text and safe-key actions. Remote ordinary users may start an
offscreen SMS login and complete it in this surface. Displaying the physical
server window remains restricted to an administrator on the loopback console.
Mobile-scan verification remains a scannable image. The Chrome extension is an
explicit advanced import option rather than the automatic risk-control path.

Local release gates completed on the candidate source: Ruff, the explicit
Python compilation list, Gitleaks preparation, the OpenAPI snapshot and
`git diff --check` passed. The complete backend suite passed 704 tests in
153.939 seconds. The frontend passed 24 files with 147 tests, TypeScript, npm
audit with zero vulnerabilities and two byte-identical production builds; the
extension passed 6 tests. The OpenAPI contract adds two owner-scoped interaction
methods and now contains 229 methods. No schema migration or dependency change
is part of this hotfix.

An isolated single-worker candidate on `127.0.0.1:8092` was exercised as an
ordinary web-console user at desktop and mobile widths. It opened the real
public Goofish SMS-login page in the authenticated live interaction surface and
exposed bounded text, pointer and safe-key controls. Cancellation returned in
0.330 seconds; a fresh process inspection found no candidate Chrome/Profile
residue and the candidate log contained no error or traceback. This rehearsal
also found and fixed a cross-thread Playwright cancellation path before the
complete backend suite was rerun.

No registered Goofish account credentials, SMS code, slider or face challenge
were submitted during this isolated rehearsal. Consequently, real account Token
validation, identity matching and persistence through a naturally triggered
platform risk-control chain remain an explicit post-release human canary, not a
claimed result.

Production release evidence:

- Application commit `8d6e78c66bfec3a631a99fbe02b376f6a8634d3d`
  was pushed directly to `main`. GitHub Actions run `30447116945` passed
  `secrets` in 9 seconds and the complete `test` job in 4 minutes 15 seconds.
- The mode-`0700` rollback unit
  `/Users/mac/Library/Application Support/XianyuManager Rollbacks/v1.10.1-pre-deploy-20260729-192456`
  retained the previous `v1.10.0` source, SQLite backup, local keys, browser
  Profile, static assets, uploads, extension, LaunchAgent and Git bundle. Its
  SQLite integrity check and all 2,338 SHA-256 entries passed after the service
  stopped. The path no longer existed during the 2026-08-25 audit, so those
  retained contents are not currently re-verifiable.
- Production fast-forwarded to the exact application commit and restarted as
  one worker with PID `40759`. Local and public readiness passed, migration
  `2026072703` remained unchanged, SQLite integrity was `ok`, and the runtime
  session count remained zero.
- Static publication retained two complete generations of 36 assets with zero
  orphan files. The new entry SHA-256 is
  `c78c80574ecf365e9c64cf8b8e0eb8f761586377da59afd3c36e1b98b09a947c`;
  the extension ZIP SHA-256 is
  `e344821ef36e4aecc7da6e0f9a8c0ce22ee31ed0fe934b30ff637ec96cd450ef`.
  All 76 checked files matched byte-for-byte on disk, localhost and the public
  HTTPS origin.
- A 67-second zero-retry window passed 10 local and 10 public readiness samples
  with a stable PID. One earlier public request exceeded an 8-second client
  timeout; five immediate probes and the complete replacement window all
  returned HTTP 200, with the slowest replacement request taking 3.195 seconds.
- An existing authenticated console session was used read-only to confirm that
  the public account modal advertises same-page QR verification and keeps the
  extension under advanced methods. No login session was started and no account
  state was changed. New production log output contained no traceback, error
  level, HTTP 5xx or detected sensitive value.

The remaining human canary is deliberately unchanged: a registered user must
complete a naturally triggered slider, face or SMS verification and confirm
that real Token validation, identity matching and account persistence occur
before the browser closes. This release does not claim that external platform
canary.

## v1.10.0 Production Release On 2026-07-29

PR `#45` squash-merged the order-sync, analytics and security hardening release
as `7a4f4726b3facea7a9e0a50e785a5275c97b4799`. Production runs that exact
application commit and annotated tag `v1.10.0` points to it. The production
schema is `2026072703`.

Release contract:

- The latest schema is `2026072703`: item metric rows and canary state are bound
  to their account owner, while `fulfillment_attempts` and
  `fulfillment_card_reservations` persist `prepared`, `sending`, `committed`,
  `released`, and `manual_review` outcomes across restarts. Possible external
  side effects after `sending` never permit automatic inventory release.
- Seller metric collection is default-off. A real adapter must be registered
  and each account must independently pass three live canaries before the
  four-hour serial scheduler starts. Synthetic tests do not verify a seller
  backend response path.
- A canary advances only when its batch inserts a new observation newer than
  the account's previous canary observation. Duplicate, reset or out-of-order
  snapshots cannot enable collection.
- Traffic deltas are assigned to the complete interval between consecutive
  snapshots. The API and dashboard expose approximate observation windows and
  duration metadata; they do not turn a four-hour sample into one-hour traffic.
- Order timing uses the saved platform order-time snapshot and its source. It is
  not described as a guaranteed payment, settlement, shipment, or completion
  timestamp.
- The recovered 20-item pre-release security ledger is closed in
  `docs/security-v1.10-closeout.md`; no replacement full scan was launched.

Local release gates completed on 2026-07-29:

- Ruff, the explicit Python compilation list, Gitleaks over the complete
  `origin/main...HEAD` delta, the OpenAPI snapshot and `git diff --check`
  passed. The complete backend suite passed 689 tests in 104.419 seconds with
  an integrity-checked isolated SQLite database.
- The frontend passed 23 files with 145 tests, TypeScript, npm audit with zero
  vulnerabilities and two production builds. Build verification retained 36
  assets with zero orphans and a 71.6% entry-size reduction. The locked Python
  dependency audit reported no vulnerability.
- The repository-outside candidate at
  `/Users/mac/Library/Application Support/XianyuManager Candidates/v1.10.0-20260729-135100`
  used an integrity-checked production database copy and copied all three
  local keys into a mode-`0700` directory. After migration, every candidate
  account was explicitly disabled before the acceptance start; no production
  account state was modified. The candidate path no longer existed during the
  2026-08-25 audit, so it remains historical evidence only.
- The acceptance start used one Uvicorn process on `127.0.0.1:8092`, migration
  `2026072703`, zero account listener tasks and zero runtime sessions. Local
  live/readiness, SQLite integrity, version `1.10.0`, root HTML, authenticated
  empty states and all four unauthenticated order/metric probes passed. The
  unauthenticated probes returned 401 and the metric adapter remained
  unavailable. The candidate was stopped and port 8092 was verified free.
- Full-page 1440-pixel desktop and 390-pixel mobile captures of the dashboard
  and order center are retained in the candidate `evidence` directory. The
  automated browser recorded no page error, console error or failed request.

The first candidate rehearsal was stopped immediately when two legacy accounts
without matching post-migration status rows defaulted to enabled. The candidate
copy was normalized only after migration and the acceptance restart then had
zero listeners. This is why candidate rehearsals must migrate first and disable
every copied account by joining the final `cookies` table, rather than assuming
that historical `cookie_status` rows are complete.

Production release evidence:

- PR run `30427347536` passed both `secrets` and `test` on the final branch
  head. The exact squash commit then passed both jobs again on main in run
  `30427623552` before deployment.
- The stopped-service, mode-`0700` rollback unit was
  `/Users/mac/Library/Application Support/XianyuManager Rollbacks/v1.10.0-pre-deploy-20260729-142216`.
  Its 2,323-entry SHA-256 manifest, Git bundle, SQLite integrity check, three
  local keys, prior tracked source, static and upload files, browser profiles,
  browser extension and LaunchAgent all verified before the production
  checkout moved. The path no longer existed during the 2026-08-25 audit, so
  its contents are not currently re-verifiable.
- The clean production checkout fast-forwarded from `8e9f056` to `7a4f472`.
  The production frontend build reported version `1.10.0`, retained 36 and 35
  assets across two generations with zero orphans, and npm reported zero known
  vulnerabilities.
- Launchd restored one listener with PID `19929`. Local and public live and
  readiness passed, the OpenAPI document reported version `1.10.0` with 227
  methods, SQLite integrity remained `ok`, and 13 local/public readiness pairs
  passed across the post-deploy observation window on migration `2026072703`.
- The two entry assets and HTML matched disk, local HTTP and public HTTPS
  byte-for-byte. Ten critical legacy table counts, all three keys, uploads and
  browser profiles had zero deployment drift. Unauthenticated order, refresh,
  item-performance, item-traffic, metric-status and metric-sync probes failed
  closed with HTTP 401 locally and publicly.
- The post-start slice covered 20 log files and 73,329 new bytes with zero
  Traceback, HTTP 5xx, error/critical entries, raw authorization or Cookie
  material, passwords, or verification URLs.

Real platform order-detail and seller-metric canaries remain external gates.
The unverified detail path and metric adapter therefore stay closed; synthetic
and production infrastructure checks are not recorded as live seller-backend
verification.

## v1.9.1 Login Risk-Control Relay Production Release On 2026-07-29

The login risk-control relay was deployed production-first from commit
`8e9f056d6c8937f4a8a97d80b93b2b84b8dc6a1d`. It keeps interactive slider,
face, and unknown verification inside a user-controlled Chrome window, retains
mobile QR verification as an image flow, and does not report success until a
real platform Token has been validated and the account identity has been
persisted. The browser-extension import protocol is version 2, owner-bound,
five-minute, and single-use; protocol version 1 remains loopback-only.

Deployment evidence:

- The release gate passed Ruff, Python compilation, 484 backend tests, 136
  frontend tests, 34 focused AccountList tests, 6 extension tests, TypeScript,
  npm audit with zero vulnerabilities, the 231-method OpenAPI snapshot,
  Gitleaks with zero findings, and `git diff --check`.
- Two isolated frontend and extension builds were byte-identical. Production
  serves entry `assets/index-D6rsP8-n.js`; the disk, local, and public HTML
  SHA-256 is `531b0f5f...`, the entry SHA-256 is `b0bf7c82...`, and the extension
  ZIP SHA-256 is `e344821e...`. Both retained asset generations contain 35
  references, with 69 unique files, zero missing files, and zero orphaned
  files; all 69 assets matched disk, local HTTP, and public HTTPS byte-for-byte.
- The mode-`0700` rollback unit is
  `v1.9.1-pre-deploy-20260729-001005` outside the repository. Its final
  2,129-entry SHA-256 manifest covers the integrity-checked SQLite backup,
  three local keys, browser profiles, uploads, prior source/static/extension,
  LaunchAgent, and verified old and candidate Git bundles.
- The production checkout fast-forwarded from `6975deb` to the release commit
  through the verified local bundle. The LaunchAgent restarted as the only
  worker with PID `67698`; local and public readiness each passed all 13 samples
  across 62 seconds with migration `2026072609`, zero active runtime sessions,
  and SQLite integrity `ok`. The deploy log slice contained no application
  error, traceback, HTTP 5xx, Cookie value, authorization value, pairing Token,
  verification URL, or password value.
- The live OpenAPI document reports version `1.9.1`, 231 methods, and
  `POST /qr-login/cancel/{session_id}`. Unauthenticated local and public cancel
  probes both returned 401. The current page, both asset generations, and the
  extension ZIP were verified through the public host after restart.

The operator explicitly waived the registered ordinary-user live canary before
GitHub publication. A roughly nine-minute observation window recorded zero
browser-extension pairing or import requests and no ordinary-user account
transition to `chrome_extension`; therefore real slider/face/mobile-scan relay,
live single-use Token replay, modal-hide polling, and the ordinary-user public
server-Chrome denial remain unobserved in production. Those behaviors are
covered by the release tests, including the expected `pairing_already_used`
replay failure, but test coverage is not recorded as live-account evidence.

## v1.9.0 Production Release On 2026-07-27

The operations cockpit and dashboard business-insights work was deployed from
commit `6975deb352de5b5be060b3e3f599885fd97a79a2`. It adds hourly order-time and
buyer-behavior analysis (behavioral and quantifiable only, no customer
profiling), order status and regional distribution charts, a product
hot-sellers board with period-over-period growth detection, and inline account
identification plus a settled-date range filter on the order list. Migration
`2026072609` repairs legacy order-snapshot source markers with a defensive,
DDL-free update.

Deployment evidence:

- Production `origin/main` fast-forwarded from `2e9b950` (`v1.8.3`) to
  `6975deb` via `git merge --ff-only`; PR #41 (order-data-completeness) and
  PR #42 (dashboard-business-insights) had already merged to `origin/main`
  through green CI (`secrets` + `test`). An annotated tag `v1.9.0` was created
  on the exact production SHA `6975deb`.
- Migration `2026072609` was rehearsed on a read-only copy of the production
  database before deployment: all three defensive `UPDATE` conditions matched
  zero rows and the content fingerprint was unchanged, confirming an idempotent
  no-op. On the live restart it applied cleanly; `/health/ready` reports
  migration `2026072609` and database integrity `ok`, and the startup created
  an automatic pre-schema backup.
- The frontend was rebuilt from the production worktree at `6975deb`. Local and
  public HTML both reference entry assets `index-BoejLjfT.js` /
  `index-BZ0fOHdb.css`; the entry JS served over the public host is
  byte-identical (SHA-256 `ebb2dee5…`) to the local file, and the build carried
  zero orphaned assets across two retained generations. The production OpenAPI
  method count is 230, matching the repository snapshot.
- The four new analytics endpoints (`/analytics/traffic`, `/analytics/buyers`,
  `/analytics/orders`, `/analytics/orders/valid`) all return `401` without
  authentication, confirming the fail-closed tenant guard. The single launchd
  worker restarted with a new PID and stayed stable across a 60-second,
  10-sample readiness observation with zero anomalies; the last 200 process log
  lines contained no error or traceback entries. The service interruption was
  limited to the restart itself.
- The pre-deploy rollback unit is the mode-`0700` snapshot
  `predeploy-dashboard-insights-20260727-135601` outside the repository under
  `/Users/mac/Library/Application Support/XianyuManager Rollbacks/`. It contains
  an integrity-checked SQLite backup, all three local keys, the prior static
  assets, browser profiles, the LaunchAgent, a source archive, and a verified
  Git bundle, with a 1,969-entry SHA-256 manifest.
- A shorter 60-second observation replaced the customary 15-minute/31-sample
  window; extend the window if a longer soak is required. Real end-user order
  and per-tenant acceptance on the live host remains for the operator to
  confirm through the authenticated UI.

## v1.8.3 Production Release On 2026-07-26

The Skill Center closeout was deployed from commit
`45dd8f1e2438f039d38ebfd0c025b467cb504e31`, authored on 2026-07-26. It rebuilds
the capability area as a single-row horizontal status track, merges the
standalone AI expert entry into per-account reply-strategy settings, demotes
runtime diagnostics to a bottom section, adds `runtime_mode` and
`operation_gates` to the capability API with a collection-level
`PUT /api/ai/reply-strategies`, restricts skill runtime evidence to persisted
playwright/mtop records, fixes the architecture method count (227) and OpenAPI
snapshot, and upgrades PostCSS. No schema migration was added; the production
migration remains `2026072301`.

Ledger reconciliation:

- This release ran in production before its ledger existed: the commit was
  deployed to the production worktree ahead of any push to `origin/main`, tag,
  CHANGELOG entry, or handoff record. On 2026-07-26 the ledger was reconciled:
  `origin/main` fast-forwarded from `b310a76` to `45dd8f1`, annotated tag
  `v1.8.3` was created on the exact production SHA, and this docs-only closeout
  records the drift as it happened. The `main` push CI run `30192564473` passed
  both `secrets` and `test` jobs.
- Deploy-time evidence (a dedicated pre-deploy snapshot name, the 15-minute
  observation window, and the byte-offset log scan) was not recorded when the
  release went live and is not reconstructed here. Live verification on
  2026-07-26 confirmed the production worktree at `45dd8f1`, local
  `/health/ready` reporting `ready` with migration `2026072301`, and production
  static serving entry assets `index-26oHvBgF.js` / `index-DvxrGUwO.css`.
- The Verification Baseline compilation list below and the operator runbook
  were synchronized with `.github/workflows/ci.yml`, which had gained
  `browser_extension_pairing.py`, two skill-monitor modules, and the two QR
  utility modules since the lists were last updated.

## v1.8.2 Production Release On 2026-07-24

The dual-QR login release was merged through PR `#35` at
`a860834a8d73694b7f6c383b7e6b27f96e3c9abb`. Live QR acceptance then exposed
one raw stable account identifier in an early database persistence log; PR
`#36` moved that redaction to the log source. The final application commit is
`29f523659c091526bd393bc3b797dce7fb4570ea`. The release-evidence merge is
`39eac9fbd129b700f0673cabd63cbc2850994070`; production fast-forwarded to it
without restarting the loaded application, and annotated tag `v1.8.2` resolves
to that merge. Any later docs-only closeout commit leaves the loaded application
payload and static assets byte-equivalent to `29f5236`.

GitHub and clean-worktree gates:

- PR `#35` CI run `30096458035` and its `main` push run `30096628378` passed
  both `secrets` and `test`. PR `#36` CI run `30099092672` and final `main`
  push run `30099265493` passed the same jobs. Both PRs used ordinary merge
  commits. No historical `v1.8.1` tag was created.
- The feature candidate passed Ruff, the explicit Python compilation list, 317
  backend tests with isolated SQLite and keys, TypeScript, 18 frontend files
  with 92 tests, 27 targeted account-panel tests, 6 extension tests, and
  `npm audit --audit-level=high` with zero vulnerabilities. The final log-source
  correction used a verified red-to-green regression and passed the same Ruff
  and compilation gates plus 318 backend tests in 25.021 seconds.
- Two builds from exact feature merge `a860834` were reproducible. The entry
  assets are `index-izG0227c.js` (245,344 bytes, SHA-256
  `1553754b77ba837f69e3dce3bd2263ee92d76d05fc89dbef8adbbb18ef1664dc`)
  and `index-Do2LTy2E.css` (76,129 bytes, SHA-256
  `3c2966b0f0b8dfa6d494834194cc053727f905bef621a8b8d6d98227edc0a85c`).
  Build verification retained two 32-asset generations with zero orphans and
  a 71.7% entry reduction. Extension verification passed; its ZIP SHA-256 is
  `373a1b9ebb424ce3276b71a3818def994d04c367066e93947c2e161ebf32f106`.

Production deployment and runtime evidence:

- Production is `/Users/mac/Library/Application Support/XianyuManager`, served
  by `com.cxywjx.xianyu-manager` with `WEB_CONCURRENCY=1`. It was advanced only
  with `git merge --ff-only`. Final process `14740` became ready in four seconds
  from commit `29f5236`; the migration remained `2026072301`.
- The mode-`0700` pre-deploy snapshot is outside the repository at
  `/Users/mac/Library/Application Support/XianyuManager Rollbacks/` under
  `v1.8.2-pre-deploy-20260724-212959`. Its 2,328-file SHA-256 manifest verifies
  an integrity-checked SQLite backup, all three local keys, the prior static
  site, 49 uploads, four stopped-service browser-profile directories, the
  LaunchAgent, source archive, verified Git bundle, and the prior atomic static
  directory. A second stopped-service post-QR/pre-final snapshot named
  `v1.8.2-post-qr-pre-final-20260724-220658` verifies 1,841 files and preserves
  the newly accepted login state before the final log-only restart.
- Local and public live/ready responses passed with identical migration and
  runtime state. Local and public `/` and `/login` HTML were byte-identical and
  referenced the new entry assets. Every current-generation asset (32/32) and
  the extension ZIP matched disk, local HTTP, and public HTTP byte-for-byte.
  OpenAPI contained 186 paths and 224 methods; all 12 QR, official-session,
  password, and compatibility login routes matched their expected methods. No
  public route or schema migration was added.
- SQLite integrity remained `ok`. Before the user-operated local-Chrome QR
  acceptance, account/user/item/order counts and all three local keys plus 49
  uploads matched the pre-deploy snapshot. The live QR created one new account
  row, produced no duplicate stable identity, promoted one canonical persistent
  profile, left no temporary or backup profile directory, installed one
  listener, reached a connected WebSocket, and closed its Chrome process.
- Browser acceptance covered 1440x900 and 390x844 production viewports plus
  isolated 360/390/430-pixel mobile checks. The account modal opened without a
  request, both QR entries remained visible with at least 44-pixel controls,
  the modal's full scroll range was reachable, and document width never exceeded
  viewport width. Web QR completed a real generation and repeated polling
  without Chrome; closing the modal stopped polling and left TTL cleanup to the
  service. Local Chrome QR completed with user scanning, real platform identity
  resolution, one persistence/listener handoff, and automatic window cleanup.
- A pre-final 31-sample observation over 15 minutes had zero failures: local and
  public readiness stayed healthy, PID and one-listener count were stable,
  migration stayed `2026072301`, and no application Chrome process reappeared.
  The final log-redacted process completed the same 31-sample/15-minute gate
  with zero health-sample failures. Its fresh log contained two error-level
  heartbeat-send timeouts during one WebSocket keepalive interruption. The
  listener then exited the stale socket, entered its normal reconnect path,
  re-established the connection in about 12 seconds, completed Token probing
  and registration, and resumed successful heartbeat responses. The same log
  had no traceback, raw account create/update identity, long numeric identity
  candidate, Cookie, Token, password, bearer value, QR content, or verification
  URL.

Acceptance boundary: unit/UI tests, isolated SQLite, CI, and candidate builds
are automated or mocked evidence. Web QR generation/polling, the local Chrome
QR scan, real identity/Profile persistence, WebSocket listener handoff, public
asset equality, and both production observation windows are live evidence. No
real password, Cookie, Token, account identifier, key, verification URL, database
content, or browser profile is recorded in this document.

## v1.8.0 Production Release On 2026-07-23

The audited application payload was merged through PRs `#30` and `#31` and
deployed from commit `f6fb00ecab788946627588e82e2c9a315d173560`. The final
docs-only release merge does not change application, frontend, extension,
migration, or dependency content. The authoritative final release SHA is the
commit resolved by both `origin/main` and the annotated `v1.8.0^{}` tag; this
document is part of that tagged commit.

GitHub and clean-worktree gates:

- PR `#30` merged the production synchronization through ordinary merge commit
  `dd5807c3d8078f4f04934376f0d10ddfe1a79d03`. Its PR CI run
  `30017648878` passed.
- PR `#31` fixed the `RemoteImage` source-change race and merged through
  ordinary merge commit `f6fb00ecab788946627588e82e2c9a315d173560`. Its PR CI
  run `30021408943` and subsequent `main` push CI run `30021629945` both
  passed all `secrets` and `test` jobs.
- The final code gates passed Ruff, the explicit Python compilation list, 302
  backend tests with an isolated `DB_PATH`, TypeScript, 18 frontend files with
  87 tests, 6 extension tests, and two production builds.
- `npm audit --audit-level=high` reported zero vulnerabilities. Build and
  extension verification passed; the build retained two generations of 32
  assets with zero orphans. OpenAPI contained 186 paths and 224 methods, all
  required login/session routes were present, and all three retired QR
  refresh/cooldown routes were absent.

Production deployment and rollback evidence:

- The stopped-service rollback snapshot is
  `backups/v1.8.0-final-pre-deploy-20260723-235008`. It contains 2,191
  hash-listed files plus the manifest, an integrity-checked SQLite backup, all
  three local encryption keys, four complete browser profiles, 49 uploads, the
  prior static site, the LaunchAgent plist, a source archive, and a verified Git
  bundle. The earlier alignment snapshot
  `backups/v1.8.0-pre-align-20260723-230353` and local rollback branch
  `codex/prod-pre-align-20260723` at
  `e76c24a74f40cfa51c8151d84f30265d305fb2e9` are retained.
- Production was advanced with `git merge --ff-only`; no reset or working-tree
  overwrite was used. The formal virtual environment then passed the same 302
  backend tests against an isolated database. The formal frontend passed 87
  tests, 6 extension tests, zero-high-severity audit, TypeScript, two builds,
  build verification, and extension verification.
- The LaunchAgent became ready in three seconds with working directory
  `/Users/mac/Library/Application Support/XianyuManager`,
  `WEB_CONCURRENCY=1`, one process listening on `8091`, and no listeners on
  `8092` or `8093`. Local and public readiness reported migration
  `2026072301`.
- Local and public `/`, `/login`, `/register`, `/forgot-password`, `/terms`,
  and `/privacy` returned 200 and matched the deployed HTML byte-for-byte. The
  referenced `index-pN18c_Tf.js`, `index-kJg_YYPB.css`, and extension ZIP all
  returned 200 locally and publicly and matched disk. The extension ZIP SHA-256
  is `373a1b9ebb424ce3276b71a3818def994d04c367066e93947c2e161ebf32f106`.
- Database integrity remained `ok`. Account/key/profile/upload counts remained
  `1/3/4/49`; item, order, knowledge-profile, AI-profile, monitor-task, and user
  counts remained `106/63/12/2/2/2`. The 49 upload files matched the rollback
  snapshot exactly, with aggregate content SHA-256
  `887cc8b078dfa1ee3860af1790401663008470d108e294789ad8057bd4988f63`.
- A byte-offset scan covered 128 new lines across the three production logs.
  Cookie assignments, raw `unb`, Tokens, bearer values, passwords, session
  secrets, QR content, verification URLs, known stored sensitive values,
  tracebacks, and error-level records each had zero matches. No real secret or
  account material was printed during acceptance.

## v1.8.0 Sync Candidate On 2026-07-23

The `codex/sync-production-20260723` branch starts from GitHub `main` at
`ecde9cf1a72d1d63c1971ad59be7357c7a30b22c` and imports the audited production
source snapshot without runtime data. The local-only snapshot branch and its
SHA-256 manifest exclude SQLite, all three local keys, Cookies, Tokens, `.env`,
logs, browser profiles, uploads, backups, generated static assets, archives,
PIDs, virtual environments, and agent metadata. GitHub CI, PR merge, production
source alignment, and the `v1.8.0` tag remain separate gates and are not implied
by the local results below.

Candidate gates completed before the release commit:

- Ruff and the explicit Python compilation list passed.
- All 302 backend tests passed with an isolated temporary `DB_PATH`.
- TypeScript passed; all 17 frontend files with 86 tests passed; all 6 extension
  tests passed; `npm audit --audit-level=high` reported zero vulnerabilities.
- Two production builds retained two generations of 32 assets with zero
  orphans; the 245,239-byte entry remained 71.7% below the baseline.
- OpenAPI contained 186 paths and 224 methods, all required login/session routes
  were present, and all three retired QR refresh/cooldown routes were absent.
- Gitleaks and `git diff --check` passed. The extension source and public ZIPs
  are now built reproducibly from ten allowlisted files; two consecutive builds
  produced the same SHA-256 and package verification passed.

These candidate results are retained as pre-release evidence. The GitHub,
rollback, deployment, local/public health, asset-equality, data-preservation, and
post-start log gates completed in the production release record above.

## Source State On 2026-07-17

The `codex/official-login-stability` branch starts from clean tag `v1.7.3` and replaces active custom QR, automatic-slider, and headless verification paths with one official Goofish browser service. New QR/password login uses the official parent page, existing accounts reuse `browser_data/user_<unb>`, verification stays human-operated, and listener replacement is bounded outside its account lock. Passwords, Cookies, email codes, reset grants, API keys, deployment tokens, databases, browser profiles, and live account data remain outside source control.

GitHub CI and the running service remain independent evidence: publishing or building this source does not prove a runtime was upgraded. Migration `2026071701` adds `cookies.browser_user_agent`; recheck the process path, health response, migration version, frontend entry bundle and referenced assets, account listeners, Cookie schedules, and Skill scheduler after every restart or deployment. Registration defaults closed on a new installation and must not be opened until the real SMTP receipt code and an end-to-end direct-registration acceptance test have both succeeded.

## Working Capabilities

- Multi-account official password, QR, and manual-Cookie binding with listener and auto-reply diagnostics.
- Stable Xianyu identity matching through `xianyu_unb`, so same-user re-login updates the existing account record.
- Persistent official browser profiles under `browser_data/user_<unb>`, with profile-only automatic renewal and no automatic saved-password submission.
- Unified official login APIs and state machine with owner isolation, read-only polling, expiry, cancellation, safe screenshots, and explicit local-browser display.
- One official refresh path that starts only from an explicit user action or one genuinely due schedule. Token and repeated connection failures enter passive `action_required`; active verification keeps one window for up to 15 minutes and closes after real Token validation plus Cookie/User-Agent/listener handoff.
- Product-scoped knowledge and training rules with draft/published separation, copy-to-draft, rule auditing, and guarded price replies.
- User-scoped AI provider profiles with encrypted keys, model discovery, and test-before-apply account switching.
- Recent-order discovery and reconciliation with completion, refund, cancellation, and login-required states.
- Skill Center manual and scheduled monitoring with a 15-minute minimum interval and persisted run state.
- Optional AI result filtering using an enabled account provider configuration.
- Webhook, WeChat, DingTalk, Feishu, Bark, and Telegram result delivery with `sent`, `partial`, and `failed` outcomes.
- Cross-run result deduplication by task and item URL, falling back to platform item ID.
- Expert prompts and real runtime, browser, AI, delivery, and account-listener diagnostics.
- One-transaction direct registration with capacity recheck, image CAPTCHA, purpose-bound email code, `v2` terms acceptance, and automatic login; successful email delivery keeps the completed CAPTCHA state, while explicit resend requires a fresh CAPTCHA.
- Username-or-email login and two-stage email password recovery: `POST /api/auth/password-reset/verify-code` issues a one-time grant held in frontend component memory, then `POST /api/auth/password-reset` consumes it and revokes all older sessions. The legacy reset payload remains temporarily compatible.
- Shared `BrandLockup` presentation across the main sidebar and public login, registration, password-recovery, terms, and privacy views, with the frontend version injected from `frontend/package.json` at build time.
- Administrator SMTP receipt confirmation, 1–1000 ordinary-user capacity, user enablement, and guarded registration switch controls.
- Ordinary-user personal item-sync settings with per-field global inheritance, plus user-owned AI provider access without administrator settings calls.
- One-request role-aware dashboard summaries, retryable error and empty states, deferred order details, and a separately loaded chart bundle.
- Purpose-isolated HMAC storage for authentication secrets and identifiers, persistent multi-dimensional rate limits, and trusted-proxy client-IP handling.

## Important Boundaries

- Training uses the current product draft; real buyer replies use only the published knowledge snapshot.
- Copying knowledge writes target drafts only, defaults to no overwrite, and never publishes automatically.
- Deleting an account removes account-linked data. Re-login or update the Cookie instead of deleting for session recovery.
- Scheduled Cookie refresh and Skill monitor schedules both default off. Cookie refresh allows 1 hour to 7 days; Skill monitoring allows 15 minutes or longer.
- Token and connection failures never launch Chrome, regardless of the schedule switch; manual start remains available and scheduled launch occurs once only when enabled and due.
- Goofish rejects headless Chromium. Official renewal uses a headed off-screen browser and becomes visible for human verification.
- Alibaba SMS, QR, face, and risk-control verification stays manual. A profile can renew without another scan only while its official session remains usable; logged-out profiles wait for the user in the same session.
- Skill Center notification delivery excludes QQ and email even though those channel types may exist elsewhere in the database.
- Capability readiness does not guarantee an external AI provider or notification endpoint will remain reachable.
- The scheduler depends on the intentional one-process, one-Uvicorn-worker runtime.
- Registration defaults off and cannot be enabled without a receipt-confirmed current SMTP fingerprint and remaining ordinary-user capacity.
- SMTP verification sends a six-digit code to the independent support mailbox and has no third-party fallback. Missing credentials, failed delivery, an unconfirmed code, database errors, or changed SMTP settings keep registration closed.
- CAPTCHA, email, and SMTP challenges expire after 10 minutes and stop after five attempts. Historical invite data is retained, while legacy invite APIs return HTTP 410.
- Password-reset grants are email-bound, expire after 10 minutes, and are single-use. The frontend keeps plaintext grant material only in component memory, and the backend stores only a purpose-isolated digest in the existing `auth_challenges` table.
- The system-secret key is independent from the AI-provider and Xianyu-account keys; all three local key files must be restored with SQLite when environment keys are absent.
- Authentication logs and runtime sessions must not expose Cookies, Tokens, verification URLs, the default administrator password, OTPs, reset grants, full email addresses, or passwords.

## Verification Baseline

Run before release or deployment:

```bash
source .venv/bin/activate
pip install -r requirements-dev.lock
ruff check .
python -m py_compile Start.py app_factory.py application_runtime.py api_routers.py auth_email_service.py auth_registration_service.py settings_service.py db_manager.py schema_migrations.py security_utils.py session_registry.py official_login_sessions.py repositories/auth_repository.py repositories/runtime_session_repository.py services/auth_service.py ai_provider_service.py ai_reply_engine.py account_session_refresh.py order_sync_service.py item_metric_service.py item_metric_scheduler.py backfill_order_snapshots.py browser_extension_pairing.py skill_monitor_scheduler.py skill_monitor_delivery_dispatcher.py skill_monitor_retention_janitor.py reply_server.py XianyuAutoAsync.py utils/browser_interaction.py utils/xianyu_official_login.py utils/xianyu_session_probe.py utils/qr_login.py utils/qr_verification_browser.py utils/outbound_http.py utils/outbound_smtp.py utils/verification_images.py
python -m unittest discover -s tests -v

cd frontend
npm audit --audit-level=high
npm run typecheck
npm test
npm run build
npm run build
npm run verify:build
```

Also run `git diff --check` and a secret scan over every tracked and prospective file. For deployment, back up SQLite, all three local encryption keys, browser profiles, and the previous static assets first.

The automated suite covers official login modes, profile promotion and reuse, non-password automatic renewal, verification timeout and cancellation, account data retention, listener cancellation timeouts and health responsiveness, Skill scheduler lifecycle and locking, success/failure rescheduling, AI filtering, supported-channel filtering, multi-channel notification outcomes, cross-run deduplication, registration transactions and races, challenge expiry and attempts, rate limits, trusted proxies, SMTP failure behavior, progressive reset grants, session revocation, public auth views, and administrator registration interactions. Real platform acceptance still requires operator-owned Xianyu, AI provider, notification, and SMTP accounts.

Deployment and live-account behavior were verified on 2026-07-17; release gates were rerun on 2026-07-18 after the final diagnostics wording cleanup:

- Release gates passed: Ruff, strict Python compilation, 274 backend tests, zero high-severity npm audit findings, TypeScript, 17 frontend files with 75 tests, two production builds, and build verification. The build contained 31 assets in each retained generation, zero orphans, and a 245,200-byte entry bundle. The final Gitleaks working-tree scan reported no findings.
- Authenticated synthetic `action_required` checks at 1440x900 and 390x844 had no horizontal overflow, console errors, duplicate start action, local-browser action, cancel action, or manual-completion action. Product-list/detail logs emitted bounded summaries, and structured account identifiers passed through the stable-identity masker.
- Deployment used a hash-verified mode-`0700` rollback snapshot outside the repository containing an integrity-checked SQLite backup, all three local keys, browser profiles, prior static assets, and prior live source. The single launchd worker became ready within three seconds; local and public readiness reported migration `2026071701`, and both HTML entries plus every referenced asset matched the deployed files.
- Startup normalized orphaned active refresh states to passive `action_required`. The first-account acceptance used exactly one start request and one same-window display request; state advanced `refreshing → verification_required → success` only after a real message Token passed. The browser closed after one Cookie/User-Agent/listener handoff. During the following 900 seconds, 60 local and 15 public health checks passed with no application browser, Chrome-for-Testing process, state drift, or repeated validation session.
- After final log hardening, a further 60-second observation produced 13 healthy local checks and no application browser. The 82 new log lines contained no raw stable account identifiers, Cookie values, verification URLs, full item payloads, error-level entries, or tracebacks. Both listeners remained enabled; the first account's explicitly restored schedule remained 360 minutes and the second account schedule remained disabled.
- The 2026-07-18 documentation and diagnostics reconciliation deployment used a fresh integrity-checked rollback snapshot outside the repository. Local and public HTML referenced `index-Mdv84IwF.js` and `index-OgVJmvqL.css`, and both responses matched the deployed files byte-for-byte. A final listener-bootstrap masking correction was then deployed with its regression test. During the final 60-second observation, 13 local and 5 public readiness checks passed with zero application-browser samples. Both listeners and their 360-minute-enabled/disabled schedule settings were preserved; both accounts remained in passive `action_required`. All 450 log lines from the final process contained no raw stable account identifier, Cookie value, verification URL, traceback, or error-level entry.

## Next Acceptance Steps

- Require the GitHub `secrets` and `test` jobs to pass for the exact release commit; local evidence above does not replace CI.
- Recheck the first account after its next genuinely due 360-minute schedule. Require one background official session at most, no early launch after Token or connection failures, and the same human-verification behavior if the platform asks again.
- Keep the second account Cookie schedule disabled unless the operator explicitly changes it.
- Complete password-reset acceptance with two old sessions: verify the email code before entering a new password, consume the in-memory grant, confirm both old sessions are rejected, confirm replay and the old password fail, and verify the new password through both username and email login.
- Keep Skill schedules default off and keep account-level scheduled Cookie refresh off unless an operator explicitly needs preventive renewal.
- Keep monitoring official page, SMTP, AI-provider, and notification changes; do not weaken human verification, rate limits, or secret-handling boundaries to improve automation rates.

## v1.10.3 Local Browser Login Deployment On 2026-08-01

The main current-device browser button now performs the bridge handshake, device
registration, session creation, and `XMC_START_LOGIN` in one flow. The extension
opens the official login URL in the user's existing browser profile, focuses and
reuses an active session tab when possible, and closes the official tab only after
the backend import and frontend confirmation succeed. Extension detection remains
diagnostic; web QR remains a separate fallback.

Temporary probe, cookie persistence, and listener handoff failures now persist as
retryable `failed` states with the original error code and login state retained.
The refresh loop converts stale retryable `action_required` rows back to `failed`
and continues on the next due probe; expiry, explicit human verification, and
identity mismatch remain manual handling states.

Local gates passed with 720 backend tests, 153 frontend tests, 11 extension
tests, type checking, Ruff, production build and build verification, extension
package verification, Python compilation, and `git diff --check`. The production
LaunchAgent is a single worker on port 8091; local and public readiness both
return HTTP 200 with migration `2026073101`. The deployed frontend entry and CSS
match local/public responses byte-for-byte, and both versioned extension ZIPs
return HTTP 200 with matching hashes.

The complete rollback unit was recorded outside the repository at
`/Users/mac/Library/Application Support/XianyuManager Rollbacks/client-browser-login-20260801-011335`.
It was recorded as containing the pre-deploy source archive, SQLite and runtime data snapshots,
browser profiles, prior static assets, patch/diff files, `verification.md`, and
`rollback.sh`. The rollback script passed syntax and `--check`; no live account
login was performed during deployment, so the remaining real-platform gate is a
manual user canary after the one-time extension installation. The path no
longer existed during the 2026-08-25 audit, so those contents are not currently
re-verifiable.

After deployment, the existing HTTP/2 Cloudflare tunnel briefly returned 502/530
while its edge connections cycled. A reversible LaunchAgent `kickstart` restored
the two connections; the following low-frequency local/public readiness sample
was 10/10 HTTP 200 with `ready` and migration `2026073101`. The application
process, database, static files, and rollback unit were unchanged by this tunnel
reload.

## v1.10.4 User-Machine Native Helper Candidate On 2026-08-02

The primary “本机 Chrome 登录” path now calls a loopback native helper on the
user's own macOS or Windows computer. The helper creates and owns one official
Chrome/Edge target, watches the resulting page and platform-domain Cookies, and
submits them directly to the existing one-time P-256 device-proof protocol. The
frontend never receives Cookie, password, verification-code, or Token material.
Extension import and web QR remain independent entries.

An existing remote-debugging browser keeps its prior pages and process. Without
one, the helper starts an application-managed Profile on the user's computer and
releases that browser after confirmation or cancellation. The server verifies a
real message Token, Cookie identity, `unb`, and User-Agent before persistence. A
temporary Token or persistence failure returns to `waiting_user` and retries with
a fresh challenge; replayed, expired, transport-confused, and identity-mismatched
submissions remain rejected.

Final local gates passed with Ruff, 746 backend tests, 24 frontend files with 153
tests, 11 extension tests, TypeScript, npm audit, production build, build/static
verification, extension-package verification, and `git diff --check`. Real Chrome
smokes covered both an application-managed Profile and an existing CDP endpoint.
The standard macOS `.app` started in about 1.76 seconds, listened only on
`127.0.0.1`, returned helper version `1.0.1`, and passed deep strict bundle
verification.

GitHub CI run `30729570218` and native package run `30729574347` passed for
`012db7495c0180a312b60055a09dea388397e40c`. The Windows runner produced a real
x64 PE executable; its ZIP SHA-256 is
`8c492863e1d74c86e34f38ba4f20fe6eab60ef38136468c7e81323765bc3d50a`.
The macOS CI ZIP SHA-256 is
`bf9952e3771e946101cfb7dc40d9fd5882dd14de67002717afe78e49cf450f1c`;
the downloaded app passed strict deep verification, started from the public ZIP,
listened only on loopback, and accepted the production console Origin with PNA
headers. macOS remains ad-hoc signed, and Windows remains unsigned.

The first production rollout passed every local gate but the public tunnel
returned HTTP/2 `530/1033`, so the complete application rollback ran immediately
and restored version `1.10.3`, migration `2026073101`, SQLite integrity, static
assets, and the single worker. The public failure persisted after rollback.
Tunnel prechecks showed QUIC available while TCP 7844 was blocked; the dedicated
LaunchAgent was switched from HTTP/2 to QUIC after a temporary connector proved
two registered edge connections and public HTTP 200. Its separate rollback script
is retained with the application rollback unit.

The second rollout deployed the exact `012db74` source tree, migration
`2026080101`, the 36-asset production generation, the versioned macOS and Windows
helper ZIPs, and extension `1.2.1`. The production checkout uses local merge
commit `a8caed4` only to preserve the pre-existing production candidate history;
its source tree has zero file differences from `origin/main@012db74`. Local and
public readiness, OpenAPI `1.10.4`, HTML entry `assets/index-B3_Qlwcs.js`, all
three public package hashes, SQLite integrity, one listener, repeated public
HTTP/2 samples, and both rollback checks passed. The complete record was
`/Users/mac/Library/Application Support/XianyuManager Rollbacks/native-helper-login-20260802-105146/verification.md`;
that path no longer existed during the 2026-08-25 audit, so the historical
record is not currently re-verifiable.

The remaining live-provider gate is a real ordinary-user login from that user's
computer: start the downloaded helper, let it open that user's Chrome, complete
the platform verification, verify Token and `unb`, confirm the persisted account
in the frontend, and confirm only the helper-owned tab closes.

## Native Helper 1.0.2 Persistence Release On 2026-08-03

Helper `1.0.2` closes the remaining install-once gap. The first packaged launch
installs the helper for the current user and registers a macOS LaunchAgent or
Windows current-user startup entry. Later console clicks can reach the loopback
service after login or reboot without an extension and without manually starting
the helper. Chrome remains the first choice and Edge the fallback; extension
import and web QR remain separate paths.

Local gates passed with 757 backend tests, 30 focused helper/package tests, 24
frontend files with 153 tests, 11 extension tests, Ruff, TypeScript, npm audit,
Actionlint, Gitleaks, production build and package verification. Native package
run `30774220706` executed install, startup registration, health, single-instance,
restart, status, and uninstall on both macOS and Windows runners. Final CI run
`30774536869` passed for `0d32354`; release-evidence CI run `30775493735`
passed for `0e290b4`.

The deployed macOS arm64 ZIP SHA-256 is
`c4e9b9be03816738859933ff68ae1f68e92c2b71838b065e7fcc69e55919e305`;
the Windows x64 ZIP SHA-256 is
`95a548fd739a37d015dea77a00910930d89f15c17952bb092eac8a7b5438e67e`.
Production functional merge `ed42def` first matched `origin/main@0d32354`.
Evidence-only merge `61a15a3` then synchronized the production source tree to
`origin/main@0e290b4` without restarting PID `82448`.
Local and public readiness passed five repeated samples, SQLite remained at
`2026080101` with integrity `ok`, one Uvicorn listener remained, and public HTML,
JS, OpenAPI, extension, and both helper downloads matched the deployed bytes.

Two early deployment attempts intentionally exercised the rollback gate after
release assertions were found to be wrong: one searched only the main chunk for
a lazy-loaded account link, and one used an incorrect session route name. Both
restored the prior source, static files, database, and readiness. The corrected
third attempt passed. The complete record and executable rollback are in
`/Users/mac/Library/Application Support/XianyuManager Rollbacks/native-helper-persistence-20260803-082948`.

A final browser-level loopback gate started the published macOS `1.0.2` helper
from an isolated state directory and loaded the production console Origin in
real headless Chrome. Page JavaScript fetched the helper health endpoint on
`127.0.0.1:17890` and received HTTP 200 with version `1.0.2`; the private-network
preflight returned 204 and an unexpected Origin returned 403. The temporary
listener, state directory, and test Keychain record were removed afterward.
This proves the browser transport and Origin/PNA boundary, not a real account
login.

The macOS package is still ad-hoc signed and the Windows package has no
Authenticode signature. The ordinary-user-machine live login canary remains
pending and must prove real platform verification, message Token and `unb`,
account persistence, frontend confirmation, and helper-owned tab closure.

## Cloudflare Tunnel 1033 Recurrence And Watchdog On 2026-08-07

The application origin remained healthy (`127.0.0.1:8091/health/ready` HTTP 200,
PID `82448`) while the user-level `cloudflared` process remained alive with
`readyConnections=0`. The public readiness endpoint returned HTTP 530 and the
connector log repeatedly recorded QUIC timeouts and exhausted edge addresses.
The current LaunchAgent still uses the existing local resolver argument; no DNS,
proxy, or origin configuration was changed. A controlled restart with the same
token and `--protocol auto` restored an edge connection and public HTTP 200.

The repository now includes `cloudflared_watchdog.py`, focused tests, and
`ops/launchd/com.cxywjx.cloudflared-watchdog.plist.template`. The watchdog only
reads `127.0.0.1:20241/ready`; after two consecutive zero-connection samples it
kickstarts `com.sub2api.cloudflared`, waits for a ready connection, and applies a
180-second cooldown. It never restarts the application worker or edits the local
proxy/DNS process. Production installation and its rollback are recorded in the
dated rollback unit for this incident after the live stability window completes.

## Lead-order guard on 2026-08-19

The production order guard now classifies platform records from positive markers:
`leadReservation`/`idleBizCode=7000` and lead components identify a lead order;
`idleBizCode=6` or the explicit `LOGISTICS_SEND` action identifies an ordinary
order. Automatic delivery and invitation staging accept only the ordinary class;
lead and unconfirmed records fail closed before card reservation, platform
confirmation, or buyer messaging.

The release was assembled from a production checkout snapshot and deployed only
the three task files (`XianyuAutoAsync.py`, `order_sync_service.py`, and
`invite_bridge_poller.py`) by same-directory atomic replacement. A single
controlled LaunchAgent reload loaded the new modules in PID `43286`; readiness
returned in two seconds, the local and public endpoints remained `ready`, and
the service stayed on one `8091` listener with migration `2026081601`.

The candidate isolation suite passed `150` tests with `46` subtests; the current
backend suite passed `963` tests with `197` subtests. The target lead order was
read-only checked before and after the cutover: it remained `pending_ship` and
`system_shipped=0`, with no fulfillment attempt or card reservation added. Full
hashes, patch replay, failure-recovery rehearsal, production backup, and the
executable rollback are recorded in
`outputs/lead-order-guard-20260819T204757+0800/evidence/verification-record.md`.

## 2026-08-26 前端关键路径性能优化发布

本轮完成订单与账号读取超时止损、订单图片直链优先与代理回退背压、页面按意图加载，以及账号状态自适应轮询。订单图片代理前后端均限制为 4 并发，并具备同订单 single-flight、失败负缓存和显式重试绕过；账号稳定态每 15 秒检查，刷新或验证活跃态保持 3 秒，页面隐藏时暂停并取消在途请求，任何批次未结束前不会叠加下一轮。

维护源提交为 `508b91a`、`f7afdbc`、`2f07bf1`、`946ba80`、`8a7dc8a`、`8e693dc`。低优先级单 worker 门禁为前端关键路径 5 文件 `80 passed`，后端图片契约 `15 passed / 3 subtests`，TypeScript、Vite build、构建产物校验、`py_compile` 与 Ruff 均通过。生产发布只增量写入新静态资产，原子替换 `static/index.html`、资产代际清单和 `reply_server.py`，随后执行一次受控服务重载；没有数据库写入或迁移。发布后本地与公网 readiness 均为 `ready`，迁移保持 `2026082502`，单个 `8091` listener，公网 HTML 与入口 JS 分别和生产本地文件哈希一致，OpenAPI 已暴露图片端点的 `retry` 参数，五个账号 listener 均恢复心跳，启动切片未出现 ERROR、Traceback 或 CRITICAL。回滚原件、候选、校验脚本及验证记录位于 `outputs/frontend-critical-20260826T005740+0800/` 与生产 `_rollback` 同名目录，`rollback.sh --check` 为 `PASS`。
