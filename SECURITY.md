# Security Policy

## Supported Version

Security fixes are applied to the latest release on `main`.

## Reporting

Please use GitHub Private Vulnerability Reporting or open a private security advisory for this repository. Do not include live Cookie values, passwords, API keys, tokens, verification URLs, database files or personal account identifiers in a public issue.

Include the affected version, reproduction steps, impact and a minimal redacted example. Maintainers will acknowledge a valid report as soon as practical.

## Deployment Baseline

- Set a strong `ADMIN_PASSWORD` and a random `JWT_SECRET_KEY`.
- Use independent AI-provider, Xianyu-account, and system-secret encryption keys, or preserve all three generated key files with SQLite backups.
- Keep the service behind HTTPS and restrict network access where possible.
- Keep public registration disabled until the current SMTP configuration has delivered a real verification message and an active single-use invite exists.
- Trust forwarded client-IP headers only from explicitly configured proxy IPs or CIDRs.
- Never commit `data/`, `.env`, logs, browser profiles or uploaded files.
- Rotate any credential that has appeared in terminal output, logs or issue attachments.
- Back up SQLite before upgrades and test restore procedures.
