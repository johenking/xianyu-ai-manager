# Operator Runbook

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

The current live runtime path is `/Users/mac/Documents/Codex/2026-06-09/github-23star-xianyu-super-butler-https-3/work/xianyu-super-butler`; it tracks `https://github.com/johenking/xianyu-ai-manager.git` as `origin`. Preserve `data/`, `logs/`, `browser_data/`, `.venv/`, and `static/uploads/` during local deployments. Cloudflare can keep old hashed assets alive with `cf-cache-status: HIT`; if the public HTML points at the new entry bundle and local `/static/assets/<old>.js` is 404, the stale asset response is cache, not the running server.

## Backup Before Risky Changes

Back up the live SQLite database before migrations, account identity changes, authentication deployments, or bulk data operations:

```bash
mkdir -p data/backups
STAMP=$(date +%Y%m%d-%H%M%S)
sqlite3 data/xianyu_data.db ".backup 'data/backups/xianyu_data_${STAMP}.db'"
sqlite3 "data/backups/xianyu_data_${STAMP}.db" "PRAGMA integrity_check;"
shasum -a 256 "data/backups/xianyu_data_${STAMP}.db"
```

Back up `data/.ai_provider_key` and `data/.account_credential_key` with the database when their environment keys are not supplied. Before replacing authentication code or profiles, stop the service and copy all of `browser_data/`; a live Chromium profile is not a reliable filesystem backup. Do not delete unmatched `user_*` profiles during cleanup because their identity may not yet be reconciled.

## Verification

```bash
source .venv/bin/activate
pip install -r requirements-dev.lock
python -m py_compile Start.py app_factory.py application_runtime.py api_routers.py settings_service.py db_manager.py schema_migrations.py security_utils.py session_registry.py skill_monitor_scheduler.py reply_server.py XianyuAutoAsync.py utils/xianyu_official_login.py
python -m unittest discover -s tests -v
ruff check .

cd frontend
npm run typecheck
npm test
npm run build
npm run build
npm run verify:build
```

The frontend build writes to `static/`. It keeps the current and previous successful asset generations and disables source maps unless `VITE_BUILD_SOURCEMAP=true`. A production build alone does not restart the backend.

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

curl -sS http://127.0.0.1:8091/api/skills/ops/health \
  -H "Authorization: Bearer $TOKEN"
```

## Environment Variables

| Variable | Purpose |
|---|---|
| `ADMIN_PASSWORD` | Initial admin password, used only when creating a new database. |
| `JWT_SECRET_KEY` | Signs backend session tokens; use an independent random value. |
| `AI_PROVIDER_ENCRYPTION_KEY` | Encrypts provider API keys. If absent, a local key file is generated under `data/`. |
| `ACCOUNT_CREDENTIAL_ENCRYPTION_KEY` | Encrypts stored Xianyu login passwords with an independent key. |
| `PORT` | Cloud web port override. |
| `API_PORT` | Alternative web port used by `entrypoint.sh` and `Start.py`. |
| `API_HOST` | Bind host, usually `0.0.0.0` in containers. |
| `DB_PATH` | SQLite path, default `data/xianyu_data.db`. |
| `TZ` | Runtime timezone, usually `Asia/Shanghai`. |
| `PLAYWRIGHT_BROWSERS_PATH` | Playwright browser cache path. |
| `DOCKER_ENV` | Enables Linux/container Playwright handling. |
| `VITE_BUILD_SOURCEMAP` | Set to `true` only when a production source map is explicitly required. |

Do not commit secrets. Put deployment tokens, model keys, SMTP credentials, and Xianyu Cookies in platform secret stores or the Web UI.

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

Persist and protect the database, logs, uploads, both local encryption keys, and `browser_data/`. Exclude `.venv/`, `frontend/node_modules/`, `data/`, `browser_data/`, `logs/`, `backups/`, `.env`, and database files from source uploads.

Official password login and renewal launch headed Chromium because Goofish rejects headless mode. The current container entrypoint does not create a virtual display, so do not claim Docker or cloud credential renewal works until a display/Xvfb setup and human-verification workflow have been tested on that deployment.

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

Symptoms include missing message Tokens, expired Cookies, or a `verification_required` refresh state.

Recommended order:

1. Read `/api/accounts/{cookie_id}/session-status` and `/api/diagnostics/auto-reply/{cookie_id}`.
2. Confirm `cookies.xianyu_unb` is present and `browser_data/user_<unb>` exists; the refresh service always tries that profile first.
3. Keep the account listener running, then trigger `/session-refresh` once.
4. If the status reports `no_credentials`, perform one official account-password login so encrypted fallback credentials are saved.
5. Complete the account-page verification when required; the visible browser waits for up to 15 minutes and platform verification cannot be bypassed.
6. Check the account edit modal before enabling scheduled preventive refresh; it defaults to off and should use conservative intervals such as 24 hours or longer.
7. Use QR or update the existing Cookie only when the official profile and password fallback cannot recover the session.
8. Do not delete the account to re-login, because deletion removes account-linked configuration and knowledge.

Cloud, overseas, or datacenter IPs can trigger Xianyu/Alibaba risk control. Local binding or a trusted domestic host is generally more reliable than a free ephemeral runtime.

Do not switch the renewal browser to `headless=True`: Goofish currently returns an illegal-access page to headless Chromium. Background renewal intentionally launches a headed browser off-screen and reopens it visibly only for human verification. Password login still depends on the current official page structure; when that flow breaks after a platform change, use QR or Cookie recovery without deleting the account.

## Skill Monitor Troubleshooting

The scheduler runs inside the one Uvicorn worker and polls every 30 seconds. Keep `WEB_CONCURRENCY=1`; multiple processes can race on the same SQLite task state.

1. Read `/api/skills/monitor/tasks` and check `schedule_enabled`, `next_run_at`, `last_status`, and `last_error`.
2. Confirm the interval is at least 15 minutes and the task is enabled.
3. For AI filtering, verify the bound account has an enabled provider, key, base URL, and model that passed a generated-reply test.
4. For notifications, enable at least one supported Webhook, WeChat, DingTalk, Feishu, Bark, or Telegram channel. QQ and email are not Skill Center senders.
5. Interpret `partial` as at least one successful and at least one failed channel; inspect `raw_data.notify_error` for per-channel errors.
6. A repeated item is intentionally skipped when the same task already stored its URL or platform item ID.
7. After a service restart, a task left in `running` becomes `failed` with an interruption error and can run again on its next schedule.

Smoke-test a task manually before enabling its schedule:

```bash
curl -sS -X POST "$BASE_URL/api/skills/monitor/tasks/$TASK_ID/run" \
  -H "Authorization: Bearer $TOKEN"
```

## Order Sync Troubleshooting

Use `POST /api/orders/sync` with `{"days":90}` to discover missing recent orders and reconcile delivery, completion, and refund states. Treat a 409 response with `requires_login` as an account-session problem, not as a successful zero-result sync. After restoring the existing account session, run the sync again and inspect each order's platform status text, sync source, last sync time, and last sync error.

## Logs And Sessions

```bash
tmux capture-pane -t xianyu-butler -p -S -500
rg -n "session-refresh|scheduled_cookie_refresh|verification_required|qr-login|password-login|风控|验证码|captcha|登录失败|error|ERROR" realtime.log logs -S
```

Protected log APIs include `/logs`, `/logs/stats`, `/risk-control-logs`, and `/admin/logs`. Logs must not contain full Cookies, tokens, passwords, provider keys, or verification URLs.

Backend login tokens live in `auth_sessions` for up to 30 days. If the dashboard logs out unexpectedly, check browser `localStorage.auth_token`, call `/verify`, confirm the same `DB_PATH` is in use, and verify that the session row still exists.
