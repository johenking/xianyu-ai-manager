# Handoff

## Monitor Integration Candidate On 2026-07-18

The monitor work is isolated in `/Users/mac/Documents/Codex/integration/xianyu-monitor-20260718` on branch `codex/monitor-integration-20260718`. The immutable source baseline is commit `dd0e8f2`, tagged `codex/live-source-baseline-20260718-043547`; its provenance records 201 allowlisted source files, zero symlinks, sanitized remotes, and Stage A backup root hash `e6983cb07117c477bab2366bc26b2355789797255d5735b541b4c9ca39d3a08d`. Stage B and its evidence record end at `0272de1`; Stage C is the following isolated offline-integration commit.

Stage A remains recoverable from `/Users/mac/Documents/Codex/backups/xianyu-monitor-stage-a-20260718-043547`. Stage B adds fail-closed monitor, scheduler, delivery, and experimental MTop feature flags; expand-only schema changes; persistent run and delivery claims with independent tokens, leases, heartbeats, and stale recovery; transactional first-seen events and delivery outbox rows; stable delivery idempotency keys with explicit `unknown` outcomes; Cookie revision compare-and-swap; and owner/account/revision-bound Playwright search with isolated profiles and no anonymous fallback. No complete MTop response is retained.

Stage C adds a default-off, offline-testable MTop adapter without selecting it from manual or scheduled monitor execution. It provides a fixed-endpoint request contract, response-size and schema validation, item-field allowlisting, deterministic pagination/filter/sort normalization, account/global budgets, bounded jitter and `Retry-After`, a persistent circuit breaker with one leased half-open probe, Cookie revision CAS, a normalized shadow comparator, and strict lease-scoped AI JSON parsing. The runtime monitor path still explicitly claims `source_adapter=playwright`; the MTop adapter has made no real request.

The API and Skill Center UI now report six independent claims: `code_present`, `config_ready`, `last_real_search`, `last_scheduled_run`, `last_ai_decision`, and `last_real_delivery`. Missing evidence is rendered as never verified, never run, never judged, or never confirmed. Code presence is not configuration, runtime, AI, or delivery evidence.

Fresh Stage C local verification on 2026-07-18 passed:

- `ruff check .` and the project `py_compile` gate exited 0; `python -m unittest discover -s tests -v` passed 330 of 330 tests. The Stage C monitor-only discovery passed 61 of 61 tests.
- `npm audit --audit-level=high` reported zero vulnerabilities; TypeScript exited 0; Vitest passed 17 files and 77 tests.
- Two independent Vite builds each produced 31 assets. `verify:build` found 31 of 31 retained assets, zero orphans, and a 245,200-byte entry bundle, 71.7% smaller than the retained baseline.
- Both exact Python lock files passed `pip-audit --no-deps --disable-pip` with no known vulnerabilities.
- Gitleaks 8.30.1 scanned all 229 tracked and prospective source files (about 4.11 MB) from a symlink-preserving temporary snapshot and reported no leaks.
- A fresh database reached `2026071802` with 43 application tables, 9 migration rows, `integrity_check=ok`, zero foreign-key violations, both new tables, and both retention indexes. The exact pre-Stage-C source at `0272de1` opened a copy of that expanded database with the same schema version, row counts, integrity result, and foreign-key result.
- Local mocked UI review covered default-off, loading, empty, missing-evidence, and synthetic error states at 1440x900 and 390x844. Default and loading runs had zero console warnings/errors, page errors, failed requests, blocked external attempts, or successful external requests; the intentional error fixture produced only its expected local HTTP 500 resource message. Full-scroll document/main widths were 1440/1440 and 390/390, and the MTop panel stayed inside the content rail. Evidence is under `/Users/mac/Documents/Codex/evidence/xianyu-monitor-stage-c-ui-20260718-075104` and is local mocked evidence only.

This branch has not been deployed. It has not read or called either existing Xianyu account, used a production or dedicated test Cookie, made an MTop or Playwright shadow request, sent a real Webhook or other notification, or called a real AI provider. No schedule, delivery dispatcher, AI provider, or MTop feature switch was enabled, and the existing live service does not yet expose this truthful capability matrix. The registered `iPhone 15 Pro` canary remains `unverified`; the historical live evidence below belongs to the official-login deployment and must not be treated as monitor-integration deployment or real-provider acceptance.

## Source State On 2026-07-17

The `codex/official-login-stability` branch starts from clean tag `v1.7.3` and replaces active custom QR, automatic-slider, and headless verification paths with one official Goofish browser service. New QR/password login uses the official parent page, existing accounts reuse `browser_data/user_<unb>`, verification stays human-operated, and listener replacement is bounded outside its account lock. Passwords, Cookies, email codes, reset grants, API keys, deployment tokens, databases, browser profiles, and live account data remain outside source control.

GitHub CI and the running service remain independent evidence: publishing or building this source does not prove a runtime was upgraded. Migration `2026071701` adds `cookies.browser_user_agent`; recheck the process path, health response, migration version, frontend entry bundle and referenced assets, account listeners, Cookie schedules, and Skill scheduler after every restart or deployment. Registration defaults closed on a new installation and must not be opened until the real SMTP receipt code and an end-to-end direct-registration acceptance test have both succeeded.

## Working Capabilities

- Multi-account official password, QR, and manual-Cookie binding with listener and auto-reply diagnostics.
- Stable Xianyu identity matching through `xianyu_unb`, so same-user re-login updates the existing account record.
- Persistent official browser profiles under `browser_data/user_<unb>`, with profile-only automatic renewal and no automatic saved-password submission.
- Unified official login APIs and state machine with owner isolation, read-only polling, expiry, cancellation, safe screenshots, and explicit local-browser display.
- One official refresh path that starts only from an explicit user action or one genuinely due schedule. Token and repeated connection failures enter passive `action_required`; active verification keeps one window for up to 15 minutes and closes after real Token validation plus Cookie/User-Agent/listener handoff.
- Product-scoped knowledge and training rules with draft/published separation, copy-to-draft, rule auditing, and guarded price replies.
- User-scoped AI provider profiles with encrypted keys, model discovery, and test-before-apply account switching.
- Recent-order discovery and reconciliation with completion, refund, cancellation, and login-required states.
- Skill Center manual and scheduled monitoring with a 15-minute minimum interval and persisted run state.
- Optional AI result filtering using an enabled account provider configuration.
- Webhook, WeChat, DingTalk, Feishu, Bark, and Telegram result delivery with `sent`, `partial`, and `failed` outcomes.
- Cross-run result deduplication by task and item URL, falling back to platform item ID.
- Expert prompts and real runtime, browser, AI, delivery, and account-listener diagnostics.
- One-transaction direct registration with capacity recheck, image CAPTCHA, purpose-bound email code, `v2` terms acceptance, and automatic login; successful email delivery keeps the completed CAPTCHA state, while explicit resend requires a fresh CAPTCHA.
- Username-or-email login and two-stage email password recovery: `POST /api/auth/password-reset/verify-code` issues a one-time grant held in frontend component memory, then `POST /api/auth/password-reset` consumes it and revokes all older sessions. The legacy reset payload remains temporarily compatible.
- Shared `BrandLockup` presentation across the main sidebar and public login, registration, password-recovery, terms, and privacy views, with the frontend version injected from `frontend/package.json` at build time.
- Administrator SMTP receipt confirmation, 1–1000 ordinary-user capacity, user enablement, and guarded registration switch controls.
- Ordinary-user personal item-sync settings with per-field global inheritance, plus user-owned AI provider access without administrator settings calls.
- One-request role-aware dashboard summaries, retryable error and empty states, deferred order details, and a separately loaded chart bundle.
- Purpose-isolated HMAC storage for authentication secrets and identifiers, persistent multi-dimensional rate limits, and trusted-proxy client-IP handling.

## Important Boundaries

- Training uses the current product draft; real buyer replies use only the published knowledge snapshot.
- Copying knowledge writes target drafts only, defaults to no overwrite, and never publishes automatically.
- Deleting an account removes account-linked data. Re-login or update the Cookie instead of deleting for session recovery.
- Scheduled Cookie refresh and Skill monitor schedules both default off. Cookie refresh allows 1 hour to 7 days; Skill monitoring allows 15 minutes or longer.
- Token and connection failures never launch Chrome, regardless of the schedule switch; manual start remains available and scheduled launch occurs once only when enabled and due.
- Goofish rejects headless Chromium. Official renewal uses a headed off-screen browser and becomes visible for human verification.
- Alibaba SMS, QR, face, and risk-control verification stays manual. A profile can renew without another scan only while its official session remains usable; logged-out profiles wait for the user in the same session.
- Skill Center notification delivery excludes QQ and email even though those channel types may exist elsewhere in the database.
- Capability readiness does not guarantee an external AI provider or notification endpoint will remain reachable.
- The scheduler depends on the intentional one-process, one-Uvicorn-worker runtime.
- Registration defaults off and cannot be enabled without a receipt-confirmed current SMTP fingerprint and remaining ordinary-user capacity.
- SMTP verification sends a six-digit code to the independent support mailbox and has no third-party fallback. Missing credentials, failed delivery, an unconfirmed code, database errors, or changed SMTP settings keep registration closed.
- CAPTCHA, email, and SMTP challenges expire after 10 minutes and stop after five attempts. Historical invite data is retained, while legacy invite APIs return HTTP 410.
- Password-reset grants are email-bound, expire after 10 minutes, and are single-use. The frontend keeps plaintext grant material only in component memory, and the backend stores only a purpose-isolated digest in the existing `auth_challenges` table.
- The system-secret key is independent from the AI-provider and Xianyu-account keys; all three local key files must be restored with SQLite when environment keys are absent.
- Authentication logs and runtime sessions must not expose Cookies, Tokens, verification URLs, the default administrator password, OTPs, reset grants, full email addresses, or passwords.

## Verification Baseline

Run before release or deployment:

```bash
source .venv/bin/activate
pip install -r requirements-dev.lock
ruff check .
python -m py_compile Start.py app_factory.py application_runtime.py api_routers.py auth_email_service.py auth_registration_service.py settings_service.py db_manager.py schema_migrations.py security_utils.py session_registry.py official_login_sessions.py repositories/auth_repository.py repositories/runtime_session_repository.py services/auth_service.py ai_provider_service.py ai_reply_engine.py account_session_refresh.py order_sync_service.py skill_monitor_scheduler.py skill_monitor_mtop_adapter.py skill_monitor_shadow.py skill_monitor_ai_contract.py reply_server.py XianyuAutoAsync.py utils/xianyu_official_login.py utils/xianyu_session_probe.py
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

The automated suite covers official login modes, profile promotion and reuse, non-password automatic renewal, verification timeout and cancellation, account data retention, listener cancellation timeouts and health responsiveness, Skill scheduler lifecycle and locking, success/failure rescheduling, AI filtering, supported-channel filtering, multi-channel notification outcomes, cross-run deduplication, registration transactions and races, challenge expiry and attempts, rate limits, trusted proxies, SMTP failure behavior, progressive reset grants, session revocation, public auth views, and administrator registration interactions. Real platform acceptance still requires operator-owned Xianyu, AI provider, notification, and SMTP accounts.

Deployment and live-account behavior were verified on 2026-07-17; release gates were rerun on 2026-07-18 after the final diagnostics wording cleanup:

- Release gates passed: Ruff, strict Python compilation, 274 backend tests, zero high-severity npm audit findings, TypeScript, 17 frontend files with 75 tests, two production builds, and build verification. The build contained 31 assets in each retained generation, zero orphans, and a 245,200-byte entry bundle. The final Gitleaks working-tree scan reported no findings.
- Authenticated synthetic `action_required` checks at 1440x900 and 390x844 had no horizontal overflow, console errors, duplicate start action, local-browser action, cancel action, or manual-completion action. Product-list/detail logs emitted bounded summaries, and structured account identifiers passed through the stable-identity masker.
- Deployment used a hash-verified mode-`0700` rollback snapshot outside the repository containing an integrity-checked SQLite backup, all three local keys, browser profiles, prior static assets, and prior live source. The single launchd worker became ready within three seconds; local and public readiness reported migration `2026071701`, and both HTML entries plus every referenced asset matched the deployed files.
- Startup normalized orphaned active refresh states to passive `action_required`. The first-account acceptance used exactly one start request and one same-window display request; state advanced `refreshing → verification_required → success` only after a real message Token passed. The browser closed after one Cookie/User-Agent/listener handoff. During the following 900 seconds, 60 local and 15 public health checks passed with no application browser, Chrome-for-Testing process, state drift, or repeated validation session.
- After final log hardening, a further 60-second observation produced 13 healthy local checks and no application browser. The 82 new log lines contained no raw stable account identifiers, Cookie values, verification URLs, full item payloads, error-level entries, or tracebacks. Both listeners remained enabled; the first account's explicitly restored schedule remained 360 minutes and the second account schedule remained disabled.
- The 2026-07-18 documentation and diagnostics reconciliation deployment used a fresh integrity-checked rollback snapshot outside the repository. Local and public HTML referenced `index-Mdv84IwF.js` and `index-OgVJmvqL.css`, and both responses matched the deployed files byte-for-byte. A final listener-bootstrap masking correction was then deployed with its regression test. During the final 60-second observation, 13 local and 5 public readiness checks passed with zero application-browser samples. Both listeners and their 360-minute-enabled/disabled schedule settings were preserved; both accounts remained in passive `action_required`. All 450 log lines from the final process contained no raw stable account identifier, Cookie value, verification URL, traceback, or error-level entry.

## Next Acceptance Steps

- Supply and explicitly approve one dedicated test Xianyu account. Until then, do not select either existing account, do not read a production Cookie for search, and keep MTop shadow and end-to-end value acceptance blocked.
- With that dedicated account, first prove the registered `iPhone 15 Pro` canary is non-empty on the official page, then run the same-account, same-query, near-time Playwright/MTop shadow matrix described in `docs/stage-c-offline-acceptance.md`. Zero hits cannot satisfy this canary.
- Require the GitHub `secrets` and `test` jobs to pass for the exact release commit; local evidence above does not replace CI.
- Recheck the first account after its next genuinely due 360-minute schedule. Require one background official session at most, no early launch after Token or connection failures, and the same human-verification behavior if the platform asks again.
- Keep the second account Cookie schedule disabled unless the operator explicitly changes it.
- Complete password-reset acceptance with two old sessions: verify the email code before entering a new password, consume the in-memory grant, confirm both old sessions are rejected, confirm replay and the old password fail, and verify the new password through both username and email login.
- Keep Skill schedules default off and keep account-level scheduled Cookie refresh off unless an operator explicitly needs preventive renewal.
- Keep monitoring official page, SMTP, AI-provider, and notification changes; do not weaken human verification, rate limits, or secret-handling boundaries to improve automation rates.
