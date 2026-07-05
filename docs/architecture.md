# Architecture

## System Shape

Xianyu AI Manager is a FastAPI + SQLite + React/Vite application for Xianyu account operations, auto-reply, order handling, card delivery, product knowledge, and the Skill Center.

Main runtime path:

1. `Start.py` starts one Uvicorn worker using the `app_factory:create_app` factory.
2. `app_factory.py` owns FastAPI lifespan; `application_runtime.py` starts and stops the CookieManager and browser pool on the server event loop.
3. `reply_server.py` keeps endpoint implementations compatible while `api_routers.py` groups all routes into auth, account, AI, order, skill, settings, content, admin, system, and frontend `APIRouter` domains.
4. `db_manager.py` retains the compatibility persistence facade. New domain SQL starts in `repositories/`, while domain decisions live in `services/`; authentication is the first extracted boundary.
5. `cookie_manager.py` starts one `XianyuAutoAsync.XianyuLive` task per enabled account and exposes an awaited shutdown path.
6. `ai_reply_engine.py` assembles product-scoped context, calls the selected provider, audits rules, and optionally regenerates once.
7. `frontend/` lazy-loads business pages, exposes domain API/type modules through compatibility barrels, and builds React assets into `static/`; FastAPI serves the SPA and `/static/*`.

The deployment model is intentionally one process, one Uvicorn worker, one asyncio event loop, and SQLite. It does not claim horizontal multi-worker support.

## Authentication And Account Identity

Backend users live in `users`. The initial `admin` password is read from `ADMIN_PASSWORD` only when a new database creates the user. New passwords use bcrypt cost 12; a successful legacy SHA-256 login upgrades the stored hash. New login Sessions store a Token digest, expire after 30 days, and are removed by `/logout`; legacy records remain readable during migration.

`schema_migrations` records ordered migrations. A pending migration backs up the SQLite database and local encryption keys before starting one transaction. Xianyu login passwords use an account-specific Fernet key that is separate from the AI provider key.

Xianyu accounts live in `cookies`. `cookies.xianyu_unb` stores the stable Xianyu identity extracted from the Cookie and is unique within a backend user. Re-login and Cookie updates use `(user_id, xianyu_unb)` to update the existing row instead of replacing its primary key. This preserves account-scoped AI settings, rules, knowledge, products, orders, and delivery data. Deleting an account remains destructive and is not a session-refresh mechanism.

Cookie refresh state is persisted in `account_session_refresh_status` with states such as `idle`, `refreshing`, `verification_required`, `success`, `failed`, `timeout`, and `cancelled`. Token failure can trigger an immediate refresh; enabled accounts also perform preventive refresh attempts. When Alibaba requires human verification, the account page reads the persisted status and verification image instead of pretending refresh succeeded.

Temporary QR login, password login, AI training, and Cookie refresh operations share a Session Registry. `runtime_sessions` stores only session type, owner, account identifier, state, redacted error, and TTL. Passwords, Cookies, Tokens, complete verification URLs, Playwright objects, and AI conversation content remain in memory. On restart, active browser-backed records become `interrupted` and the UI must ask the operator to start again.

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

System settings are split into basic, AI, and SMTP sections. `settings_service.py` normalizes booleans and numbers, applies `keep/set/clear` secret actions, and returns only configuration state and masks. SMTP verification authenticates without sending mail.

## Data Model

Core tables:

- `users`, `auth_sessions`: backend identities and persistent login sessions.
- `schema_migrations`: ordered, transactional database migration history.
- `runtime_sessions`: safe ownership, status, TTL, and redacted errors for temporary operations.
- `cookies`, `cookie_status`, `account_session_refresh_status`: Xianyu accounts, listener state, and Cookie refresh state.
- `keywords`, `default_replies`, `item_replay`: deterministic reply rules.
- `ai_reply_settings`, `ai_provider_profiles`, `ai_conversations`, `ai_item_cache`: AI account configuration, providers, and context.
- `ai_training_rules`: global and product-scoped rules with enabled state.
- `ai_item_knowledge_profiles`, `ai_item_knowledge_versions`: knowledge draft, published snapshot, and version history.
- `cards`, `delivery_rules`, `orders`, `order_status_events`, `item_info`: inventory, delivery, synchronized orders and deferred status events, and products.
- `notification_channels`, `message_notifications`, `risk_control_logs`: notification and risk-control records.
- `skill_monitor_tasks`, `skill_monitor_results`, `skill_agent_prompts`, `skill_run_logs`: Skill Center state.

## Route Groups

- Auth: `/login`, `/logout`, `/verify`, `/change-password`, `/change-admin-password`.
- Account binding: `/qr-login/*`, `/password-login/*`, `/cookies*`.
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

The Skill Center is an independent safe rewrite informed by monitor workflow, expert-strategy, and diagnostics ideas from the projects named in `NOTICE`. Manual product searches, expert prompts, and diagnostics are functional. Scheduled monitoring, AI monitor filtering, and notification delivery are explicitly unavailable and must not be represented as queued or successful.

## Deployment Notes

The local workspace uses port `8091`; containers commonly expose `8080` through `PORT` or `API_PORT`. A Hugging Face Spaces export needs Docker frontmatter with `app_port: 8080`, but the GitHub README does not require that frontmatter.

`/health/live` proves the process can answer HTTP. `/health/ready` additionally checks SQLite and CookieManager readiness and reports the schema migration version plus a runtime-session summary. Responses carry `X-Request-ID`; HTTP error JSON keeps `detail` and adds `request_id`.

Set `WEB_CONCURRENCY=1`. Startup rejects values other than one because SQLite state and in-memory browser sessions are not shared between workers. SQL details default to DEBUG.

Production source maps are disabled unless `VITE_BUILD_SOURCEMAP=true`. The Vite retention plugin records successful asset generations and keeps only the current and previous generation. CI verifies that the entry chunk remains at least 30% smaller than the v1.1.0 baseline and that no unowned bundle remains after two builds.

Python runtime requirements are declared in `requirements.in` and locked in `requirements.lock`; development and build tools are declared separately in `requirements-dev.in` and `requirements-dev.lock`. `requirements.txt` remains a compatibility include for existing deployment commands.

Xianyu login remains environment-sensitive. Datacenter or overseas IPs and headless browser fingerprints can trigger Alibaba risk controls. Human verification cannot be bypassed; local binding or a trusted domestic host is generally more reliable.
