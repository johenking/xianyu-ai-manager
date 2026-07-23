# Architecture

## System Shape

Xianyu AI Manager is a FastAPI + SQLite + React/Vite application for Xianyu account operations, auto-reply, order handling, card delivery, product knowledge, and the Skill Center.

Main runtime path:

1. `Start.py` starts one Uvicorn worker using the `app_factory:create_app` factory.
2. `app_factory.py` owns FastAPI lifespan; `application_runtime.py` starts and stops the CookieManager, Skill Center scheduler, and browser pool on the server event loop.
3. `reply_server.py` keeps endpoint implementations compatible while `api_routers.py` groups all routes into auth, account, AI, order, skill, settings, content, admin, system, and frontend `APIRouter` domains.
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

`schema_migrations` records ordered migrations. A pending migration backs up the SQLite database plus the AI-provider, Xianyu-account, and system-secret local keys before starting one transaction. The compatibility upgrader keeps database version `1.6` idempotent while ordered migrations add the registration security schema, normalized identity indexes, `2026071104` order-analysis indexes, and `2026071701` persisted official-browser identity. A case-insensitive identity conflict aborts migration instead of merging users. Xianyu login passwords use an account-specific Fernet key that is separate from the AI provider and system-secret keys; account detail and status APIs never return the plaintext or ciphertext.

Xianyu accounts live in `cookies`. `cookies.xianyu_unb` stores the stable Xianyu identity extracted from the Cookie and is unique within a backend user. Re-login and Cookie updates use `(user_id, xianyu_unb)` to update the existing row instead of replacing its primary key. This preserves account-scoped AI settings, rules, knowledge, products, orders, and delivery data. Deleting an account remains destructive and is not a session-refresh mechanism.

`utils/qr_login.py` owns the default API QR path and renders the official `codeContent` locally; ordinary QR generation and scanning do not launch a browser. `utils/xianyu_official_login.py` owns visible SMS/password authentication, QR secondary verification, and password renewal with the installed system Chrome in headed mode. Background work only positions that window off-screen; the service does not override the browser User-Agent, inject anti-detection behavior, disable web security, or use a headless verification browser. New browser sessions use a temporary `.login_<uuid>` profile. Cookie presence is only a prerequisite: success also requires a real message `accessToken` from the shared session probe, expected-`unb` matching, and no active login or security surface. The browser's actual User-Agent is persisted with the Cookie and reused by Token and listener traffic. The validated browser remains open while Cookie persistence and the single listener replacement complete, then the temporary profile is atomically promoted to `browser_data/user_<unb>` while preserving the previous profile on promotion failure.

Cookie refresh state is persisted in `account_session_refresh_status` with `idle`, passive `action_required`, active `refreshing` and `verification_required`, stable `manual_reauth_required`, plus `success`, `failed`, `timeout`, and `cancelled`. Only password accounts with valid stored credentials support automatic renewal. Renewal reuses `browser_data/user_<unb>` first and submits decrypted credentials only after complete logout. Non-password sources and human-action password failures persist `manual_reauth_required`; later triggers return the matching CTA without launching Chrome. Manual requests atomically reserve the account before background scheduling, and duplicate requests return the active state without queuing. Listener replacement receives the already-probed Token, persisted browser User-Agent, renewal anchor, and item-sync anchor in one generation.

Goofish rejects Chromium headless mode as an illegal browser. Background renewal therefore uses a normal headed browser positioned off-screen. If Alibaba returns a verification address, it is kept only in memory and opened in that same official context. The service stores only a safe screenshot and keeps polling the page, `unb`, Cookie set, and message Token for up to 15 minutes. `browser_active` is computed from the live Worker; only `verification_required` with `browser_active=true` exposes show/cancel controls. The UI does not ask the user to confirm completion because the backend continues automatically.

`official_login_sessions.py` owns the unified visible SMS/password state machine and its QR-compatible mode: `preparing`, `waiting_user`, `verification_required`, `persisting`, `restarting_listener`, and the terminal states `success`, `expired`, `failed`, `cancelled`, or `interrupted`. One background task performs completion exactly once; status reads have no lifecycle side effects. Temporary login, AI training, and Cookie refresh operations share a Session Registry. `runtime_sessions` stores only session type, owner, account identifier, state, redacted error, and TTL. Polling another user's session returns HTTP 403. Passwords are passed only to the background task and are never stored in login dictionaries or the registry. Cookies, Tokens, QR content, verification URLs, Playwright objects, and AI conversation content are also excluded. On restart, active browser-backed records become `interrupted` and the UI asks the operator to start again.

CookieManager listener replacement uses a short account-lock section to detach the old task, cancels and waits with a finite timeout outside that lock, then reacquires the lock to install only the latest generation. The official browser thread posts immutable status snapshots back to the owning event loop and never holds a database lock while waiting for that loop.

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
2. AI combines that overview with the synchronized product title, price, and detail text to produce a structured draft.
3. Generated fields remain pending until the seller confirms or edits them.
4. Publishing stores an immutable version and copies the draft into the production snapshot.
5. Rollback restores a historical version.

Copying knowledge chooses the source draft, or the published snapshot when no draft exists, and writes it only to target drafts. The default is no overwrite. Copy never publishes target products.

## Order Synchronization

`order_sync_service.py` discovers seller orders from the recent paginated platform feed and reconciles them with stored details. The default window is 90 days. Status text takes precedence over numeric codes, so signed orders become `completed`, active refunds become `refunding`, successful refunds become `refunded`, and closed refund requests become `refund_cancelled`.

Unknown or failed responses never overwrite a reliable stored status. Shipped, completed, refunding, and legacy closed orders remain eligible for detail checks because completion can later move to refund. Session expiry stops that account's sync with `requires_login` instead of counting the attempt as success. Unmatched status events are persisted in `order_status_events` and reconciled when the corresponding order is later discovered.

## AI Providers And Settings

`ai_provider_profiles` stores user-scoped provider profiles, encrypted API keys, the default model, cached model lists, and verification state. OpenAI-compatible providers use Chat Completions and `/models`; Gemini uses its native models list and `generateContent`. Accounts bind to a profile and select their own model. A provider/model change must generate a successful test reply before it can replace the active account configuration.

Administrator settings are split into global basic, AI, and SMTP sections. Ordinary users do not call the administrator summary: they read and update only `item_sync_enabled`, `item_sync_interval`, and `item_sync_max_pages` through typed user endpoints, with values stored in `user_settings` and global values used as defaults. AI provider profiles remain user-owned for every role. `settings_service.py` normalizes booleans and numbers, applies `keep/set/clear` secret actions, and returns only configuration state and masks. SMTP verification requires a valid independent support email. Sending the test code does not verify SMTP; only confirming the six-digit receipt code marks that exact settings fingerprint as verified.

The dashboard first calls one role-aware summary endpoint. Ordinary users receive only rows joined through their owned `cookies`; administrators receive system scope. The response combines counters, current and previous analytics periods, and product names. Order detail loading starts after the summary cards render, and the Recharts dependency lives in a separate lazy chunk. Analytics use timestamp boundaries instead of wrapping `created_at` in `DATE()` so SQLite can use the migration indexes.

## Data Model

Core tables:

- `users`, `auth_sessions`: normalized backend identities, terms acceptance, active state, and persistent login sessions.
- `registration_invites`, `auth_challenges`, `auth_rate_events`: retained historical invite state, purpose-bound authentication and SMTP challenges, and persistent rate-limit events stored as digests.
- `schema_migrations`: ordered, transactional database migration history.
- `runtime_sessions`: safe ownership, status, TTL, and redacted errors for temporary operations.
- `cookies`, `cookie_status`, `account_session_refresh_status`: Xianyu accounts, listener state, account-level scheduled refresh settings, and Cookie refresh state.
- `keywords`, `default_replies`, `item_replay`: deterministic reply rules.
- `ai_reply_settings`, `ai_provider_profiles`, `ai_conversations`, `ai_item_cache`: AI account configuration, providers, and context.
- `ai_training_rules`: global and product-scoped rules with enabled state.
- `ai_item_knowledge_profiles`, `ai_item_knowledge_versions`: knowledge draft, published snapshot, and version history.
- `cards`, `delivery_rules`, `orders`, `order_status_events`, `item_info`: inventory, delivery, synchronized orders and deferred status events, and products.
- `notification_channels`, `message_notifications`, `risk_control_logs`: notification and risk-control records.
- `skill_monitor_tasks`, `skill_monitor_results`, `skill_agent_prompts`, `skill_run_logs`: Skill Center schedules, run state, deduplicated results, expert prompts, and audit logs.

## Route Groups

- Public auth: `GET /api/auth/registration-config`, `POST /api/auth/captcha`, `POST /api/auth/email-code`, `POST /register`, `POST /api/auth/password-reset/verify-code`, `POST /api/auth/password-reset`, and username-or-email `POST /login`. The legacy `/send-verification-code` returns HTTP 410.
- Auth sessions: `/logout`, `/verify`, `/change-password`, `/change-admin-password`.
- Registration admin: `/api/admin/registration/status`, `/limit`, `/users`, and `/enabled`; ordinary users can be enabled or disabled without destructive deletion. Legacy `/invites` methods return HTTP 410.
- Account binding: `POST /api/official-login/sessions`, `GET /api/official-login/sessions/{session_id}`, and session `show-browser`/`cancel`; compatibility `/qr-login/*`, `/password-login*`, and `/cookies*` remain available. Password login accepts `account`, `password`, and `show_browser`; caller-supplied account IDs are not authoritative.
- Session refresh: `/api/accounts/{cookie_id}/session-status`, `/session-refresh`, `/session-refresh/cancel`, `/session-refresh/show-browser`, and `PUT /cookies/{cid}/cookie-refresh-settings`.
- Diagnostics: `/api/diagnostics/auto-reply/{cookie_id}` and `/api/skills/ops/*`.
- Settings: administrator-only `/api/settings/summary`, `/api/settings/sections/{section}`, `/api/settings/verify/{section}`, and user-owned `/api/settings/user-summary`, `/api/settings/user-basic`.
- AI providers: `/api/ai/providers*`, including model refresh and generated-reply tests.
- AI training: `/ai-reply-lab/*`, `/ai-training-rules/*`.
- Product knowledge: `/ai-item-knowledge/{cookie_id}/{item_id}/*`.
- Replies and inventory: `/keywords*`, `/default-replies*`, `/cards*`, `/delivery-rules*`, `/items*`, `/item-reply*`.
- Orders and analytics: role-aware `GET /api/dashboard/summary`, `POST /api/orders/sync`, `/api/orders*`, `/analytics/orders*`.
- Skill Center: `/api/skills/monitor/*`, `/api/skills/agent/*`, `/api/skills/ops/*`.

## Skill Center Boundary

The Skill Center is an independent safe rewrite informed by monitor workflow, expert-strategy, and diagnostics ideas from the projects named in `NOTICE`. Manual searches and scheduled tasks share `execute_skill_monitor_task`, so the database running flag prevents overlapping runs. The single-worker scheduler polls every 30 seconds, starts only enabled due tasks, resets interrupted `running` rows during startup, and computes the next run after success or failure. Schedules default off and accept intervals of at least 15 minutes.

Rule-matched items can pass through the account's configured AI provider. Missing AI configuration fails the run instead of silently accepting items. Accepted results are deduplicated across runs by `(task_id, user_id, item_url)`, with `raw_data.item_id` as fallback when no URL exists.

Skill notifications use enabled Webhook, WeChat, DingTalk, Feishu, Bark, or Telegram channels. Every supported channel is attempted. Results persist `sent` when all succeed, `partial` when only some succeed, and `failed` when all fail; disabled or missing-channel states remain explicit. QQ and email may exist elsewhere in the notification schema but are not advertised as Skill Center delivery channels.

## Deployment Notes

The local workspace uses port `8091`; containers commonly expose `8080` through `PORT` or `API_PORT`. A Hugging Face Spaces export needs Docker frontmatter with `app_port: 8080`, but the GitHub README does not require that frontmatter.

`/health/live` proves the process can answer HTTP. `/health/ready` additionally checks SQLite and CookieManager readiness and reports the schema migration version plus a runtime-session summary. Responses carry `X-Request-ID`; HTTP error JSON keeps `detail` and adds `request_id`.

Set `WEB_CONCURRENCY=1`. Startup rejects values other than one because SQLite state and in-memory browser sessions are not shared between workers. SQL details default to DEBUG.

Production source maps are disabled unless `VITE_BUILD_SOURCEMAP=true`. The Vite retention plugin records successful asset generations and keeps only the current and previous generation. CI verifies that the entry chunk remains at least 30% smaller than the v1.1.0 baseline and that no unowned bundle remains after two builds.

Python runtime requirements are declared in `requirements.in` and locked in `requirements.lock`; development and build tools are declared separately in `requirements-dev.in` and `requirements-dev.lock`. `requirements.txt` remains a compatibility include for existing deployment commands.

Xianyu login remains environment-sensitive. Datacenter or overseas IPs can trigger Alibaba risk controls, and the official page currently rejects headless Chromium. Deployments that rely on automatic renewal must persist and back up `browser_data/` alongside SQLite and all locally generated encryption keys. Human verification remains an operator action; local binding or a trusted domestic host is generally more reliable.

Deployments that enable direct registration must also preserve `data/.system_secret_key` when `SYSTEM_SECRET_ENCRYPTION_KEY` is not supplied. Registration stays closed until an operator confirms the real SMTP receipt code and capacity remains; application health does not imply registration readiness.
