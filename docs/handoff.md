# Handoff

## Status On 2026-06-09

The project is installed, built, running locally, and deployed to Hugging Face Spaces.

Known URLs:

- Local: `http://127.0.0.1:8091`
- 服务地址：使用您自己的部署域名，或本地 `http://127.0.0.1:8091`

The backend admin username is `admin`. Passwords are intentionally not stored in repository files; use the deployed platform secret `ADMIN_PASSWORD` or update the password through the Web UI.

## Completed Work

- Installed Python, Playwright, and frontend dependencies.
- Built React/Vite assets into `static/`.
- Changed local service configuration from port `8080` to `8091` to avoid a port conflict.
- Added Docker deployment support through `Dockerfile`, `entrypoint.sh`, `render.yaml`, and `fly.toml`.
- Deployed the app to Hugging Face Spaces Docker.
- Added persistent backend login sessions through the `auth_sessions` table.
- Fixed frontend auth verification so `authenticated: false` does not count as a logged-in state.
- Added a sidebar `技能中心` entry.
- Added safe-rewrite Skill Center modules:
  - Monitor tasks and results.
  - AI expert Prompt management and test reply.
  - Ops health, browser status, and delivery diagnostics.

## Verification Evidence

Commands verified on 2026-06-09:

```bash
source .venv/bin/activate
python -m py_compile db_manager.py reply_server.py

cd frontend
npm run build

curl -sS http://127.0.0.1:8091/health
```

Remote checks verified:

```bash
curl -sS http://127.0.0.1:8091/health
```

Remote login and `/verify` succeeded after the deployment returned to `RUNNING`.

## Important Caveats

- Hugging Face or other overseas cloud environments are poor fits for initial Xianyu account binding. Use local binding or a trusted domestic server when possible.
- Runtime databases, cookies, logs, API keys, and deployment tokens must not be committed or uploaded.
- The Skill Center integration is a first usable loop, not a full clone of the referenced repositories.
- Directly copying GPL/AGPL source from external Xianyu projects should be avoided unless the repository owner accepts the licensing consequences.

## Next Useful Work

- Add a first-class "manual Cookie add" flow for brand-new accounts instead of requiring QR login first.
- Surface QR/password login failure messages in the frontend modal.
- Add a Skill Center log table view backed by `skill_run_logs`.
- Add tests around `auth_sessions` expiry and Skill Center API authorization.
