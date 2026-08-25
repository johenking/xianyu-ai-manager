# Architecture

## System Shape

Xianyu AI Manager is a FastAPI + SQLite + React/Vite application for Xianyu account operations, auto-reply, order handling, product-bound card delivery, seller auto-rating, and product knowledge.

Main runtime path:

1. `Start.py` starts one Uvicorn worker using the `app_factory:create_app` factory.
2. `app_factory.py` owns FastAPI lifespan; `application_runtime.py` starts and stops the CookieManager, item-metric scheduler, invitation poller, and seller auto-rate scheduler on the server event loop.
3. `reply_server.py` keeps endpoint implementations compatible while `api_routers.py` groups routes into auth, account, AI, order, settings, content, admin, system, invitation-bridge, and frontend `APIRouter` domains.
4. `db_manager.py` retains the compatibility persistence facade. New domain SQL starts in `repositories/`, while domain decisions live in `services/`; authentication is the first extracted boundary.
5. `cookie_manager.py` starts one `XianyuAutoAsync.XianyuLive` task per enabled account and exposes an awaited shutdown path.
6. `ai_reply_engine.py` assembles product-scoped context, calls the selected provider, audits rules, and optionally regenerates once.
7. `frontend/` lazy-loads business pages and dashboard charts, exposes domain API/type modules through compatibility barrels, and builds React assets into `static/`; FastAPI serves the SPA and `/static/*`. The main sidebar and public authentication/legal views share `BrandLockup`, while Vite defines `__APP_VERSION__` from `frontend/package.json` for build-time version labels.

The deployment model is intentionally one process, one Uvicorn worker, one asyncio event loop, and SQLite. It does not claim horizontal multi-worker support.

## Authentication And Account Identity

Backend users live in `users`. The initial `admin` password is read from `ADMIN_PASSWORD` only when a new database creates the user. New passwords use bcrypt cost 12; a successful legacy SHA-256 login upgrades the stored hash. New login Sessions store a Token digest, expire after 30 days, and are removed by `/logout`; legacy records remain readable during migration.

Usernames are NFKC-normalized and emails are lowercased for case-insensitive uniqueness. Ordinary users carry active state plus the accepted terms version and timestamp. `/login` resolves one identifier as either username or email. Disabling a user or resetting a password deletes all persisted sessions for that user and removes matching in-memory sessions.

Direct registration is fail-closed. `registration_enabled` defaults to false and migration `2026071103` forces it false, sets agreement `v2`, installs a default ordinary-user limit of 20, and consumes invitation-era registration challenges. Readiness requires a valid support email, receipt-code confirmation for the exact current SMTP fingerprint, and remaining ordinary-user capacity. Disabled ordinary users count toward capacity; the administrator does not. A `BEGIN IMMEDIATE` registration transaction rechecks the switch and capacity, validates the purpose-bound email challenge, normalized identities, password policy, and terms version, then creates the user and consumes the challenge. Filling the last slot closes the switch in the same transaction.

`registration_invites` remains as historical, non-destructively retained data; runtime invite creation, listing, revocation, and consumption are retired. `auth_challenges` stores digests for image CAPTCHA, registration email, password-reset email, password-reset grant, and SMTP receipt-code secrets, with expiry, attempt, and consumption state. `auth_rate_events` stores HMAC digests for IP, email, and account dimensions. Forwarded client addresses are considered only when the direct peer is in the `auth_trusted_proxies` setting.

Password recovery is progressive. `POST /api/auth/password-reset/verify-code` consumes the purpose-bound email code and returns an email-bound, expiring, one-time grant. The public frontend retains the plaintext grant only in component memory; the backend stores its purpose-isolated digest in the existing challenge table. `POST /api/auth/password-reset` consumes the grant, changes the password, and revokes the user's existing sessions. The old reset payload containing `challenge_id`, `verification_code`, and `new_password` remains temporarily accepted for client migration. A successful email-code send does not trigger another CAPTCHA request; an explicit resend after cooldown starts with a fresh CAPTCHA.

`auth_email_service.py` is the only authentication-mail path. It sends through the configured SMTP server and has no third-party fallback. SMTP authorization codes are encrypted by `SystemSecretCipher`; the same independent system secret derives purpose-separated HMAC keys. SMTP verification first saves the configuration as unverified and sends a six-digit code to the support mailbox. Confirmation binds that code to the current fingerprint; changing any SMTP field invalidates verification, consumes pending SMTP challenges, and closes registration. Authentication-code delivery uses the same public response and SMTP path for eligible and decoy targets so account existence is not exposed.

`schema_migrations` records ordered migrations. A pending migration backs up the SQLite database plus the AI-provider, Xianyu-account, and system-secret local keys before starting one transaction. The compatibility upgrader keeps database version `1.6` idempotent while ordered migrations add the registration security schema, normalized identity indexes, order-analysis indexes, persisted official-browser identity, product delivery bindings in `2026081302`, seller auto-rating in `2026081601`, the delivery center in `2026082401`, and account L3 browser-memory flags in `2026082502`. A case-insensitive identity conflict aborts migration instead of merging users. Xianyu login passwords use an account-specific Fernet key that is separate from the AI provider and system-secret keys; account detail and status APIs never return the plaintext or ciphertext.

Xianyu accounts live in `cookies`. `cookies.xianyu_unb` stores the stable Xianyu identity extracted from the Cookie and is unique within a backend user. Re-login and Cookie updates use `(user_id, xianyu_unb)` to update the existing row instead of replacing its primary key. This preserves account-scoped AI settings, rules, knowledge, products, orders, and delivery data. Deleting an account remains destructive and is not a session-refresh mechanism.

The packaged native helper has been removed. The UI recommends the independent web-QR path and offers server-side headed Playwright Chromium as a local/formal-domain fallback; remote devices can use the browser extension. The extension submits signed login state directly to `/api/client-browser/import`, and Cookie, Token, password, OTP, and verification content still do not pass through the frontend.

`client_browser_login.py` owns the five-minute device/session/challenge protocol. Migration `2026080101` adds `client_browser_devices.client_type`; after the native-helper removal only `extension` devices can register, and historic `native_helper` rows stay readable without any login or renewal capability. A login reaches success only after the server validates a real message Token and `unb`, persists the account, and the frontend confirms the account list. The extension then closes only its own official tab. Temporary probe or persistence failures release the import for a fresh challenge instead of turning into a human-verification state.

`utils/qr_login.py` owns the independent API QR path and renders the official `codeContent` locally; ordinary QR generation and scanning do not launch a browser. Secondary interactive verification is handed to the server-side local Chrome window when the UI exposes it, or to the extension. `utils/xianyu_official_login.py`, `utils/qr_verification_browser.py`, and `utils/browser_interaction.py` power that server-browser path. Backend access requires a valid console session; unfamiliar network sources are logged for observation rather than used as a Host-based rejection rule. The UI exposes the path only for loopback and the formal domain. These flows launch Playwright's bundled Chromium by default (`XIANYU_BROWSER_CHANNEL` selects an installed system browser instead), do not override its User-Agent or disable web security, and still require a real message `accessToken`, expected-`unb` matching, Cookie persistence, and listener replacement before success.

Cookie refresh state is persisted in `account_session_refresh_status` with `idle`, passive `action_required`, active `refreshing` and `verification_required`, stable `manual_reauth_required`, plus `success`, `failed`, `timeout`, and `cancelled`. Only password accounts with valid stored credentials support automatic renewal. Renewal reuses `browser_data/user_<unb>` first and submits decrypted credentials only after complete logout. Non-password sources and human-action password failures persist `manual_reauth_required`; later triggers return the matching CTA without launching Chrome. Manual requests atomically reserve the account before background scheduling, and duplicate requests return the active state without queuing. Listener replacement receives the already-probed Token, persisted browser User-Agent, renewal anchor, and item-sync anchor in one generation.

Goofish rejects Chromium headless mode as an illegal browser. Background renewal therefore uses a normal headed browser positioned off-screen. If Alibaba returns a verification address, it is kept only in memory and opened in that same official context. The service stores only a safe screenshot and keeps polling the page, `unb`, Cookie set, and message Token for up to 15 minutes. `browser_active` is computed from the live Worker; only `verification_required` with `browser_active=true` exposes show/cancel controls. The UI does not ask the user to confirm completion because the backend continues automatically.

`official_login_sessions.py` owns the authenticated server-browser SMS/password state machine and its QR-compatible mode: `preparing`, `waiting_user`, `verification_required`, `persisting`, `restarting_listener`, and the terminal states `success`, `expired`, `failed`, `cancelled`, or `interrupted`. One background task performs completion exactly once; status reads have no lifecycle side effects. Temporary login, AI training, and Cookie refresh operations share a Session Registry. `runtime_sessions` stores only session type, owner, account identifier, state, redacted error, and TTL; its row or status count is not an account-listener metric. Polling another user's session returns HTTP 403. Passwords are passed only to the background task and are never stored in login dictionaries or the registry. Cookies, Tokens, QR content, verification URLs, Playwright objects, and AI conversation content are also excluded. On restart, active browser-backed records become `interrupted` and the UI asks the operator to start again.

CookieManager listener replacement uses a short account-lock section to detach the old task, cancels and waits with a finite timeout outside that lock, then reacquires the lock to install only the latest generation. The official browser thread posts immutable status snapshots back to the owning event loop and never holds a database lock while waiting for that loop.

`XianyuLive._instances` is the routing registry for live WebSocket-capable account instances used by message sending and the invitation bridge. Long-lived instances created by `cookie_manager.py` register by default. `POST /items/get-all-from-account` and `POST /items/get-by-page` create short-lived HTTP clients with `register_instance=False`, close their own sessions, and never replace the registered listener for the same account.

Authentication logging follows the same minimum-data boundary: logs must not include the default administrator password, email OTPs, reset grant IDs or tokens, full email addresses, or any submitted password. Operational messages use event type, redacted identifiers, and exception class where needed.

## AI Context Flow

Each reply is built in this order:

1. Safety restrictions.
2. Current product title, price, details, and product knowledge.
3. Enabled global rules plus enabled rules for the current product.
4. Local intent routing to bargain, technical, or default expert strategy.
5. Account-wide response style.

Production replies read only `published_json` from the current product knowledge profile. The training lab reads `draft_json` first, then falls back to the published version. Rules belonging to other products and disabled rules are reported but not injected.

After generation, the engine audits every applied rule. If any rule is marked violated, it asks the same configured provider to regenerate once with the violations made explicit, then audits again. The response metadata includes applied, excluded, and disabled rules, audit results, conflicts, and whether regeneration occurred. Contradictory rules still require human correction.

## Product Knowledge Lifecycle

Knowledge is scoped by `(cookie_id, item_id)`:

1. The seller writes a required plain-language overview.
2. AI combines that overview with the synchronized product title, price, and detail text to replace the complete current draft; failed generation leaves the previous draft intact.
3. Generated fields remain pending until the seller confirms or edits them.
4. Publishing stores an immutable version and copies the draft into the production snapshot.
5. Rollback restores a historical version.

Copying knowledge chooses the source draft, or the published snapshot when no draft exists, and replaces the selected target drafts. Target published snapshots and version history remain intact; copy never publishes target products.

## Order Synchronization

`order_sync_service.py` discovers seller orders through the direct MTOP seller feed and reconciles them with stored details. Accounts synchronize under a keyed lock; pagination is serial, bounded, delayed between pages, and retries only classified 429, network, and 5xx failures. A single-order refresh stops when the target appears. A successful envelope without a recognized list container is an invalid response, never an empty successful sync. Status text takes precedence over numeric codes, with longer phrases such as "waiting for buyer confirmation" evaluated before "confirm receipt".

Unknown or failed responses never overwrite a reliable stored status. `PUT /api/orders/{order_id}` changes only explicitly supplied local fields; platform refresh uses the structured `/refresh` or `/sync` path. Platform detail classifies the business type as `ordinary`, `lead`, or `unknown`. Automatic delivery and invitation fulfillment accept only a positively classified `ordinary` order with a current direct-API `pending_ship` result; `lead`, `unknown`, missing, mismatched, denied, or unconfirmed results fail closed. A real order-detail adapter remains unregistered until an authenticated canary verifies its response fields; DOM status guesses are never authoritative.

`orders.ordered_at_utc` is a saved platform order-time snapshot whose exact source is carried in `ordered_at_source`. It supports time-of-day analysis but is not asserted to be the payment, settlement, shipment, or completion timestamp.

Order item images use a direct-first, bounded-fallback chain. The browser first renders the order's CDN link with lazy loading, asynchronous decoding, and a no-referrer policy. Only a direct-load failure calls the authenticated `GET /api/orders/{order_id}/item-image` fallback. The client shares duplicate fallback requests for the same order, limits proxy downloads to four, caches successful object URLs and short-lived failures per authenticated session, and lets an explicit retry bypass the failure cache. The backend serves an existing private JPEG cache when possible; otherwise it validates and pins a trusted public source address, shares one in-flight download per image, limits external downloads to four, negative-caches failures, and moves Pillow transcoding off the event loop. Machine-readable failure reasons remain `not_saved`, `source_expired`, and `unsupported_format`.

Migration `2026072701` stores tenant-scoped verified item metric snapshots. Migration `2026072702` stores the three-canary state per user and account. Migration `2026072703` binds metric rows and state to account ownership and adds durable fulfillment attempts plus card reservations. Adapter batches are limited, timed out, and committed atomically; counter resets never become negative traffic and out-of-order snapshots are rejected. Counter deltas belong to the full interval between consecutive snapshots. The four-hour scheduler therefore reports approximate observation windows and never attributes an interval delta to one hour. The real adapter is unregistered by default, and the scheduler remains off until an account independently passes three live canaries.

Fulfillment persists `prepared` before inventory is used, moves to `sending` before the first irreversible platform or buyer-facing action, and reaches `committed` only after the complete quantity is acknowledged. A pre-send cancellation can reach `released`; any crash, partial send, or uncertain result after `sending` reaches `manual_review` and keeps its reservations out of the available inventory pool.

## Auto Delivery Delivery Center

The delivery center exposes three owner-scoped views: 商品配置 selects exactly one mode per item (`off`, `resource`, or `invite`), 资源库 manages fixed资料、一次一密、图片 and the fixed idempotent API v1 resource, and 发货记录 shows masked durable payload history. A newly synchronized item has no explicit mode and may use the legacy keyword rule only in that state. Once a seller chooses a mode, a missing, disabled, empty, out-of-stock, protocol-invalid, or specification-mismatched resource fails closed and never falls back to another card or keyword.

`2026082401` adds `cards.low_stock_threshold`, encrypted API-token fields, `item_info.delivery_mode`, and the owner-constrained `fulfillment_api_operations`, `fulfillment_delivery_payloads`, and `fulfillment_resend_events` tables. It is additive and leaves legacy API configurations readable as `manual_only`. Card stock continues to use `cards.data_content` and the existing reservation rows; TXT, CSV `secret` columns, and line imports trim blanks, remove duplicates against current stock and all historical reservations, and cap a batch at 10,000 entries with a 2,048-byte item limit.

The API resource accepts only HTTPS POST with a stable idempotency key. Its request body contains `action`, `idempotency_key`, `order_id`, `item_id`, `quantity`, and `spec`; buyer and Cookie data never enter the provider request. The server persists the key and configuration fingerprint before outbound I/O, replays only that same key for bounded network/408/429/5xx/pending outcomes (at most four attempts), accepts only the strict v1 response shape, and persists a successful item list before sending it. An unknown, conflicting, malformed, or quantity-mismatched result is `manual_review`; a failed result can release only pre-send reservations. Token plaintext is decrypted only at the outbound boundary and is absent from public cards, logs, records, and evidence.

Successful payloads are immutable. 原样重发 reads the committed payload, does not consume inventory or call the provider, and requires confirmation before the platform message path records `succeeded`, `failed`, or `ambiguous` by its `mid` ACK contract. A resource with an item binding or any fulfillment history can be disabled but cannot be hard-deleted.

## 邀请履约桥

邀请商品范围只由 `item_info.invite_auto_fulfillment` 的 `(cookie_id, item_id)` 开关决定，新同步商品默认关闭。独立邀请服务拥有确认页、兑换码、OAuth 账号、兑换和资格动作；邀请商品绕过本项目旧有的 `cards` 与 `delivery_rules`，避免同一订单扣减两套库存。

付款状态事件先由 `XianyuAutoAsync._verify_paid_order_for_delivery` 实时核验。平台订单号为数字时优先读取单订单详情；详情不可用时才在账号级同步锁内执行最多 5 页的订单列表回退。核验并落库后，付款事件调用 `invite_bridge_poller.scan_once(discover=False, trusted_order_ids={order_id})`，只处理本次目标订单并绕过批量平台发现使用的全局扫描锁。该信任只在本次调用内有效；普通后台扫描仍重新核验付款，`/internal/invite/send-message` 也保留独立付款复核和 ACK 门禁。

`invite_bridge.py` 提供 HMAC 鉴权的 `/internal/invite/*` 路由，并按调用方提供的 `operationKey` 持久化一条操作账本。消息操作复用账号主 WebSocket，`XianyuAutoAsync.send_msg` 在写入 `/r/MessageSend/sendByReceiverScope` 前先按请求 `mid` 注册 waiter。匹配响应明确成功时写 `succeeded`，平台明确拒绝时写 `failed`，响应缺失、连接中断或写入后超时时写 `ambiguous`。同一 operation key 重放只返回账本状态，不盲目再次发送文本。

跨项目顺序固定为：发现已付款订单 → 发送确认链接 → 买家确认 → 邀请服务锁码 → 兑换码和地址消息达到 `succeeded` → 确认发货或免拼发货达到 `succeeded` → 邀请服务把订单写为 `fulfilled`。`submitted`、`ambiguous` 与 `needs_review` 都不是送达证据，不能放行平台发货。

## AI Providers And Settings

`ai_provider_profiles` stores user-scoped provider profiles, encrypted API keys, the default model, cached model lists, and verification state. OpenAI-compatible providers use Chat Completions and `/models`; Gemini uses its native models list and `generateContent`. Accounts bind to a profile and select their own model. A provider/model change must generate a successful test reply before it can replace the active account configuration.

Inbound platform image messages are normalized by `utils/xianyu_message.py`. For an OpenAI-compatible profile whose selected model accepts vision input, the AI engine validates HTTPS CDN references, bounds image count/bytes/pixels, downloads the image, and sends it as an OpenAI-compatible `image_url` data part alongside the text prompt. Image-only messages bypass keyword matching and enter the AI path; text-only messages keep the existing request shape. Gemini and DashScope profiles keep their existing text path until a provider-specific multimodal contract is verified.

Administrator settings are split into global basic, AI, and SMTP sections. Ordinary users do not call the administrator summary: they read and update only `item_sync_enabled`, `item_sync_interval`, and `item_sync_max_pages` through typed user endpoints, with values stored in `user_settings` and global values used as defaults. AI provider profiles remain user-owned for every role. `settings_service.py` normalizes booleans and numbers, applies `keep/set/clear` secret actions, and returns only configuration state and masks. SMTP verification requires a valid independent support email. Sending the test code does not verify SMTP; only confirming the six-digit receipt code marks that exact settings fingerprint as verified.

仪表盘首先调用一个汇总接口。所有角色（含管理员）都只能读取通过自己 `user_id` 所有者 `cookies` 关联出的数据；接口始终报告 `scope: "user"`，不存在系统范围仪表盘。管理员权限只覆盖注册、用户和系统管理。响应包含计数、当前与上一周期分析、商品名称和显式 `trend_granularity`：单日范围携带稀疏的东八区 `hourly_stats`，多日范围使用 `daily_stats`。订单明细在汇总渲染后再加载，Recharts 依旧位于独立懒加载 chunk。分析查询使用时间边界而不是对 `created_at` 包裹 `DATE()`，以便 SQLite 使用迁移索引。

仪表盘默认范围为 `today`，表面是深色驾驶舱 hero，下面接浅色分析面板。Hero 显示核心营收、环比徽章（上一周期为零时保持中性）、营收/订单趋势和次级计数。今日绘图序列只保留到东八区当前小时，洞察序列排除未结束小时；跨日范围结束于今天时，洞察同样排除未结束日期，数据不足则显示 `--`。历史单日仍补齐 24 小时，多日按日补零。文档可见且网络在线时，客户端执行有界的 15 秒后台刷新；刷新失败保留最后一次成功汇总并显示延迟状态。`NumberFlow` 负责数字变化动画并遵守 reduced-motion 偏好。`frontend/components/ui/dashboardParts.tsx` 保存 `Dashboard.tsx`、`DashboardCharts.tsx` 和 `BusinessInsights.tsx` 共用的面板、标题、提示、横向占比条、HTML 排行、订单状态语义色、未命名商品兜底和金额/计数格式化器。

## Data Model

Core tables:

- `users`, `auth_sessions`: normalized backend identities, terms acceptance, active state, and persistent login sessions.
- `registration_invites`, `auth_challenges`, `auth_rate_events`: retained historical invite state, purpose-bound authentication and SMTP challenges, and persistent rate-limit events stored as digests.
- `schema_migrations`: ordered, transactional database migration history.
- `runtime_sessions`: safe ownership, status, TTL, and redacted errors for temporary operations.
- `client_browser_devices`, `account_renewal_bindings`, `client_renewal_tasks`: typed extension public devices (historic native-helper rows remain readable), extension-only renewal authorization, and one-time encrypted renewal delivery.
- `cookies`, `cookie_status`, `account_session_refresh_status`: Xianyu accounts, listener state, account-level scheduled refresh settings, and Cookie refresh state.
- `keywords`, `default_replies`, `item_replay`: deterministic reply rules.
- `ai_reply_settings`, `ai_provider_profiles`, `ai_conversations`, `ai_item_cache`: AI account configuration, providers, and context.
- `ai_training_rules`: global and product-scoped rules with enabled state.
- `ai_item_knowledge_profiles`, `ai_item_knowledge_versions`: knowledge draft, published snapshot, and version history.
- `cards`, `delivery_rules`, `orders`, `order_status_events`, `item_info`: inventory rules, synchronized orders and deferred status events, and products.
- `item_metric_snapshots`, `item_metric_collection_states`: verified metric history and account-scoped, default-off collection gates.
- `fulfillment_attempts`, `fulfillment_card_reservations`: durable delivery state and inventory reservations that survive process restarts.
- `fulfillment_api_operations`, `fulfillment_delivery_payloads`, `fulfillment_resend_events`: API idempotency state, immutable masked-delivery history, and resend outcomes, all constrained by user/account/attempt ownership.
- `notification_channels`, `message_notifications`, `risk_control_logs`: notification and risk-control records.
- `order_auto_ratings`: one durable seller-review state row per order, including schedule, pre-submit marker, result classification, and reconciliation state.
- Historical `skill_monitor_*` tables remain non-destructively in existing databases, but the Skill Center scheduler, page, and `/api/skills/*` routes are retired. `skill_agent_prompts` remains because AI reply strategy still reads it.

## Route Groups

- Public auth: `GET /api/auth/registration-config`, `POST /api/auth/captcha`, `POST /api/auth/email-code`, `POST /register`, `POST /api/auth/password-reset/verify-code`, `POST /api/auth/password-reset`, and username-or-email `POST /login`. The legacy `/send-verification-code` returns HTTP 410.
- Auth sessions: `/logout`, `/verify`, `/change-password`, `/change-admin-password`.
- Registration admin: `/api/admin/registration/status`, `/limit`, `/users`, and `/enabled`; ordinary users can be enabled or disabled without destructive deletion. Legacy `/invites` methods return HTTP 410.
- Account binding: web QR uses `/qr-login/*`; the server-browser fallback uses `/api/official-login/sessions`; extension devices use `/api/client-browser/devices`, `/sessions`, `/challenges`, `/import`, and session `confirm`/`cancel`. New native-helper registration/session surfaces are absent; historic source rows remain readable. Compatibility `/password-login*`, `/official-window-login*`, and `/cookies*` remain available; caller-supplied account IDs are not authoritative.
- Session refresh: `/api/accounts/{cookie_id}/session-status`, `/session-refresh`, `/session-refresh/cancel`, `/session-refresh/show-browser`, and `PUT /cookies/{cid}/cookie-refresh-settings`.
- Diagnostics: `/api/diagnostics/auto-reply/{cookie_id}` plus protected log and runtime-status endpoints.
- Settings: administrator-only `/api/settings/summary`, `/api/settings/sections/{section}`, `/api/settings/verify/{section}`, and user-owned `/api/settings/user-summary`, `/api/settings/user-basic`.
- AI providers: `/api/ai/providers*`, including model refresh and generated-reply tests.
- AI training: `/ai-reply-lab/*`, `/ai-training-rules/*`.
- Product knowledge: `/ai-item-knowledge/{cookie_id}/{item_id}/*`.
- Replies and inventory: `/keywords*`, `/default-replies*`, `/cards*`, `/cards/{id}/stock/import`, `/cards/{id}/api/validate`, `/delivery-rules*`, `/items*`, `/item-reply*`, item delivery mode routes (`PUT /items/{cookie_id}/{item_id}/delivery-mode`, `POST /items/delivery-modes/batch`), item delivery bindings, `/fulfillment-records`, and the item-level invitation switch.
- Orders and analytics: role-aware `GET /api/dashboard/summary`, structured `POST /api/orders/sync`, `/api/orders*`, order timing and buyer behavior, `/analytics/items/performance`, `/analytics/items/traffic`, and account-scoped metric status/manual canaries.
- Seller auto-rating: account-owned `PUT /cookies/{cid}/auto-rate`; task execution is internal to the single scheduler.

## Auto Delivery And Seller Auto-Rate Boundary

`item_info.delivery_mode` is the current source of truth for explicit product delivery: `off`, `resource`, or `invite`. Legacy `/items/{cookie_id}/{item_id}/delivery-binding` remains readable for older clients; its `card_id` binding maps to `resource`, while `null` maps to explicit `off`, not keyword fallback. Keyword delivery rules remain a compatibility fallback only while a newly synchronized item has no explicit mode. Product delivery and `invite_auto_fulfillment` are mutually exclusive, so enabling either clears the other instead of allowing two inventory owners.

Seller auto-rating is opt-in per account and defaults off. Discovery accepts only orders created after the switch was enabled and carrying the exact platform `RATE` action. Each new task receives a 5-15 minute delay, and one task at most is submitted per 60-second scheduler pass. AI produces one bounded, fact-conservative Chinese sentence; unavailable or rejected model output falls back to a fixed positive sentence.

Historical discovery is separate from the scheduler: `backfill_auto_rates.py` defaults to a SQLite `mode=ro` scan and never writes tasks or calls the review submitter. `db_manager.schedule_auto_rate_task(..., allow_historical=False)` preserves the enable-time boundary for all existing callers; only an explicit, complete `--apply` re-scan can set the historical override. Partial coverage, truncation, login errors, or unparseable timestamps stop the whole apply phase.

The merchant request uses `tradeIdList`. Success requires `data.module.success` and the target order in `successOrderIds`, with no matching `failOrderInfos` entry. Explicit rejection becomes `failed`; an inconclusive response or restart after the durable pre-POST marker becomes `needs_reconcile` and is not automatically retried. The current feature covers seller-to-buyer reviews only; buyer-to-seller review remains outside the active contract until one real write-and-readback canary proves it.

## Deployment Notes

The local workspace uses port `8091`; containers commonly expose `8080` through `PORT` or `API_PORT`. A Hugging Face Spaces export needs Docker frontmatter with `app_port: 8080`, but the GitHub README does not require that frontmatter.

`/health/live` proves the process can answer HTTP. `/health/ready` additionally checks SQLite and CookieManager readiness and reports the schema migration version plus a runtime-session summary. Responses carry `X-Request-ID`; HTTP error JSON keeps `detail` and adds `request_id`.

Set `WEB_CONCURRENCY=1`. Startup rejects values other than one because SQLite state and in-memory browser sessions are not shared between workers. SQL details default to DEBUG.

Production source maps are disabled unless `VITE_BUILD_SOURCEMAP=true`. The Vite retention plugin records successful asset generations and keeps only the current and previous generation. CI verifies that the entry chunk remains at least 30% smaller than the v1.1.0 baseline and that no unowned bundle remains after two builds.

Python runtime requirements are declared in `requirements.in` and locked in `requirements.lock`; development and build tools are declared separately in `requirements-dev.in` and `requirements-dev.lock`. `requirements.txt` remains a compatibility include for existing deployment commands.

Xianyu login remains environment-sensitive. Datacenter or overseas IPs can trigger Alibaba risk controls, and the official page currently rejects headless Chromium. Deployments that rely on automatic renewal must persist and back up `browser_data/` alongside SQLite and all locally generated encryption keys. Human verification remains an operator action; local binding or a trusted domestic host is generally more reliable.

The native helper is retired. Frontend builds come only from the maintained source tree and must contain Auto Delivery while excluding Skill Center, native-helper code, and user-facing “本机助手” wording. The installed production checkout is not a build source unless it first proves byte-equivalent to the maintained frontend.

Deployments that enable direct registration must also preserve `data/.system_secret_key` when `SYSTEM_SECRET_ENCRYPTION_KEY` is not supplied. Registration stays closed until an operator confirms the real SMTP receipt code and capacity remains; application health does not imply registration readiness.
