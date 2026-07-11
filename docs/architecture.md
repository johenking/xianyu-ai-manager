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
7. `frontend/` lazy-loads business pages, exposes domain API/type modules through compatibility barrels, and builds React assets into `static/`; FastAPI serves the SPA and `/static/*`.

The deployment model is intentionally one process, one Uvicorn worker, one asyncio event loop, and SQLite. It does not claim horizontal multi-worker support.

## Authentication And Account Identity

Backend users live in `users`. The initial `admin` password is read from `ADMIN_PASSWORD` only when a new database creates the user. New passwords use bcrypt cost 12; a successful legacy SHA-256 login upgrades the stored hash. New login Sessions store a Token digest, expire after 30 days, and are removed by `/logout`; legacy records remain readable during migration.

Usernames are NFKC-normalized and emails are lowercased for case-insensitive uniqueness. Ordinary users carry active state plus the accepted terms version and timestamp. `/login` resolves one identifier as either username or email. Disabling a user or resetting a password deletes all persisted sessions for that user and removes matching in-memory sessions.

Direct registration is fail-closed. `registration_enabled` defaults to false and migration `2026071103` forces it false, sets agreement `v2`, installs a default ordinary-user limit of 20, and consumes invitation-era registration challenges. Readiness requires a valid support email, receipt-code confirmation for the exact current SMTP fingerprint, and remaining ordinary-user capacity. Disabled ordinary users count toward capacity; the administrator does not. A `BEGIN IMMEDIATE` registration transaction rechecks the switch and capacity, validates the purpose-bound email challenge, normalized identities, password policy, and terms version, then creates the user and consumes the challenge. Filling the last slot closes the switch in the same transaction.

`registration_invites` remains as historical, non-destructively retained data; runtime invite creation, listing, revocation, and consumption are retired. `auth_challenges` stores digests for image CAPTCHA, registration email, password-reset email, and SMTP receipt-code secrets, with expiry, attempt, and consumption state. `auth_rate_events` stores HMAC digests for IP, email, and account dimensions. Forwarded client addresses are considered only when the direct peer is in the `auth_trusted_proxies` setting.

`auth_email_service.py` is the only authentication-mail path. It sends through the configured SMTP server and has no third-party fallback. SMTP authorization codes are encrypted by `SystemSecretCipher`; the same independent system secret derives purpose-separated HMAC keys. SMTP verification first saves the configuration as unverified and sends a six-digit code to the support mailbox. Confirmation binds that code to the current fingerprint; changing any SMTP field invalidates verification, consumes pending SMTP challenges, and closes registration. Authentication-code delivery uses the same public response and SMTP path for eligible and decoy targets so account existence is not exposed.

`schema_migrations` records ordered migrations. A pending migration backs up the SQLite database plus the AI-provider, Xianyu-account, and system-secret local keys before starting one transaction. The compatibility upgrader keeps database version `1.6` idempotent while ordered migrations add the registration security schema and normalized identity indexes. A case-insensitive identity conflict aborts migration instead of merging users. Xianyu login passwords use an account-specific Fernet key that is separate from the AI provider and system-secret keys; account detail and status APIs never return the plaintext or ciphertext.

Xianyu accounts live in `cookies`. `cookies.xianyu_unb` stores the stable Xianyu identity extracted from the Cookie and is unique within a backend user. Re-login and Cookie updates use `(user_id, xianyu_unb)` to update the existing row instead of replacing its primary key. This preserves account-scoped AI settings, rules, knowledge, products, orders, and delivery data. Deleting an account remains destructive and is not a session-refresh mechanism.

`utils/xianyu_official_login.py` owns official browser authentication. Initial password login uses a temporary `.login_<uuid>` profile, switches the embedded official page from SMS to password mode, confirms the agreement and keep-login prompts, and accepts success only when login and security surfaces disappear while both `unb` and a session Cookie exist. The profile is then promoted to `browser_data/user_<unb>`; an existing target is backed up and restored if replacement fails.

Cookie refresh state is persisted in `account_session_refresh_status` with states such as `idle`, `refreshing`, `verification_required`, `success`, `failed`, `timeout`, and `cancelled`. Manual refresh, scheduled refresh, Token failure, and repeated connection-failure recovery all call the same official service. It first opens the canonical `unb` profile and seeds the current Cookie when needed, then falls back to encrypted credentials only if the official profile has logged out. A returned `unb` must match the expected account before CookieManager is updated once.

Goofish rejects Chromium headless mode as an illegal browser. Background renewal therefore uses a normal headed browser positioned off-screen. If Alibaba requires human verification, the service reopens the same profile visibly, stores only a safe verification screenshot, and waits up to 15 minutes. The account page reads the persisted status and image; verification URLs are not exposed and verification is never treated as bypassed.

Temporary QR login, password login, AI training, and Cookie refresh operations share a Session Registry. `runtime_sessions` stores only session type, owner, account identifier, state, redacted error, and TTL. The password-login task receives credentials only as its background-task arguments; the password is not stored in the login-session dictionaries or registry. Cookies, Tokens, verification URLs, Playwright objects, and AI conversation content are also excluded. On restart, active browser-backed records become `interrupted` and the UI must ask the operator to start again.

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

System settings are split into basic, AI, and SMTP sections. `settings_service.py` normalizes booleans and numbers, applies `keep/set/clear` secret actions, and returns only configuration state and masks. SMTP verification requires a valid independent support email. Sending the test code does not verify SMTP; only confirming the six-digit receipt code marks that exact settings fingerprint as verified.

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

- Public auth: `GET /api/auth/registration-config`, `POST /api/auth/captcha`, `POST /api/auth/email-code`, `POST /register`, `POST /api/auth/password-reset`, and username-or-email `POST /login`. The legacy `/send-verification-code` returns HTTP 410.
- Auth sessions: `/logout`, `/verify`, `/change-password`, `/change-admin-password`.
- Registration admin: `/api/admin/registration/status`, `/limit`, `/users`, and `/enabled`; ordinary users can be enabled or disabled without destructive deletion. Legacy `/invites` methods return HTTP 410.
- Account binding: `/qr-login/*`, `POST /password-login`, `GET /password-login/check/{session_id}`, and `/cookies*`, including `PUT /cookies/{cid}/cookie-refresh-settings`. Password login accepts `account`, `password`, and `show_browser`; caller-supplied account IDs are not authoritative.
- Session refresh: `/api/accounts/{cookie_id}/session-status`, `/session-refresh`, `/session-refresh/cancel`.
- Diagnostics: `/api/diagnostics/auto-reply/{cookie_id}` and `/api/skills/ops/*`.
- Settings: `/api/settings/summary`, `/api/settings/sections/{section}`, `/api/settings/verify/{section}`.
- AI providers: `/api/ai/providers*`, including model refresh and generated-reply tests.
- AI training: `/ai-reply-lab/*`, `/ai-training-rules/*`.
- Product knowledge: `/ai-item-knowledge/{cookie_id}/{item_id}/*`.
- Replies and inventory: `/keywords*`, `/default-replies*`, `/cards*`, `/delivery-rules*`, `/items*`, `/item-reply*`.
- Orders and analytics: `POST /api/orders/sync`, `/api/orders*`, `/analytics/orders*`.
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

Xianyu login remains environment-sensitive. Datacenter or overseas IPs can trigger Alibaba risk controls, and the official page currently rejects headless Chromium. Deployments that rely on automatic renewal must persist and back up `browser_data/` alongside SQLite and the account credential key. Human verification cannot be bypassed; local binding or a trusted domestic host is generally more reliable.

Deployments that enable direct registration must also preserve `data/.system_secret_key` when `SYSTEM_SECRET_ENCRYPTION_KEY` is not supplied. Registration stays closed until an operator confirms the real SMTP receipt code and capacity remains; application health does not imply registration readiness.
