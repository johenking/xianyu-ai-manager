# Xianyu AI Manager Deployment Notes

## Local Login

- URL: `http://localhost:8091`
- Username: `admin`
- Initial password: `ADMIN_PASSWORD` if set; otherwise `admin123`

Set `ADMIN_PASSWORD` before public deployment, or change the password immediately after first login. Do not commit real admin passwords, Hugging Face tokens, Fly tokens, Xianyu cookies, or model API keys.

## Why Not Netlify

This project is a long-running FastAPI application with SQLite data, WebSocket/background work, and Playwright/Chromium browser automation. Netlify is best for static sites and serverless/edge functions. Its functions run in ephemeral runtimes and have execution limits, so it is not a good fit for keeping this app alive as a normal backend service.

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
PORT=8080
API_HOST=0.0.0.0
TZ=Asia/Shanghai
PYTHONUNBUFFERED=1
```

When deploying with `huggingface_hub`, upload source and built assets only. Exclude `.venv/`, `frontend/node_modules/`, `data/`, `logs/`, `backups/`, `.env`, and database files. If a Hugging Face token was pasted into chat or logs, rotate it after deployment.

## Local Docker

The Docker image is prepared, but the current machine needs more free disk space before building. Use this after disk space is available:

```bash
docker build -t xianyu-ai-manager .
docker run --rm -p 8091:8080 \
  -e PORT=8080 \
  -e API_HOST=0.0.0.0 \
  -e ADMIN_PASSWORD='change-me' \
  -v "$PWD/data:/app/data" \
  -v "$PWD/logs:/app/logs" \
  xianyu-ai-manager
```

Then open `http://localhost:8091`.
