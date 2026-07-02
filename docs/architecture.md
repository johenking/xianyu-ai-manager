# Architecture

## System Shape

`xianyu-super-butler` is a FastAPI + SQLite + React/Vite application for Xianyu account operations, auto-reply, order handling, card delivery, and the added Skill Center.

Main runtime path:

1. `Start.py` checks database files, Playwright Chromium, frontend build output, and starts the API server.
2. `reply_server.py` owns FastAPI routes, authentication, admin APIs, Xianyu account APIs, order APIs, and Skill Center APIs.
3. `db_manager.py` owns SQLite schema creation, migrations, and persistence helpers.
4. `cookie_manager.py` starts per-account Xianyu message tasks through `XianyuAutoAsync.XianyuLive`.
5. `frontend/` builds React assets into `static/`; the backend serves the SPA and `/static/*`.

## Authentication

Backend login uses the `users` table. The initial admin user is `admin`; its initial password is read from `ADMIN_PASSWORD` during first database creation, and falls back to `admin123` if the environment variable is absent.

Successful login creates a bearer token in memory and persists it in `auth_sessions`. Tokens expire after 30 days. Frontend stores the token in `localStorage` as `auth_token` and verifies it with `/verify` on load. `/logout` removes both the in-memory and SQLite session.

## Data Model

Core tables:

- `users`: backend users and admin account.
- `auth_sessions`: persistent backend login sessions.
- `cookies`, `cookie_status`: Xianyu account cookies and enabled state.
- `keywords`, `default_replies`, `item_replay`: keyword/default/item-specific reply rules.
- `ai_reply_settings`, `ai_conversations`, `ai_item_cache`: AI reply configuration and conversation context.
- `cards`, `delivery_rules`, `orders`, `item_info`: inventory, delivery rules, order data, item data.
- `notification_channels`, `message_notifications`: notification destinations and per-account bindings.
- `risk_control_logs`: slider/captcha/verification events.

Skill Center tables:

- `skill_monitor_tasks`: monitor task definitions.
- `skill_monitor_results`: monitor output rows.
- `skill_agent_prompts`: per-user Prompt presets for AI expert replies.
- `skill_run_logs`: module run logs.

## Skill Center Design

The Skill Center is a safe rewrite of behavior inspired by three external projects, without directly copying their GPL/AGPL code:

- `Usagi-org/ai-goofish-monitor`: monitor task and result workflow.
- `shaxiu/XianyuAutoAgent`: multi-expert reply strategy and Prompt management.
- `GuDong2003/xianyu-auto-reply-fix`: deployment, browser, delivery, and stability diagnostics.

The implementation keeps the original app surface intact and adds a sidebar entry for `SkillCenter.tsx`. Existing account, order, keyword, card, and delivery APIs remain the source of truth.

## Route Groups

Auth and app shell:

- `GET /`, `/login`, `/register`, `/{path:path}`
- `POST /login`, `POST /logout`, `GET /verify`
- `POST /change-password`, `POST /change-admin-password`

Xianyu account login and cookies:

- `POST /qr-login/generate`, `GET /qr-login/check/{session_id}`
- `POST /password-login`, `GET /password-login/check/{session_id}`
- `GET /cookies/details`, `POST /cookies`, `PUT /cookies/{cid}`, `DELETE /cookies/{cid}`

Reply and inventory:

- `/keywords*`, `/default-replies*`, `/api/default-reply*`
- `/cards*`, `/delivery-rules*`, `/items*`, `/item-reply*`
- `/ai-reply-settings*`, `/ai-reply-test/{cookie_id}`

Orders and analytics:

- `/api/orders*`
- `/analytics/orders`, `/analytics/orders/valid`

Skill Center:

- `GET/POST /api/skills/monitor/tasks`
- `POST /api/skills/monitor/tasks/{task_id}/run`
- `GET /api/skills/monitor/results`
- `GET /api/skills/agent/prompts`
- `PUT /api/skills/agent/prompts/{prompt_type}`
- `POST /api/skills/agent/test-reply`
- `GET /api/skills/ops/health`
- `GET /api/skills/ops/browser-status`
- `GET /api/skills/ops/delivery-diagnostics`

## Deployment Notes

Local default port comes from `global_config.yml` and is set to `8091` in this workspace. Cloud platforms override with `PORT` or `API_PORT`; Hugging Face Spaces Docker expects `app_port: 8080` in `README.md` frontmatter.

Xianyu login is environment-sensitive. Cloud, overseas, or datacenter IPs can trigger Xianyu/Alibaba risk controls. Account binding is more reliable on a local machine or trusted domestic host; Cookie-based binding is often more stable than cloud QR login.
