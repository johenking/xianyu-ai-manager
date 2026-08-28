# Operator Runbook

> **⚠️ 生产位置已变更（2026-08-27）**：唯一活跃生产不再是本机 Mac 的 8091 LaunchAgent，而是云 HOST `app-suite-candidate` 上的 Docker 容器 `app-a-cloud-app-a-1`（回环 18091→容器 8080，release `/opt/app-suite-cloud/releases/cutover-20260827`，公网 `xianyu.cxywjx.top` / `xianyu-cloud.cxywjx.top` 走 VM 上的 cloudflared 隧道 `app-suite-cloud`）。当前发布方式：以运行中镜像为基底构建派生镜像（仅 COPY 任务文件）→ 更新 `manifest.env` → `docker compose -p app-a-cloud up -d --wait app-a` → 核验容器 healthy、18091 与公网双域名 readiness、迁移号、账号 listener 心跳；回滚脚本放同一 release 目录。拓扑与 ssh 入口见 `自动化/cloud-deploy/PROGRESS.md` 顶部「当前生产」。本手册以下 Mac 8091/LaunchAgent 章节仅适用于已冻结的本机回滚副本。

## Local Start

```bash
cd /path/to/xianyu-ai-manager
source .venv/bin/activate
python Start.py
```

Open `http://127.0.0.1:8091`. If tmux manages the service, inspect it without restarting:

```bash
tmux capture-pane -t xianyu-butler -p -S -200
```

## Current Mac Public Tunnel

On this Mac, `https://xianyu.cxywjx.top` routes through the existing Cloudflare Tunnel to `http://127.0.0.1:8091`. Verify the process path before claiming a release is deployed:

```bash
curl -sS https://xianyu.cxywjx.top/health/live
curl -sS https://xianyu.cxywjx.top/health/ready
lsof -nP -iTCP:8091 -sTCP:LISTEN
ps -axo pid,ppid,command | rg 'cloudflared|Start.py|uvicorn|xianyu'
curl -sS https://xianyu.cxywjx.top/ | rg 'static/assets/index-'
```

Inspect the command line of the process listening on port `8091` to identify the live runtime directory; do not infer it from the current shell. Preserve `data/`, `logs/`, `browser_data/`, `.venv/`, and `static/uploads/` during local deployments. Cloudflare can keep old hashed assets alive with `cf-cache-status: HIT`; if the public HTML points at the new entry bundle and local `/static/assets/<old>.js` is 404, the stale asset response is cache, not the running server.

The maintained source and installed runtime are separate trees. Build frontend assets only from `/Users/mac/Documents/咸鱼监控台/frontend`; reject candidates unless Auto Delivery is present and Skill Center, native-helper code, and user-facing “本机助手” wording are absent. Deploy only task-related backend files and verified static assets. Preserve production dirty changes and runtime data, keep static publication online, and reload the backend once only when new Python code must be loaded.

The current production schema includes product delivery binding (`2026081302`), seller auto-rating (`2026081601`), the delivery center (`2026082401`), and account L3 browser-memory flags (`2026082502`). Migration version alone does not prove the service or public bundle was upgraded. Before calling any revision deployed, verify the listening process path, health response, HTML entry bundle and every referenced asset, public page version, account listeners, and default-off external-write switches. Keep dated rollout evidence in `docs/handoff.md` rather than treating this checklist as proof.

### Tunnel 1033 / Zero Connections

Cloudflare `1033` means the edge cannot find a healthy connector. Separate the
origin and tunnel checks before changing application code:

```bash
curl -sS http://127.0.0.1:8091/health/ready
curl -sS http://127.0.0.1:20241/ready
curl -sS -o /dev/null -w '%{http_code}\n' https://xianyu.cxywjx.top/health/ready
```

The user-level `com.sub2api.cloudflared` LaunchAgent uses `--protocol auto` so
cloudflared can fall back between QUIC and HTTP/2 when the network changes. The
separate `com.cxywjx.cloudflared-watchdog` LaunchAgent samples the loopback
readiness endpoint every 60 seconds. After two consecutive zero-connection
samples it kickstarts only the cloudflared job, with a 180-second cooldown; it
never restarts Uvicorn or touches the local DNS/proxy listener.

Keep the existing `--dns-resolver-addrs` setting only while its local resolver
is running and forwarding Cloudflare traffic. If it is absent or unhealthy,
fix that resolver or remove only the flag and its address, preserving the
token-file arguments. Verify outbound TCP/UDP port `7844`; inbound port
forwarding is not part of the Tunnel path.

When changing the LaunchAgent, back up the plist, wait for the old process to
exit after `launchctl bootout`, then run `launchctl bootstrap` and verify both
the loopback readiness JSON and the public HTTP status. Do not run a second
cloudflared instance through Homebrew services while this LaunchAgent is loaded.

## Backup Before Risky Changes

Back up the live SQLite database before migrations, account identity changes, authentication deployments, or bulk data operations:

```bash
mkdir -p data/backups
STAMP=$(date +%Y%m%d-%H%M%S)
sqlite3 data/xianyu_data.db ".backup 'data/backups/xianyu_data_${STAMP}.db'"
sqlite3 "data/backups/xianyu_data_${STAMP}.db" "PRAGMA integrity_check;"
shasum -a 256 "data/backups/xianyu_data_${STAMP}.db"
```

Back up `data/.ai_provider_key`, `data/.account_credential_key`, and `data/.system_secret_key` with the database when their environment keys are not supplied. The system-secret key protects SMTP authorization codes and derives authentication HMAC digests; losing it prevents existing encrypted SMTP settings and one-time authentication records from being reused. Before replacing authentication code or profiles, stop the service and copy all of `browser_data/`; a live Chromium profile is not a reliable filesystem backup. The login service may best-effort remove only `.login_*`, `.window_*`, and `user_*.backup-*` directories older than six hours. Do not delete canonical or unmatched `user_*` profiles, unknown legacy directories, or fresh temporary directories because their identity or rollback value may not yet be reconciled.

## Auto Delivery Delivery Center

Use the maintained frontend at `/Users/mac/Documents/咸鱼监控台/frontend` and the single-worker runtime. The workbench is deliberately three pages: 商品配置, 资源库, and 发货记录. Resource types are fixed资料 (paste a link, extraction code, and instructions), 一次一密 (one value per line or CSV `secret` column), 图片, and 幂等 API. Empty resources cannot be created; an item mode is an atomic `off/resource/invite` choice, and an explicitly unavailable resource fails closed rather than falling back to a keyword or another resource.

For a one-time-secret replenishment, open 资源库 → resource detail, choose 逐行粘贴 or CSV, inspect the precheck count and duplicate count, then confirm. The server trims blanks and deduplicates against both current stock and historical reservations. Check `available/reserved/used/review/bound` after import. A partial batch response changes only successful rows; failed rows remain selected for correction.

API resources use only the fixed v1 contract: HTTPS POST, an encrypted Token, a strict `{status, operation_id, items[]}` response for allocation, and a stable `Idempotency-Key`. The UI stores Token with `SystemSecretCipher`, shows only a short mask, and disables 验证连接 until unsaved URL/spec/Token changes are saved. Do not paste a provider secret into logs, screenshots, curl output, or a fulfillment record.

When investigating delivery, check the item mode, resource health, `fulfillment_attempts`, `fulfillment_api_operations`, and `fulfillment_delivery_payloads` in that order. `prepared` may release only before any possible side effect; `sending`, `pending`, or an unknown provider result stays in manual review. 发货记录 displays masked payload history. 原样重发 requires the confirmation prompt, reuses the committed payload, never calls the provider again, and records the platform `mid` ACK as `succeeded`, `failed`, or `ambiguous`; an ambiguous result is never retried automatically.

The migration is `2026082401`. Before a production release, make an online SQLite backup and verify it independently:

```bash
sqlite3 data/xianyu_data.db ".backup 'data/backups/xianyu_data_delivery-center.db'"
sqlite3 data/backups/xianyu_data_delivery-center.db "PRAGMA integrity_check;"
curl -fsS http://127.0.0.1:8091/health/ready
curl -fsS https://xianyu.cxywjx.top/health/ready
lsof -nP -iTCP:8091 -sTCP:LISTEN
```

Deploy only the four task backend files and the maintained `static/` generation. Copy hashed assets first and switch `static/index.html` last; atomically replace the four Python files in the runtime directory, then perform one controlled LaunchAgent reload. Immediately verify the migration, SQLite integrity, one `8091` listener, both readiness endpoints, account listener log state, stable delivery/order counts, and absence of new traceback/5xx output. Keep the dated candidate, patch, verification record, and rollback unit together.

## Verification

```bash
source .venv/bin/activate
pip install -r requirements-dev.lock
python -m py_compile Start.py app_factory.py application_runtime.py api_routers.py auth_email_service.py auth_registration_service.py settings_service.py db_manager.py schema_migrations.py security_utils.py session_registry.py official_login_sessions.py repositories/auth_repository.py repositories/runtime_session_repository.py services/auth_service.py ai_provider_service.py ai_reply_engine.py auto_rate_service.py backfill_auto_rates.py account_session_refresh.py order_sync_service.py item_metric_service.py item_metric_scheduler.py invite_bridge.py invite_bridge_poller.py backfill_order_snapshots.py browser_extension_pairing.py cloudflared_watchdog.py reply_server.py XianyuAutoAsync.py utils/browser_interaction.py utils/xianyu_official_login.py utils/xianyu_session_probe.py utils/qr_login.py utils/qr_verification_browser.py utils/xianyu_l3_memory.py utils/outbound_http.py utils/outbound_smtp.py utils/outbound_dns.py utils/xianyu_message.py utils/xianyu_slider_stealth.py utils/verification_images.py
python -m pytest -q tests
ruff check .

cd frontend
npm audit --audit-level=high
npm run typecheck
npm test
npm run build
npm run build
npm run verify:build
```

The frontend build writes to `static/`. It keeps the current and previous successful asset generations and disables source maps unless `VITE_BUILD_SOURCEMAP=true`. A production build alone does not restart the backend.

The displayed frontend version comes from `frontend/package.json` through the Vite `__APP_VERSION__` define. Check the package version before building, then verify the built login, registration, password-recovery, terms, and privacy views all show the expected shared brand and version; a source edit without a matching public entry bundle is not a deployment.

Read the current schema from `/health/ready`; the latest production verification is migration `2026082502`. Existing fulfillment attempts and card reservations remain durable: a `sending` attempt found after restart, or any partial/uncertain send, stays `manual_review`; do not return its reservations to available inventory or mark the order shipped. Only a `prepared` attempt with no possible external side effect can be released.

The item-metric scheduler must remain stopped unless at least one account has independently completed three fresh real canaries and a verified adapter is registered. A duplicate or non-increasing `observed_at`, a counter reset, or an out-of-order snapshot does not advance the canary. `metric_adapter_unavailable` is the expected fail-closed response before that external acceptance; it is not evidence that traffic collection ran. Scheduled collection is approximately every four hours, so traffic deltas are observation-window totals between consecutive snapshots. Do not interpret the compatibility `hourly` field as one-hour traffic; use `observation_windows` and its duration metadata.

Order synchronization uses the direct seller-order MTOP feed. A partial response, `sync_limit_reached`, `status_unconfirmed`, `platform_permission_denied`, or `requires_login` must not be reported as a completed refresh. Automatic delivery and invitation fulfillment require both a positively classified `ordinary` order and a current direct API `pending_ship` result; `lead` or `unknown` orders fail closed and the system must not fall back to DOM text.

### 邀请桥发布与核验

邀请桥代码随唯一的 `com.cxywjx.xianyu-manager` LaunchAgent 进程加载。修改 `invite_bridge.py`、`invite_bridge_poller.py` 或 `XianyuAutoAsync.py` 后，先运行受影响测试和 `py_compile`，再重启现有 LaunchAgent；不要另外启动第二个 `Start.py`。重启后至少验证：

```bash
curl -fsS http://127.0.0.1:8091/health/ready
lsof -nP -iTCP:8091 -sTCP:LISTEN
python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8091/openapi.json')); print('/internal/invite/send-message' in data['paths'])"
```

消息账本语义固定如下：

- `succeeded`：已收到该消息 `mid` 对应的明确成功响应，可以由邀请服务继续平台发货；
- `failed`：平台明确拒绝或消息写入前确定失败，可以安全失败；
- `ambiguous/needs_review`：写入后超时、断线或结果未知，不重发消息，也不执行平台发货；
- `submitted`：仅表示旧路径曾写入，不是当前送达证据。

确认链接延迟应分段核对：付款状态事件到首次实时核验、订单落库到邀请服务接收、`send_confirmation` 开始到平台 ACK。买家点击确认后的 `send_fulfillment_message` 是另一条幂等消息，不要误判为确认链接重复发送。若前两段重新出现十秒级等待，先检查数字订单详情是否回退、账号同步锁和批量扫描是否占用；不要通过删除付款复核、幂等或 ACK 门禁换取表面速度。

真实验收前先确认所有启用邀请商品对应的卖家账号 listener 在线，且订单轮询已恢复；`manual_reauth_required` 必须由用户完成人工认证，不能靠重启绕过。随后只允许一笔受控订单，依次核对确认链接、兑换码和地址消息可见、消息操作 `succeeded`、平台发货操作 `succeeded`、闲鱼订单已发货，以及邀请服务本地订单 `fulfilled`。任一步未知时停止继续下单并保留两个项目的操作账本现场。

Basic smoke tests:

```bash
curl -sS http://127.0.0.1:8091/health

curl -sS -X POST http://127.0.0.1:8091/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<password>"}'
```

After login, verify settings and operations with a bearer token:

```bash
curl -sS http://127.0.0.1:8091/api/settings/summary \
  -H "Authorization: Bearer $TOKEN"

curl -sS 'http://127.0.0.1:8091/api/dashboard/summary?range=7days' \
  -H "Authorization: Bearer $TOKEN"
```

### 仪表盘实时刷新验收

仪表盘发布后，用已登录汇总接口检查图表契约：

```bash
curl -fsS 'http://127.0.0.1:8091/api/dashboard/summary?range=today' \
  -H "Authorization: Bearer $TOKEN" | jq '{trend_granularity, hourly_count: (.current.hourly_stats | length)}'
curl -fsS 'http://127.0.0.1:8091/api/dashboard/summary?range=7days' \
  -H "Authorization: Bearer $TOKEN" | jq '{trend_granularity, daily_count: (.current.daily_stats | length)}'
```

期望结果是：单日为 `hour`，服务端 `hourly_stats` 允许稀疏；多日为 `day` 并返回 `daily_stats`。界面默认选择“今天”，补零后只画到东八区当前小时，未来小时不展示，未结束小时/日期不参与峰谷和涨跌；历史单日仍保留完整 24 小时。数据不足时洞察显示 `--`。做 15 秒刷新探针时保持一个浏览器页签可见且在线。新付款快照会更新金额、数量、下单时间和明细；`refunding` 保留金额，`refunded` 扣除金额，`refund_cancelled` 恢复金额。后台请求失败时保留最后一次成功汇总，页头显示刷新延迟，不清空生产数据。

## Environment Variables

| Variable | Purpose |
|---|---|
| `ADMIN_PASSWORD` | Initial admin password, used only when creating a new database. |
| `JWT_SECRET_KEY` | Signs backend session tokens; use an independent random value. |
| `AI_PROVIDER_ENCRYPTION_KEY` | Encrypts provider API keys. If absent, a local key file is generated under `data/`. |
| `ACCOUNT_CREDENTIAL_ENCRYPTION_KEY` | Encrypts stored Xianyu login passwords with an independent key. |
| `SYSTEM_SECRET_ENCRYPTION_KEY` | Encrypts SMTP authorization codes and derives purpose-isolated authentication digests. |
| `PORT` | Cloud web port override. |
| `API_PORT` | Alternative web port used by `entrypoint.sh` and `Start.py`. |
| `API_HOST` | Bind host, usually `0.0.0.0` in containers. |
| `DB_PATH` | SQLite path, default `data/xianyu_data.db`. |
| `TZ` | Runtime timezone, usually `Asia/Shanghai`. |
| `PLAYWRIGHT_BROWSERS_PATH` | Playwright browser cache path. |
| `DOCKER_ENV` | Enables Linux/container Playwright handling. |
| `VITE_BUILD_SOURCEMAP` | Set to `true` only when a production source map is explicitly required. |

Do not commit secrets. Put deployment tokens, model keys, SMTP credentials, and Xianyu Cookies in platform secret stores or the Web UI.

## Direct Registration Rollout

Registration is disabled on new installations and is forced disabled by migration `2026071103`. Keep it closed while configuring the system:

1. In “系统与 AI”, use the QQ preset or enter the SMTP server, port, sender address, authorization code, TLS/SSL mode, and an independent public support email. QQ uses `smtp.qq.com:465`, SSL on, STARTTLS off.
2. Start SMTP verification. This saves the candidate configuration as unverified and sends a six-digit code to the support email.
3. Read the code from that real mailbox and enter it in the settings page within 10 minutes. Connection or send success alone is not acceptance.
4. Confirm the ordinary-user limit. The default is 20; the administrator is excluded and disabled ordinary users still count.
5. Open registration only after the status card reports receipt-confirmed SMTP and remaining capacity.
6. Complete one real registration, automatic login, service-restart session restore, username-or-email login, password reset, and old-session rejection before leaving registration open.

Changing any SMTP field invalidates the verified fingerprint, consumes pending SMTP challenges, and closes registration. The final available slot also closes registration automatically; increasing the limit requires a manual reopen. SMTP errors never generate a usable authentication challenge, and there is no third-party mail fallback.

After a registration or password-reset email is sent successfully, the public UI must not fetch another CAPTCHA immediately. Once the cooldown ends, the user must explicitly request a resend, solve the newly fetched CAPTCHA, and submit it before a second email can be sent.

The default authentication limits are 30 image CAPTCHAs per IP per hour; one email send per email per 60 seconds, five per email per hour, and 20 per IP per hour; five attempts per 10-minute challenge; five failed logins per account or IP in 15 minutes followed by a 15-minute cooldown; and 10 registration failures per IP per hour. HTTP 429 responses include `retry_after`.

`CF-Connecting-IP`, `X-Forwarded-For`, and `X-Real-IP` are ignored unless the direct peer belongs to the comma-separated IP/CIDR list in the `auth_trusted_proxies` system setting. Configure only proxies you operate; leaving the setting empty is safer than trusting arbitrary forwarded headers. Database rate events contain HMAC digests rather than raw addresses, emails, or account identifiers.

## Container And Hugging Face Deployment

Docker defaults to port `8080`:

```bash
cp .env.example .env
docker compose up --build -d
```

For a Hugging Face Spaces export, add Docker frontmatter to that export's README:

```yaml
sdk: docker
app_port: 8080
```

Persist and protect the database, logs, uploads, all three local encryption keys, and `browser_data/`. Exclude `.venv/`, `frontend/node_modules/`, `data/`, `browser_data/`, `logs/`, `backups/`, `.env`, and database files from source uploads.

The account QR panel recommends “Web QR” and offers “Server Chrome” as a fallback; opening the panel starts neither. Web QR keeps `/qr-login/*` independent and ordinary generation/scanning does not launch a browser. The server-browser fallback requires an authenticated console session and is exposed by the UI only on loopback and the formal production hostname; unfamiliar source/Host values are warning-only observations. The extension is the remote-device path. Switching modes or explicitly cancelling ends the matching session; hiding the panel does not stop polling. Automatic renewal is available only to password accounts with valid stored credentials. The current container entrypoint does not provide a real display, so do not claim Docker or cloud server-browser login works until the display/Xvfb and human-verification workflow have been tested there.

## AI And Knowledge Diagnostics

When a reply appears to ignore product facts:

1. Confirm the incoming conversation resolves to the expected `cookie_id` and `item_id`.
2. Open the product knowledge profile and distinguish draft from published state.
3. Remember that the training lab reads the draft, while production reads only the published snapshot.
4. Inspect the lab response's applied, excluded, and disabled rules.
5. Check `rule_audit` and `regenerated`; conflicting rules need manual cleanup.
6. Confirm the account's provider and model passed a generated-reply test.

For provider issues, refresh the profile model list and test the exact selected model. A failed test must not replace the account's active provider/model.

## Xianyu Session Troubleshooting

Symptoms include missing message Tokens, expired Cookies, passive `action_required`, active `verification_required`, or stable `manual_reauth_required`.

Recommended order:

1. Read `/api/accounts/{cookie_id}/session-status` and `/api/diagnostics/auto-reply/{cookie_id}`.
2. Confirm `cookies.xianyu_unb` and `cookies.login_method` are present. Only `password` plus a valid username and encrypted password supports automatic renewal; that path tries `browser_data/user_<unb>` first.
3. Keep the account listener running. In `action_required`, trigger `/session-refresh` exactly once; repeated Token or connection failures must not create a browser. In `manual_reauth_required`, the listener must remain in passive wait without WebSocket or Token-probe retries; use the returned `reauth_action` and do not keep calling refresh.
4. Use “网页二维码（推荐）” first. It creates no browser process. If the platform requests interactive verification, switch to the authenticated server-browser fallback where the UI exposes it, or to the extension on a remote device.
5. Keep `mobile_scan` verification as a scannable image. Slider, face, SMS, `interactive`, or unknown verification must move to the chosen browser path. Hiding the modal must not turn an unfinished login into success.
6. Check the account edit modal before enabling scheduled preventive refresh; it defaults to off, is disabled for non-password sources, and should use conservative intervals such as 24 hours or longer.
7. Use web QR, authenticated server-side Chrome, the separate extension, or a matching manual Cookie. Never update an existing record with a different Cookie `unb`; the API must return HTTP 409 `account_identity_mismatch`.

For a password-account manual-refresh acceptance check, keep the account's scheduled refresh disabled, click start once, and observe the same window through the full human step. It must remain open until the real message Token succeeds, the Cookie and actual browser User-Agent are saved, and one listener replacement finishes. Then observe processes and status for at least 15 minutes: there must be no later browser, validation popup, scheduled refresh, or immediate item-detail Playwright session. Active duplicate requests return the current status instead of queuing work.
8. Do not delete the account to re-login, because deletion removes account-linked configuration and knowledge.

Cloud, overseas, or datacenter IPs can trigger Xianyu/Alibaba risk control. Local binding or a trusted domestic host is generally more reliable than a free ephemeral runtime.

Do not switch the server-browser or legacy renewal browser to `headless=True`: Goofish currently returns an illegal-access page to headless Chromium. Do not override its User-Agent or add web-security/anti-detection flags. Invalid/missing credentials, identity mismatch, verification/login timeout, or official-page structure mismatch must persist `manual_reauth_required`; `profile_in_use`, temporary browser/probe failures, and cancellation remain retryable. Password login still depends on the current official page structure; when that flow breaks, use web QR, the extension, or matching Cookie recovery without deleting the account.

API QR expiry is an explicit terminal result. After the first `expired` response, repeated polling must return `status='expired'` and “二维码已过期，请重新扫码” for at least five minutes before the session becomes `not_found`; associated verification screenshots must be removed on schedule. The old `/qr-login/refresh-cookies`, `/qr-login/reset-cooldown/{cookie_id}`, and `/qr-login/cooldown-status/{cookie_id}` routes are intentionally removed.

### Listener Registry Isolation

`XianyuLive._instances` must contain the long-lived instance created by `cookie_manager.py`. Full and paginated product sync use temporary HTTP clients with `register_instance=False`; clicking sync or changing pages must not replace the account's WebSocket listener. Lock this contract with:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q \
  tests/test_item_sync_ownership.py tests/test_user_dashboard_access.py
```

When investigating `listener_unavailable`, do not use `runtime_sessions.total`: that registry counts expiring login, training, and refresh operations, not account listeners. Confirm one Uvicorn process on `8091`, inspect the affected account's listener task/state, record the current log byte offset, perform only the intended full or paginated sync, and require no new `listener_unavailable` or `Traceback` in the appended window. A backend release must also retain local/public readiness and a single `8091` listener.

## Notification Policy

普通客户聊天和自动发货成功（含多数量成功）保持静默；付款拦截、发货失败、处理异常和人工复核继续告警。Cookie/会话失效、人工重登和明确运行故障沿用现有一次性去重告警；重复检测不重复发送。该策略已随 2026-08-25 Bundle A 部署，当前生产 `XianyuAutoAsync.py` 只保留兼容方法定义，没有普通聊天或成功发货调用点。SMTP、QQ 邮箱配置、数据库结构和通知 API 不因该策略改变。

## Seller Auto-Rate Troubleshooting

The scheduler runs inside the one Uvicorn worker, scans every 60 seconds, and submits at most one task per pass. Keep `WEB_CONCURRENCY=1`; multiple processes can race on the same SQLite task state.

1. Read the account details returned by `/cookies/details`; confirm the owner-scoped `auto_rate_enabled` value and pending/success/failed/needs-reconcile counters.
2. The switch defaults off. Enabling requires a ready account identity and schedules only orders created afterward with the exact platform `RATE` action.
3. Normal due time is 5-15 minutes after discovery. AI configuration is optional; unavailable or rejected output uses a fixed positive sentence.
4. `failed` means the platform explicitly rejected the order. `needs_reconcile` means the write may have happened or the response was inconclusive; inspect the platform order once and never replay it automatically.
5. A restart before the durable pre-POST marker can reschedule the task; a restart after that marker moves it to `needs_reconcile`.
6. The active contract is seller-to-buyer only. Keep buyer-to-seller automation out of production until a real write-and-readback canary proves the platform flow.

### Historical Backfill (Read-Only First)

`backfill_auto_rates.py` is an operator tool, not part of the scheduler. Run it from the installed runtime directory with the production virtualenv:

```bash
cd "/Users/mac/Library/Application Support/XianyuManager"
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python backfill_auto_rates.py
```

The default run opens SQLite with `mode=ro`, scans 365 days with a 100-page/2000-order cap, reports only account slots and aggregate counts, and never imports the review submitter. Any session expiry, partial coverage, truncation, or unparseable order time sets `incomplete=true` and blocks the entire apply phase. Do not treat a candidate count as a completed review.

Only after every enabled account reports `complete=true` and the operator has an explicit submission confirmation may the same command be rerun with `--apply`. That mode re-scans first, inserts only untracked rateable orders through the unique key, assigns a 5-15 minute delay, and still does not submit a review directly. Repeating `--apply` should add zero rows. Keep the generated manifest and use the dated rollback script for code rollback; never reverse an already successful or ambiguous platform review automatically.

## Order Sync Troubleshooting

Use `POST /api/orders/sync` with `{"days":90}` to discover missing recent orders and reconcile delivery, completion, and refund states. Treat a 409 response with `requires_login` as an account-session problem, not as a successful zero-result sync. After restoring the existing account session, run the sync again and inspect each order's platform status text, sync source, last sync time, and last sync error.

## Logs And Sessions

```bash
tmux capture-pane -t xianyu-butler -p -S -500
rg -n "session-refresh|scheduled_cookie_refresh|auto.?rate|needs_reconcile|verification_required|qr-login|password-login|风控|验证码|captcha|登录失败|error|ERROR" realtime.log logs -S
```

Protected log APIs include `/logs`, `/logs/stats`, `/risk-control-logs`, and `/admin/logs`. Logs must not contain full Cookies, tokens, provider keys, verification URLs, stable account identities, the default administrator password, email OTPs, password-reset grant IDs or tokens, full email addresses, or any password. API request records use request IDs and matched route templates such as `/api/accounts/{cookie_id}/session-status`; raw Uvicorn access logging stays disabled so dynamic path values are not emitted a second time. Expected WebSocket disconnects are warning-level retry events without tracebacks, while unexpected failures retain only the exception class and sanitized summary.

After a deployment that changes logging, record the current log byte offsets, request one protected dynamic account route with a synthetic or already-authorized session, and scan only the newly appended bytes. Require zero matches for the stable account identity, Cookie and Token values, passwords, QR content, verification URLs, and `Traceback`. Do not print the searched values or matching lines into release notes or chat.

Backend login tokens live in `auth_sessions` for up to 30 days. If the dashboard logs out unexpectedly, check browser `localStorage.auth_token`, call `/verify`, confirm the same `DB_PATH` is in use, and verify that the session row still exists.

### Password Reset Acceptance

1. Keep the same ordinary user logged in in window A and sign in again in private window B.
2. Confirm two unexpired sessions exist for that user without printing their Token values.
3. Open `/forgot-password`, solve CAPTCHA, and request the email code. Confirm the successful send does not fetch another CAPTCHA; after cooldown, confirm an explicit resend requires a newly fetched CAPTCHA.
4. Enter the six-digit code and confirm the UI completes `POST /api/auth/password-reset/verify-code` before displaying the new-password fields. Do not print or persist the returned grant; the public UI must keep it only in component memory.
5. Submit the new password and confirm `POST /api/auth/password-reset` consumes the grant. A second use of the same grant must fail.
6. Refresh A and B. Both old sessions must return to login, and the database must show zero sessions for that user before the first new login.
7. Confirm the old password fails. Confirm the new password works once with the username and once with the email.
8. Confirm other users and the administrator were not logged out. Never place passwords, verification codes, reset grants, or full email addresses in logs, screenshots, shell history, or chat.

For an ordinary-user dashboard that does not finish loading, verify `/verify` returns `is_admin: false` and call `/api/dashboard/summary` with that user's Token. The page must not request `/admin/stats`; a 403 or 500 from the summary should end in a visible retry state. Migration `2026071104` adds `idx_orders_cookie_created_at` and `idx_orders_status_created_at`; verify them with `PRAGMA index_list(orders)` when summary latency regresses.

When account-level `cookie_refresh_enabled` is false, Token, Session, and connection failures must not launch Chrome. The refresh status should be passive `action_required` with `browser_active=false`; only the account page's explicit start action may launch the official browser.
