# Xianyu AI Manager Deployment Notes

## Local Login

- URL: `http://localhost:8091`
- Username: `admin`
- Initial password: `ADMIN_PASSWORD` if set; otherwise `admin123`

Set `ADMIN_PASSWORD` before public deployment, or change the password immediately after first login. Do not commit real admin passwords, Hugging Face tokens, Fly tokens, Xianyu cookies, or model API keys.

## Why Not Netlify

This project is a long-running FastAPI application with SQLite data, WebSocket/background work, and Playwright/Chromium browser automation. Netlify is best for static sites and serverless/edge functions. Its functions run in ephemeral runtimes and have execution limits, so it is not a good fit for keeping this app alive as a normal backend service.

The account listeners and Skill Center scheduler share the application event loop. Run exactly one Uvicorn worker and persist SQLite storage; horizontal multi-worker deployment is unsupported.

The official-login stability change adds migration `2026071701`, which persists the official browser User-Agent on each account. Before calling any build deployed, verify the listening process path, local and public health, the migration version, HTML entry bundle and every referenced asset, account listeners, disabled-by-default Cookie and Skill schedules, and the official-login status endpoints. Source or build completion alone is not deployment or CI evidence; dated Mac deployment evidence and external-account release gates live in `docs/handoff.md`.

## Recommended Platforms

Use a Docker web service platform instead:

1. Render
   - Easiest path for this repo because `render.yaml` is included.
   - Free web services sleep when idle and do not keep local SQLite files permanently.
   - Good for demo/testing. Use paid persistent disk or migrate SQLite data to an external database for production.

2. Fly.io
   - Better fit if you need persistent SQLite via a volume.
   - Requires Fly account/CLI and usually billing setup.

3. Koyeb
   - Docker/Git deploy workflow, similar to Render.
   - Free instance resources are small, so Playwright/Chromium may be tight.

4. Hugging Face Spaces Docker
   - Good for public demos.
   - Runtime disk is ephemeral unless you attach storage.
   - Xianyu login/binding is often unreliable from cloud or overseas datacenter IPs. Bind accounts locally or use a trusted domestic host when possible.

5. Cheap VPS
   - Most stable option for this specific app because browser automation and SQLite prefer a persistent machine.

## Render Setup

1. Push this repo to your own GitHub repository.
2. Open Render and create a new Blueprint or Web Service from that repository.
3. Use the included `render.yaml` or Dockerfile.
4. Set these environment variables:

```bash
PORT=8080
API_HOST=0.0.0.0
TZ=Asia/Shanghai
PYTHONUNBUFFERED=1
ADMIN_PASSWORD=<set-a-strong-password>
JWT_SECRET_KEY=<set-an-independent-random-secret>
AI_PROVIDER_ENCRYPTION_KEY=<set-an-independent-random-secret>
ACCOUNT_CREDENTIAL_ENCRYPTION_KEY=<set-an-independent-random-secret>
SYSTEM_SECRET_ENCRYPTION_KEY=<set-an-independent-random-secret>
```

5. After deploy, open:

```text
https://<your-render-service>.onrender.com
```

6. Login with `admin` and the configured `ADMIN_PASSWORD`, then change the password in the UI if needed.

## Hugging Face Spaces

Use Docker SDK. Add or preserve `sdk: docker` and `app_port: 8080` frontmatter in the Hugging Face export README; the GitHub README does not require Spaces frontmatter.

Required/recommended Space secrets or variables:

```bash
ADMIN_PASSWORD=<set-a-strong-password>
JWT_SECRET_KEY=<set-an-independent-random-secret>
AI_PROVIDER_ENCRYPTION_KEY=<set-an-independent-random-secret>
ACCOUNT_CREDENTIAL_ENCRYPTION_KEY=<set-an-independent-random-secret>
SYSTEM_SECRET_ENCRYPTION_KEY=<set-an-independent-random-secret>
PORT=8080
API_HOST=0.0.0.0
TZ=Asia/Shanghai
PYTHONUNBUFFERED=1
```

When deploying with `huggingface_hub`, upload source and built assets only. Exclude `.venv/`, `frontend/node_modules/`, `data/`, `browser_data/`, `logs/`, `backups/`, `.env`, and database files. If a Hugging Face token was pasted into chat or logs, rotate it after deployment.

## Local Docker

Build and run the Docker image locally with:

```bash
docker build -t xianyu-ai-manager .
docker run --rm -p 8091:8080 \
  -e PORT=8080 \
  -e API_HOST=0.0.0.0 \
  -e ADMIN_PASSWORD='change-me' \
  -v "$PWD/data:/app/data" \
  -v "$PWD/browser_data:/app/browser_data" \
  -v "$PWD/logs:/app/logs" \
  xianyu-ai-manager
```

Then open `http://localhost:8091`.

The official Goofish login flow requires the installed system Chrome in headed mode; the current container command does not start a virtual display. Automatic renewal is profile-only and never submits a stored password. Treat QR login, explicit password login, and profile renewal as unsupported in Docker or cloud environments until system Chrome, a display/Xvfb setup, and the human-verification path have been tested there. Persisting `browser_data/` is required once that support exists.

## Direct Registration

Do not enable registration as part of an unattended deployment. Start with `registration_enabled=false`, configure SMTP and an independent public support email in the administrator UI, send the six-digit SMTP receipt code, and enter the code from the real mailbox. The verified fingerprint becomes stale and registration closes after any SMTP change. Confirm the ordinary-user limit, then exercise direct registration, automatic login, restart persistence, username-or-email login, and two-stage password reset before opening the switch. A successful registration or reset email send must not refresh CAPTCHA immediately; an explicit resend after cooldown must require a new CAPTCHA. Reset acceptance must verify that `/api/auth/password-reset/verify-code` issues an in-memory one-time grant, `/api/auth/password-reset` consumes it, grant replay fails, and all old sessions are revoked. The legacy direct reset payload is temporary compatibility only. The default capacity is 20; disabled ordinary users still count, and the administrator does not.

Inspect authentication logs after acceptance without printing request bodies. They must not expose the default administrator password, email OTPs, reset grant IDs or tokens, full email addresses, or passwords.

When environment encryption keys are not supplied, persist and back up `data/.ai_provider_key`, `data/.account_credential_key`, and `data/.system_secret_key` with SQLite. SMTP credentials are configured in the UI and encrypted with the system-secret key; the example `SMTP_*` environment names are not a configuration path for this application.
