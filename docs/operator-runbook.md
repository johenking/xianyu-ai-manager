# Operator Runbook

## Local Start

```bash
cd /path/to/xianyu-ai-manager
source .venv/bin/activate
python Start.py
```

Open `http://127.0.0.1:8091`.

If the service is already managed in tmux:

```bash
tmux capture-pane -t xianyu-butler -p -S -200
```

## Build

```bash
source .venv/bin/activate
python -m py_compile db_manager.py reply_server.py

cd frontend
npm run build
```

The frontend build writes to `static/`.

## Smoke Tests

```bash
curl -sS http://127.0.0.1:8091/health

curl -sS -X POST http://127.0.0.1:8091/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<password>"}'
```

After login:

```bash
curl -sS http://127.0.0.1:8091/api/skills/ops/health \
  -H "Authorization: Bearer $TOKEN"
```

## Environment Variables

| Variable | Purpose |
|---|---|
| `PORT` | Cloud web port override. |
| `API_PORT` | Alternative web port override used by `entrypoint.sh` and `Start.py`. |
| `API_HOST` | Bind host, usually `0.0.0.0` in containers. |
| `ADMIN_PASSWORD` | Initial admin password used only when the database creates the `admin` user. |
| `DB_PATH` | SQLite database path, default `data/xianyu_data.db`. |
| `TZ` | Runtime timezone, usually `Asia/Shanghai`. |
| `PYTHONUNBUFFERED` | Log flushing in containers. |
| `PLAYWRIGHT_BROWSERS_PATH` | Playwright browser cache path. |
| `DOCKER_ENV` | Enables Linux/container Playwright handling. |

Do not commit secrets. Put Hugging Face tokens, Fly tokens, model API keys, and Xianyu cookies in platform secret stores or the Web UI only.

## Hugging Face Deployment

The Space uses Docker. Keep `README.md` frontmatter:

```yaml
sdk: docker
app_port: 8080
```

Recommended Space secrets/variables:

```bash
ADMIN_PASSWORD=<strong-password>
PORT=8080
API_HOST=0.0.0.0
TZ=Asia/Shanghai
PYTHONUNBUFFERED=1
```

Use `huggingface_hub.HfApi.upload_folder` or a git remote. Exclude local runtime state:

- `.venv/`
- `frontend/node_modules/`
- `data/`
- `logs/`
- `backups/`
- `.env`
- database files

## Xianyu Login Troubleshooting

Symptoms:

- QR code loads but confirmation never creates an account.
- Password login triggers slider, face verification, or fails silently.
- Cookie refresh fails after QR confirmation.

Likely causes:

- Cloud or overseas datacenter IP triggers Xianyu/Alibaba risk control.
- Headless Chromium fingerprint is rejected.
- Runtime filesystem is ephemeral and browser profile state is lost.
- Cookie is expired or missing required fields such as `unb`, `_m_h5_tk`, `_m_h5_tk_enc`, `cookie2`, `sgcookie`.

Recommended order:

1. Bind the Xianyu account locally at `http://127.0.0.1:8091`.
2. Use visible-browser password login when the local UI exposes it.
3. Use manual Cookie entry if QR/password login is blocked.
4. Use a trusted domestic host or VPS for long-running automation.
5. Avoid relying on Hugging Face free runtime for persistent Xianyu login state.

## Logs

Useful local checks:

```bash
tmux capture-pane -t xianyu-butler -p -S -500
rg -n "qr-login|password-login|风控|验证码|滑块|captcha|x5sec|登录失败|error|ERROR" realtime.log logs -S
```

Protected Web APIs:

- `GET /logs`
- `GET /logs/stats`
- `GET /risk-control-logs`
- `GET /admin/logs`

## Session Persistence

Backend login tokens are persisted in `auth_sessions` and expire after 30 days. If a user is unexpectedly logged out:

1. Confirm the browser still has `localStorage.auth_token`.
2. Call `/verify` with the bearer token.
3. Check that `auth_sessions` exists in the SQLite database.
4. Confirm the server was not reset to a different `DB_PATH`.
