# 代理主站共享两连发（2026-08-29 · 已上云，当前运行 delivery-site-share-20260829-2132）

- 需求拍板（用户原话口径）：代理是“我的代理，消耗我的无所谓”——AI 平台与卡密自动发货全部同步主站；决策卡答复=AI 共享立即提交+部署、卡密发货完全同步主站。
- AI 平台站级共享（commit `1fb114f`）：db 层 `get_site_default_ai_provider_profile` + 双读路径（`get_ai_reply_settings` / `get_ai_reply_settings_for_user`）回退；顺序=账号绑定平台 > 账号自有 Key > 管理员站级默认已验证配置 > 系统全局 Key；回退时 `provider_profile_id` 恒 None、`api_key_source='site'`，前端 AccountList 显示「主站共享配置」，明文 Key 不下发、admin 未验证配置不外借；模型经 `resolve_ai_model_and_base_url` 与站级平台对齐。新测试 `test_ai_providers.py` +5。
- 卡密发货主站回退（commit `d7d5641`）：`XianyuAutoAsync._match_rules_with_site_fallback`（普通与多规格两分支同语义）——代理无自有命中规则时回退 admin 规则并消耗主站卡密；自有规则永远优先、商品显式发货方式（resource/off/invite）不回退、admin 自身不重复查询、发货计数落真实命中的主站规则行；db 层 `get_site_admin_user_id`，租户隔离 SQL 未动（fail-closed 保持）。新测试 `test_delivery_site_fallback.py` 8 条。
- 发布链（当日三连）：`multitenant-loginbase-20260829-2000` → `ai-site-share-20260829-2113` → `delivery-site-share-20260829-2132`（当前运行）；迁移 `2026082901` 三次不变；每次均容器 healthy、双公网 ready 200、Traceback=0、监听 9 任务恢复；回滚脚本三份就绪（见云 PROGRESS 顶部条目）。
- 注册现状：注册开+强制邀请码已上线（SMTP 经用户真实收件码验证后手动开启）；管理员已自行生成 1 个未使用邀请码（30 天有效）；普通用户 4 个全为历史停用态，尚无真实代理注册。

# 代理多租户（2026-08-29 · 六任务全部完成，已上云 multitenant-loginbase-20260829-2000）

- 目标：分销代理经邀请码注册进监控台，只管自己的闲鱼账号（销量/商品/AI/自助扫码重登），互不可见；admin 独享全站汇总+分代理明细+账号总览；禁用代理即账号下线。开发+测试完成即止，不部署不 push（等领导确认）。
- 顺序：任务 0 基线 → 1 邀请码注册 → 2 全站汇总+admin 明细 → 3 admin 账号总览 → 4 告警按归属路由+自助重登走查 → 5 禁用处置 → 6 越权渗透固化。
- 最大风险：与并行登录会话共享 4 文件（reply_server/db_manager/schema_migrations/XianyuAutoAsync）——只加不改其未提交 diff，git add 永远点名文件；其在制品已致 12 个测试红（见 BLOCKED 首条），完成口径按快照调整。
- 任务 0 完成（15:5x）：基线快照 12 failed + 1101 passed（归因并行在制品）；Ruff 全绿；并行在制品 12 文件清单已录（git status 快照 /tmp/tenant-task0-gitstatus.txt）；迁移号 2026082901 已被并行占用，本任务用 2026082902。
- 现状底账：registration_invites 表已在迁移存在（列全）、register_user 有 invite_code 参数但无校验实现、registration-config 硬编码 invite_required=False、前端有 invite_required 类型无输入框、cookies.user_id 归属/仪表盘按用户隔离/扫码会话归属校验均已在。
- 任务 1 完成（16:2x）：邀请码注册复活（v1.7.0 移除的功能）——auth_registration_service.py 服务层（创建/列表/吊销/开关，HMAC digest 存储明文只回一次，事务内唯一消费）+ 三个 410 路由复活 + PUT /api/admin/registration/invite-required + 前端注册输入框与管理区块。开关默认关=存量语义零破坏。新测试 8（test_registration_invites.py）+ 端到端改写；反向验证 7 红 1 绿（绿者恰为 legacy 用例）。
- 任务 2 完成（16:3x）：GET /api/dashboard/global-summary（任何登录用户见全站合计，无分用户泄漏）+ GET /api/admin/dashboard/agents（admin 独享分代理明细）；db_manager 两聚合方法（口径与 dashboard-summary 一致）；前端 SiteOverview 组件。新测试后端 2 + 前端 3。
- 任务 3 完成（16:4x）：GET /api/admin/accounts/overview（cookies JOIN users 单 SQL，掉线判定/归属/登录方式，绝不出 cookie value）；前端 AdminAccountsPanel 挂 Settings 管理区。新测试后端 1 + 前端 2。
- 任务 4 完成（16:5x）：走查结论=告警归属路由/绑定双校验/自助重登链路均已就位无需改产品代码；补回归锁 4 测试（test_account_ownership_routing.py）。
- 任务 5 完成（17:1x）：禁用处置——get_inactive_user_ids + cookie_manager 加载/启用护栏（停用属主账号标 disabled、enable 被拒）+ update_registration_user 启停后 reconcile_from_db 同步运行态（listener 下线/恢复），失败 503。新测试 4（handoff 2 + hardening 1 + registration 1），62 passed。
- 任务 6 完成（17:4x，18:1x 收全量门禁）：tests/test_privilege_escalation.py（7 用例 + 69 subtests）——纵向 41 条 admin 路由普通用户全 403 + 未认证 401 + admin 正控制；横向 24 条 cookie-scoped 路由跨租户全 403 + QR 会话 403/404 + 自有资源正控制。反向验证非空洞（关守卫即变红）。全量 `pytest tests/` 最终 2 failed + 1183 passed + 276 subtests——2 失败均为并行登录会话在制品地界（迁移号断言 2026082901≠2026082502），其余全绿含全部新增；test_application_architecture 路由数断言已随新增 6 路由更新 244→250。
- 部署清单备忘（等领导确认后）：生产开邀请码需管理页开关或 `registration_invite_required=1`；无新迁移（registration_invites 表 v1.6 起已在，迁移号 2026082902 未用上）。→ 已执行：2026-08-29 20:02 与登录地基合并上云（镜像 `multitenant-loginbase-20260829-2000`，迁移 2026082901——登录地基的 cookies 代理列所致）；注册开+强制邀请码同晚由用户验证 SMTP 后开启。

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
- 未动文件说明：order_sync_service.py / delivery_stage_metrics.py 在白名单内但本次无需改动（回查复用既有 fetch_xianyu_order_detail + parse_order_detail_payload，埋点复用既有阶段常量）。

## 发货提速（已上云 · 2026-08-29）

- 生产镜像 `xianyu-ai-manager:ship-speedup-20260829-072150`（覆盖 `XianyuAutoAsync.py` / `invite_bridge.py` / `invite_bridge_poller.py`，sha256 与维护源 HEAD `b0d6f4c` 一致）。基线 `p0p2-observability-20260828-2120`。
- 风控闸保持失败关闭：同买家 fan-out 15s 冷却、单次最多 5 笔、待发货只扫前 2 页；核验重试仅 `order_not_observed` 的 2/4/8s；对账默认 600s 一轮、每账号 ≤5 笔，只补免拼+虚拟发货，不重发码。
- 按用户拍板未修存量卡单（活跃号 10 笔、历史号遗留、代点未打开链接的 2 笔）。
- 健康检查：云端 18091 与公网 `xianyu.cxywjx.top` `/health/ready` = ready，迁移仍 `2026082502`。
- 回滚：`outputs/ship-speedup-20260829T072150+0800/rollback/rollback.sh --check|--execute`。证据：`outputs/ship-speedup-20260829T072150+0800/evidence/verification-record.md`。
- 下一笔真实新单才是现网速度金丝雀；代码门禁不能代替墙钟。

## 拼单发货修复（已上云 · 2026-08-29 14:48）

- 根因（当日调查实证）：拼团/两人小刀订单平台编码 `idleBizCode="6000"`、`xGlobalBizCode="idleShop|pinGroup|c2c"`，而 08-25 bundle-a 的 `classify_order_business_type` 只认 bizCode=="6" 或 LOGISTICS_SEND 按钮 → 拼团单判 unknown → 热路径拒发（order_business_type_unconfirmed 不重试）+ 30s 轮询永久跳过；仅买家再发一条消息触发二次核验才自愈。08-27 上云起生效，非 08-28/29 两笔提交引入。生产影响：1 笔卡 10h 已被申请退款、1 笔被买家取消、平台仍挂 ~10 笔拼团待发货存量。
- 改动 1（order_sync_service.py）：新增 `ORDINARY_BIZ_CODES = {"6","6000"}` 与 `ORDINARY_BIZ_CODE_KEYWORDS = ("pingroup",)`，分类循环命中即 ordinary；lead 分支与「lead∧ordinary→unknown」失败关闭语义一字未改。一处改动同时修热路径门禁、send-message 门禁、30s 轮询、对账重发器四条链路。
- 改动 2（XianyuAutoAsync.py）：`INVITE_VERIFY_RETRYABLE_ERROR_CODES = {order_not_observed, order_business_type_unconfirmed}`，2/4/8s 退避循环按集合判可重试；确定性失败仍首查即弃，重试上限/请求预算不变。
- 测试：新增 3 条（拼团分类三形态+冲突仍 unknown / 热路径 unconfirmed→重试→投递 / 真实 normalize_order_record 归一化拼团平台行→轮询 stage+发事件），均反向验证（stash 源码后变红）。定向 226 passed + 53 subtests；全量 **1101 passed + 205 subtests, exit 0**（HEAD 基线 1098 + 3，对账吻合）；Ruff/py_compile 过。
- 用户拍板（14:16 决策卡）：现在改码+补测试，部署等确认；存量 ~10 笔随修复自动收敛（不加豁免，幂等不重码）。14:44 用户确认部署。
- 发布（14:48）：镜像 `xianyu-ai-manager:pingroup-fix-20260829-144558`（基底=部署时刻运行镜像 `browser-ext-1.2.3-20260829-0745`，仅 COPY 两文件）。`DEPLOY_APP_A=PASS`；容器内两文件 sha256 与维护源工作树一致；本地 18091 + 公网双域名 `/health/ready`=ready；迁移 `2026082502` 不变；`rollback.sh --check`=PASS；启动切片 Traceback/ERROR=0；心跳恢复（295 次/3 分钟）。
- 生效证据：修复前每轮必打的「邀请桥订单跳过非普通业务类型」发布后 **0 次**。当前轮询跳过均为正当理由：2 笔 refunding（含用户要人工处理的退款单）、1 笔 cancelled、5 账号 skipped_reauth（登录过期，既有状态）。
- 存量收敛路径（多路）：①从没收到链接的存量单→轮询 stage+补链接；②已发过链接的→幂等跳过等买家点（日志见「已有下游消息操作」×4 笔）；③本地已发×平台待发货漂移→对账重发器 600s 首轮补免拼+真发货；④挂在过期账号上的→等用户扫码重登。
- 退款单（尾号 005037，ref_8e58c959c5）系统正确拒发（status=refunding），留用户人工处理。
- 证据：`outputs/pingroup-fix-20260829T144558+0800/evidence/verification-record.md`；回滚 `outputs/pingroup-fix-20260829T144558+0800/rollback/rollback.sh --check|--execute`（回 browser-ext-1.2.3）。

## AI 回复图片降级修复（已上云 · 2026-08-29 15:30）

- 根因（当日调查实证）：买家发图片消息时 `generate_reply` 先经 `_prepare_image_parts` 下载+六种安全校验（provider 兼容/CDN 白名单/响应类型/解码/像素上限/格式），任一失败抛 ValueError → 外层 except 只记 error_type 不记原因 → 整条回复放弃 → 买家收不到任何回复。08-27 上云起 ~20 次/天（18/27/18），与发货链路无关。
- 修复（ai_reply_engine.py 单文件）：两处调用点（订单感知主路径+legacy）捕获 ValueError 降级——warning 记具体失败原因（固定文案无敏感数据）+ image_parts 置空；纯图片消息由既有非文本引导接管（回「图片我这边看不了，麻烦文字描述下问题」并落 draft），混合消息走无图正常生成。六种校验零放宽，失败图片绝不进模型。
- 测试：新增 2 条（主路径纯图降级引导+不触模型+draft 落库 / legacy 降级无图生成），反向验证 stash 后恰好 2 红。全量 `pytest tests/` **1103 passed + 205 subtests**（基线 1101+2 对账吻合）；Ruff/py_compile 过。注意全量口径必须 `pytest tests/`，裸 `pytest` 会误采集 outputs/ 快照测试报 35 collection errors。
- 发布（15:30）：镜像 `xianyu-ai-manager:ai-image-fallback-20260829-152854`（基底 pingroup-fix-144558，仅 COPY 1 文件）。`DEPLOY_APP_A=PASS`；双哈希一致（本地=staging=容器 `bcdeabfb…`）；拼团修复标记仍在（派生链未丢）；`rollback.sh --check`=PASS（回 pingroup-fix）；部署后 4 分钟日志无 traceback，心跳/API/邀请桥轮询正常。
- 观察项：ValueError 应归零；新 warning `入站图片处理失败，降级为无图回复: reason=…` 将首次揭示线上实际失败原因是六种中哪一种（旧日志不记原因无法定位）。
- 证据：`outputs/ai-image-fallback-20260829T152854+0800/evidence/verification-record.md`；回滚 `outputs/ai-image-fallback-20260829T152854+0800/rollback/rollback.sh --check|--execute`。

# 扫码账号免密自动续签（五阶段）：已发布上线

- 目标：扫码账号留下持久浏览器记忆（L3），cookie 失效后免密自动续签，不必再绑密码。
- 顺序：0 基线 → 1 扫码落 profile/DB 标记 → 2 免密续签优先于账密 → 3 放开门槛 → 4 分层错误 → 5 CDP 接管。全部完成并于 2026-08-25 18:42 部署生产（用户明确指令将「部署前真人 canary」调整为「部署后首次真实观察」）。
- 发布内容：五阶段 `7055de7` + 审查优化 `330ab1c`（免密续签防假成功 session_not_renewed、快速进入按钮缺失判 fast_entry_unavailable、CDP 建档失败不虚标 L3、启动失败分类收窄）。
- 最大风险不变：闲鱼 passport「快速进入」和 CfT 指纹会漂移；代码测试不是真机证据，首次真实免密续签（约 10h 后 L2 过期时）才是真机验证。

# 当前生产状态

- 最终复核时间：2026-08-29 15:35。唯一活跃生产是云 HOST `app-suite-candidate` 容器 `app-a-cloud-app-a-1`，镜像 `xianyu-ai-manager:ai-image-fallback-20260829-152854`。容器 healthy，迁移 `2026082502`。Mac LaunchAgent / `8091` 已冻结，不得再据其推导线上状态。
- 镜像链（新→旧）：AI 图片降级（本镜像）→ 拼单修复 `pingroup-fix-20260829-144558` → 浏览器扩展导入修复 `browser-ext-1.2.3-20260829-0745` → 发货提速+破自锁+对账 `ship-speedup-20260829-072150` → P0/P2 埋点 `p0p2-observability-20260828-2120` → 小刀两段式、白屏固化、404 no-store、Bundle A / L3 / Toast / 仪表盘等。
- listener 注册隔离仍有效；`runtime_sessions` 只记录带 TTL 的临时操作，不代表 listener 数量。
- 维护源是 `/Users/mac/Documents/咸鱼监控台`；Mac `/Users/mac/Library/Application Support/XianyuManager` 是冻结回滚备份。两棵树不得整树互相覆盖。工作树中的 `.cursor/` 与未提交 `browser_extension/*` 继续保留，不清理、不覆盖。

## 部署后观察（替代真人 canary）

- 下次任一扫码账号登录成功 → 应写入 `browser_data/user_<unb>` 并置 `has_l3_memory=1`（账号列表出现 L3 标识、`auto_refresh_supported=true`）。
- 约 10 小时后 L2 过期 → 观察 `account_session_refresh`：`浏览器记忆免密续签成功`=目标；`session_not_renewed`=网络类可重试；`fast_entry_unavailable`=单向 manual + 一次性告警邮件（需重新扫码）。
- CDP 接管（任务 5）代码级失败关闭测试全过；本机真实 Chrome 冒烟需开 `--remote-debugging-port` 后另行执行，未配置 `XIANYU_CHROME_CDP_ENDPOINT` 时该路径不启用。

## 证据路由

- 发货提速云端发布（08-29）：`outputs/ship-speedup-20260829T072150+0800/`（evidence/verification-record.md；回滚 `rollback/rollback.sh`）。
- 营收趋势图恢复早上版本发布（08-26 01:43）：`outputs/trend-restore-20260826T014313+0800/`（verification-record.md、post-deploy-verify.txt；回滚在生产 `_rollback/trend-restore-20260826T014313+0800/`）。
- 前端关键路径性能优化发布（08-26 00:57）：`outputs/frontend-critical-20260826T005740+0800/`（verification-record.md、source-head.txt；回滚在生产 `_rollback/` 同名目录）。
- 运营概览精修发布（22:28，含过载事故记录）：`outputs/hero-refine-20260825T222457+0800/`（evidence/verification-record.md、original/、rollback/rollback.sh + static-original）。
- 仪表盘图表区重组发布（20:59）：`outputs/dashboard-refine-20260825T205258+0800/`。
- L3 发布：`outputs/l3-login-deploy-20260825T183702+0800/`（evidence/verification-record.md、original/、candidate/、rollback/rollback.sh + db-backup + static-original）。
- Toast 静态发布：`outputs/knowledge-toast-20260825T190639+0800/`（并行会话，纯前端）。
- 上一发布：`outputs/bundle-a-20260825T133612+0800/`（evidence/verification-record.md、patch、rollback）。
- 当前发布、历史发布与回滚：`docs/handoff.md`。
- 尚未闭合的外部门禁：`BLOCKED.md`。
