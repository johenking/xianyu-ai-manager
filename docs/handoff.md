# Handoff

## Source State On 2026-07-11

The `v1.6.0` source combines the v1.5.0 official Goofish session renewal and Skill Center automation with fail-closed invitation registration, email-based password recovery, persistent authentication limits, local agreement pages, and administrator registration controls. Passwords, Cookies, email codes, invite codes, API keys, deployment tokens, databases, browser profiles, and live account data remain outside source control.

Publishing source does not prove a running service was upgraded. Verify the process path, health response, migration version, frontend entry bundle, account listeners, and Skill scheduler before describing any deployment as running v1.6.0. Registration must remain closed until real SMTP delivery and an end-to-end invited-user acceptance test have both succeeded.

## Working Capabilities

- Multi-account official password, QR, and manual-Cookie binding with listener and auto-reply diagnostics.
- Stable Xianyu identity matching through `xianyu_unb`, so same-user re-login updates the existing account record.
- Persistent official browser profiles under `browser_data/user_<unb>`, with profile-first renewal and encrypted credential fallback after complete logout.
- One official refresh path for manual refresh, scheduled refresh, Token expiry, and repeated connection failures, with account-page secondary verification and cancellation.
- Product-scoped knowledge and training rules with draft/published separation, copy-to-draft, rule auditing, and guarded price replies.
- User-scoped AI provider profiles with encrypted keys, model discovery, and test-before-apply account switching.
- Recent-order discovery and reconciliation with completion, refund, cancellation, and login-required states.
- Skill Center manual and scheduled monitoring with a 15-minute minimum interval and persisted run state.
- Optional AI result filtering using an enabled account provider configuration.
- Webhook, WeChat, DingTalk, Feishu, Bark, and Telegram result delivery with `sent`, `partial`, and `failed` outcomes.
- Cross-run result deduplication by task and item URL, falling back to platform item ID.
- Expert prompts and real runtime, browser, AI, delivery, and account-listener diagnostics.
- One-transaction invitation registration with a single-use invite, image CAPTCHA, purpose-bound email code, terms acceptance, and automatic login.
- Username-or-email login and email password recovery that revokes all older sessions after reset.
- Administrator SMTP readiness, invite creation/revocation, ordinary-user enablement, and guarded registration switch controls.
- Purpose-isolated HMAC storage for authentication secrets and identifiers, persistent multi-dimensional rate limits, and trusted-proxy client-IP handling.

## Important Boundaries

- Training uses the current product draft; real buyer replies use only the published knowledge snapshot.
- Copying knowledge writes target drafts only, defaults to no overwrite, and never publishes automatically.
- Deleting an account removes account-linked data. Re-login or update the Cookie instead of deleting for session recovery.
- Scheduled Cookie refresh and Skill monitor schedules both default off. Cookie refresh allows 1 hour to 7 days; Skill monitoring allows 15 minutes or longer.
- Goofish rejects headless Chromium. Official renewal uses a headed off-screen browser and becomes visible for human verification.
- Alibaba SMS, QR, face, and risk-control verification cannot be bypassed. A profile can renew without another scan only while the official session or encrypted credential fallback remains usable.
- Skill Center notification delivery excludes QQ and email even though those channel types may exist elsewhere in the database.
- Capability readiness does not guarantee an external AI provider or notification endpoint will remain reachable.
- The scheduler depends on the intentional one-process, one-Uvicorn-worker runtime.
- Registration defaults off and cannot be enabled without a verified current SMTP fingerprint and an active invite.
- SMTP verification sends a real message and has no third-party fallback. Missing credentials, failed delivery, database errors, or changed SMTP settings keep registration closed.
- Raw invite codes appear once. CAPTCHA and email challenges expire after 10 minutes and stop after five attempts.
- The system-secret key is independent from the AI-provider and Xianyu-account keys; all three local key files must be restored with SQLite when environment keys are absent.

## Verification Baseline

Run before release or deployment:

```bash
source .venv/bin/activate
pip install -r requirements-dev.lock
ruff check .
python -m py_compile Start.py app_factory.py application_runtime.py api_routers.py auth_email_service.py auth_registration_service.py settings_service.py db_manager.py schema_migrations.py security_utils.py session_registry.py repositories/auth_repository.py repositories/runtime_session_repository.py services/auth_service.py ai_provider_service.py ai_reply_engine.py account_session_refresh.py order_sync_service.py skill_monitor_scheduler.py reply_server.py XianyuAutoAsync.py utils/xianyu_official_login.py
python -m unittest discover -s tests -v

cd frontend
npm audit --audit-level=high
npm run typecheck
npm test
npm run build
npm run build
npm run verify:build
```

Also run `git diff --check` and a secret scan over every tracked and prospective file. For deployment, back up SQLite, all three local encryption keys, browser profiles, and the previous static assets first.

The automated suite covers official login modes, profile promotion and reuse, encrypted credential fallback, verification timeout and cancellation, account data retention, Skill scheduler lifecycle and locking, success/failure rescheduling, AI filtering, supported-channel filtering, multi-channel notification outcomes, cross-run deduplication, registration transactions and races, challenge expiry and attempts, rate limits, trusted proxies, SMTP failure behavior, session revocation, public auth views, and administrator registration interactions. Real platform acceptance still requires operator-owned Xianyu, AI provider, notification, and SMTP accounts.

Verified on 2026-07-11 for the v1.6.0 source: Ruff and explicit Python compilation passed, all 194 backend tests passed, all 15 frontend test files with 37 tests passed, and `npm audit --audit-level=high` reported zero vulnerabilities. Two production builds retained two generations of 28 assets with zero orphans; the 235,685-byte entry bundle is 72.8% below the 865,910-byte baseline. The favicon resolves at `/static/favicon.svg`.

## Next Acceptance Steps

- Keep registration closed while configuring a real QQ or 163 SMTP authorization code and support mailbox, then verify actual inbox delivery.
- Create a small seven-day invite batch and complete one real registration, automatic login, service-restart session restore, password reset, and invite second-use rejection before opening registration.
- Verify the deployed frontend entry, schema migration `2026071102`, account listeners, and Skill scheduler after restart; Skill schedules remain default off.
- Keep monitoring official page, SMTP, AI-provider, and notification changes; do not weaken human verification, rate limits, or secret-handling boundaries to improve automation rates.
