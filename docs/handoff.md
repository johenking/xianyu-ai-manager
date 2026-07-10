# Handoff

## Source State On 2026-07-11

The `v1.5.0` source release combines official Goofish login and Cookie renewal with the completed Skill Center scheduler, AI filtering, notification delivery, and result deduplication. Passwords, Cookies, API keys, deployment tokens, databases, browser profiles, and live account data remain outside source control.

Publishing this source does not deploy it. The existing Mac service on port `8091` has the official-login renewal code but has not deployed the Skill Center scheduler changes. Verify the listening process, health response, frontend bundle, and enabled tasks before describing that service as running all `v1.5.0` capabilities.

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

## Verification Baseline

Run before release or deployment:

```bash
source .venv/bin/activate
pip install -r requirements-dev.lock
ruff check .
python -m py_compile Start.py app_factory.py application_runtime.py api_routers.py settings_service.py db_manager.py schema_migrations.py security_utils.py session_registry.py skill_monitor_scheduler.py reply_server.py XianyuAutoAsync.py utils/xianyu_official_login.py
python -m unittest discover -s tests -v

cd frontend
npm audit --audit-level=high
npm run typecheck
npm test
npm run build
npm run build
npm run verify:build
```

Also run `git diff --check` and a secret scan over every tracked and prospective file. For deployment, back up SQLite, both local encryption keys, browser profiles, and the previous static assets first.

The automated suite covers official login modes, profile promotion and reuse, encrypted credential fallback, verification timeout and cancellation, account data retention, Skill scheduler lifecycle and locking, success/failure rescheduling, AI filtering, supported-channel filtering, multi-channel notification outcomes, cross-run deduplication, API validation, and frontend task interactions. Real platform acceptance still requires operator-owned Xianyu, AI provider, and notification accounts.

Verified on 2026-07-11 for the source release: Ruff and Python compilation passed, all 110 backend tests passed, all 12 frontend test files with 26 tests passed, `npm audit --audit-level=high` reported zero vulnerabilities, and two production builds produced 31 assets with zero orphans. The entry bundle was 216,200 bytes, 75% below the 865,910-byte baseline.

## Next Acceptance Steps

- Complete one real official password login, then verify the canonical `user_<unb>` profile, manual renewal, service restart, profile reuse, and listener recovery.
- Before deploying Skill scheduling to the Mac service, back up runtime data and confirm no existing task will become immediately due after migration.
- Exercise one real scheduled monitor with AI filtering and at least two supported notification channels, then verify deduplication on the second run.
- Keep monitoring official page changes and external provider failures; do not weaken human verification or secret-handling boundaries to improve automation rates.
