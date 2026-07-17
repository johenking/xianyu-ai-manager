# AGENTS

## Project

This is a FastAPI + SQLite + React/Vite application for Xianyu account operations. The backend serves the React SPA from `static/`; `frontend/` is the frontend source. The runtime intentionally uses one process and one Uvicorn worker.

## High-Signal Commands

```bash
source .venv/bin/activate
ruff check .
python -m py_compile Start.py app_factory.py application_runtime.py api_routers.py settings_service.py db_manager.py schema_migrations.py security_utils.py session_registry.py official_login_sessions.py skill_monitor_scheduler.py reply_server.py XianyuAutoAsync.py utils/xianyu_official_login.py utils/xianyu_session_probe.py
python -m unittest discover -s tests -v

cd frontend
npm audit --audit-level=high
npm run typecheck
npm test
npm run build
npm run build
npm run verify:build
```

The local service URL is `http://127.0.0.1:8091`. On the current Mac, `https://xianyu.cxywjx.top` tunnels to that service. Before claiming a deployment, verify the listening process path, local and public health, the HTML entry bundle, and every referenced asset.

## Important Files

| Path | Purpose |
|---|---|
| `Start.py` | Startup checks and API launch. |
| `application_runtime.py` | Lifespan-owned CookieManager and scheduler startup/shutdown. |
| `reply_server.py` | FastAPI routes and auth/session integration. |
| `official_login_sessions.py` | Owner-scoped official-login state machine and completion single-flight. |
| `utils/xianyu_official_login.py` / `utils/xianyu_session_probe.py` | Official browser flow plus the shared real message-Token probe and browser identity. |
| `account_session_refresh.py` | Structured refresh state, cancellation, visibility, and screenshots. |
| `cookie_manager.py` | Runtime account-task ownership and lock-safe listener replacement. |
| `XianyuAutoAsync.py` | Xianyu live, message, order, and refresh behavior. |
| `db_manager.py` | SQLite schema, migrations, and persistence facade. |
| `skill_monitor_scheduler.py` | Single-loop Skill Center schedule lifecycle. |
| `ai_provider_service.py` / `ai_reply_engine.py` | Provider configuration and product-scoped replies. |
| `frontend/` / `static/` | Frontend source and built assets served by FastAPI. |
| `docs/` | Architecture, API, operations, and handoff evidence. |

## Boundaries

- Never commit or document real passwords, Cookies, Tokens, provider keys, deployment credentials, verification URLs, databases, logs, or browser profiles.
- Preserve `data/`, `logs/`, `browser_data/`, `.venv/`, `.env`, and `static/uploads/` during deployment. Back up SQLite, all local encryption keys, prior static assets, live source, and every browser profile before authentication changes.
- Keep `WEB_CONCURRENCY=1`; SQLite and in-memory browser sessions are not multi-worker safe.
- Keep `frontend/vite.config.ts` `base: '/static/'` because FastAPI serves bundles under `/static/`.
- Treat `cookies.xianyu_unb` as stable account identity. Re-login updates the existing row; deleting an account is destructive and is not session recovery.
- New login opens the official parent page in the installed system Chrome. Background mode is headed and off-screen: do not add headless renewal, User-Agent overrides, anti-detection scripts, web-security bypass flags, internal QR requests, or automatic slider handling.
- Automatic and scheduled renewal reuse `browser_data/user_<unb>` only. They never read or submit a saved password; a logged-out profile waits for explicit human login in the same session.
- SMS, QR, face, slider, and risk-control verification remain human-operated. Keep safe screenshots, explicit browser display, a 15-minute timeout, cancellation, and automatic continuation after the user completes the official step.
- Official-login success requires a real `unb`, key session Cookies, no active login/verification surface, and expected-identity matching for an existing account.
- Detach the old listener under the account lock, cancel and wait outside the lock, then install the latest generation under the lock. Browser threads may post immutable state to the event loop but must not wait on it while holding a database lock.
- Status polling is read-only. Concurrent polling, repeated callbacks, and repeated refresh requests must not duplicate persistence or listener restarts.
- `POST /password-login` accepts `account`, `password`, and `show_browser`; legacy `account_id` is ignored. Save credentials only after an explicit password login succeeds.
- APIs, runtime sessions, and logs must not expose stored passwords or ciphertext, full Cookies, Tokens, or verification URLs. Logs use masked account identifiers and stay mode `0600`.
- Product facts stay scoped by `(cookie_id, item_id)`. Training may read drafts; production reads only published knowledge. Copy writes target drafts, defaults to no overwrite, and never auto-publishes.
- Provider/model changes must pass a generated-reply test before replacing active configuration.
- Cookie refresh and Skill schedules default off. Do not change persisted schedule settings as a side effect of deployment.

## Documentation Map

| Need | Read |
|---|---|
| System design and data model | `docs/architecture.md` |
| API examples and account-session behavior | `docs/integration-guide.md` |
| Deployment, backups, and troubleshooting | `docs/operator-runbook.md` |
| Current evidence and pending acceptance | `docs/handoff.md` |

Keep route docs, OpenAPI snapshots, and frontend wrappers synchronized for `/api/official-login/sessions*`, compatibility `/qr-login/*` and `/password-login/*`, `/api/accounts/{cookie_id}/session-*`, `/cookies/{cid}/cookie-refresh-settings`, settings, providers, product knowledge, orders, and Skill Center APIs.
