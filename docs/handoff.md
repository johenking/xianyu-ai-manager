# Handoff

## v1.8.2 Production Release On 2026-07-24

The dual-QR login release was merged through PR `#35` at
`a860834a8d73694b7f6c383b7e6b27f96e3c9abb`. Live QR acceptance then exposed
one raw stable account identifier in an early database persistence log; PR
`#36` moved that redaction to the log source. The final application commit is
`29f523659c091526bd393bc3b797dce7fb4570ea`. This document update is docs-only;
after it merges, production can fast-forward without another restart. Release
closure will place the annotated `v1.8.2` tag on this final evidence
merge after production fast-forwards to it; the loaded application payload
remains byte-equivalent to `29f5236`.

GitHub and clean-worktree gates:

- PR `#35` CI run `30096458035` and its `main` push run `30096628378` passed
  both `secrets` and `test`. PR `#36` CI run `30099092672` and final `main`
  push run `30099265493` passed the same jobs. Both PRs used ordinary merge
  commits. No historical `v1.8.1` tag was created.
- The feature candidate passed Ruff, the explicit Python compilation list, 317
  backend tests with isolated SQLite and keys, TypeScript, 18 frontend files
  with 92 tests, 27 targeted account-panel tests, 6 extension tests, and
  `npm audit --audit-level=high` with zero vulnerabilities. The final log-source
  correction used a verified red-to-green regression and passed the same Ruff
  and compilation gates plus 318 backend tests in 25.021 seconds.
- Two builds from exact feature merge `a860834` were reproducible. The entry
  assets are `index-izG0227c.js` (245,344 bytes, SHA-256
  `1553754b77ba837f69e3dce3bd2263ee92d76d05fc89dbef8adbbb18ef1664dc`)
  and `index-Do2LTy2E.css` (76,129 bytes, SHA-256
  `3c2966b0f0b8dfa6d494834194cc053727f905bef621a8b8d6d98227edc0a85c`).
  Build verification retained two 32-asset generations with zero orphans and
  a 71.7% entry reduction. Extension verification passed; its ZIP SHA-256 is
  `373a1b9ebb424ce3276b71a3818def994d04c367066e93947c2e161ebf32f106`.

Production deployment and runtime evidence:

- Production is `/Users/mac/Library/Application Support/XianyuManager`, served
  by `com.cxywjx.xianyu-manager` with `WEB_CONCURRENCY=1`. It was advanced only
  with `git merge --ff-only`. Final process `14740` became ready in four seconds
  from commit `29f5236`; the migration remained `2026072301`.
- The mode-`0700` pre-deploy snapshot is outside the repository at
  `/Users/mac/Library/Application Support/XianyuManager Rollbacks/` under
  `v1.8.2-pre-deploy-20260724-212959`. Its 2,328-file SHA-256 manifest verifies
  an integrity-checked SQLite backup, all three local keys, the prior static
  site, 49 uploads, four stopped-service browser-profile directories, the
  LaunchAgent, source archive, verified Git bundle, and the prior atomic static
  directory. A second stopped-service post-QR/pre-final snapshot named
  `v1.8.2-post-qr-pre-final-20260724-220658` verifies 1,841 files and preserves
  the newly accepted login state before the final log-only restart.
- Local and public live/ready responses passed with identical migration and
  runtime state. Local and public `/` and `/login` HTML were byte-identical and
  referenced the new entry assets. Every current-generation asset (32/32) and
  the extension ZIP matched disk, local HTTP, and public HTTP byte-for-byte.
  OpenAPI contained 186 paths and 224 methods; all 12 QR, official-session,
  password, and compatibility login routes matched their expected methods. No
  public route or schema migration was added.
- SQLite integrity remained `ok`. Before the user-operated local-Chrome QR
  acceptance, account/user/item/order counts and all three local keys plus 49
  uploads matched the pre-deploy snapshot. The live QR created one new account
  row, produced no duplicate stable identity, promoted one canonical persistent
  profile, left no temporary or backup profile directory, installed one
  listener, reached a connected WebSocket, and closed its Chrome process.
- Browser acceptance covered 1440x900 and 390x844 production viewports plus
  isolated 360/390/430-pixel mobile checks. The account modal opened without a
  request, both QR entries remained visible with at least 44-pixel controls,
  the modal's full scroll range was reachable, and document width never exceeded
  viewport width. Web QR completed a real generation and repeated polling
  without Chrome; closing the modal stopped polling and left TTL cleanup to the
  service. Local Chrome QR completed with user scanning, real platform identity
  resolution, one persistence/listener handoff, and automatic window cleanup.
- A pre-final 31-sample observation over 15 minutes had zero failures: local and
  public readiness stayed healthy, PID and one-listener count were stable,
  migration stayed `2026072301`, and no application Chrome process reappeared.
  The final log-redacted process completed the same 31-sample/15-minute gate
  with zero health-sample failures. Its fresh log contained two error-level
  heartbeat-send timeouts during one WebSocket keepalive interruption. The
  listener then exited the stale socket, entered its normal reconnect path,
  re-established the connection in about 12 seconds, completed Token probing
  and registration, and resumed successful heartbeat responses. The same log
  had no traceback, raw account create/update identity, long numeric identity
  candidate, Cookie, Token, password, bearer value, QR content, or verification
  URL.

Acceptance boundary: unit/UI tests, isolated SQLite, CI, and candidate builds
are automated or mocked evidence. Web QR generation/polling, the local Chrome
QR scan, real identity/Profile persistence, WebSocket listener handoff, public
asset equality, and both production observation windows are live evidence. No
real password, Cookie, Token, account identifier, key, verification URL, database
content, or browser profile is recorded in this document.

## v1.8.0 Production Release On 2026-07-23

The audited application payload was merged through PRs `#30` and `#31` and
deployed from commit `f6fb00ecab788946627588e82e2c9a315d173560`. The final
docs-only release merge does not change application, frontend, extension,
migration, or dependency content. The authoritative final release SHA is the
commit resolved by both `origin/main` and the annotated `v1.8.0^{}` tag; this
document is part of that tagged commit.

GitHub and clean-worktree gates:

- PR `#30` merged the production synchronization through ordinary merge commit
  `dd5807c3d8078f4f04934376f0d10ddfe1a79d03`. Its PR CI run
  `30017648878` passed.
- PR `#31` fixed the `RemoteImage` source-change race and merged through
  ordinary merge commit `f6fb00ecab788946627588e82e2c9a315d173560`. Its PR CI
  run `30021408943` and subsequent `main` push CI run `30021629945` both
  passed all `secrets` and `test` jobs.
- The final code gates passed Ruff, the explicit Python compilation list, 302
  backend tests with an isolated `DB_PATH`, TypeScript, 18 frontend files with
  87 tests, 6 extension tests, and two production builds.
- `npm audit --audit-level=high` reported zero vulnerabilities. Build and
  extension verification passed; the build retained two generations of 32
  assets with zero orphans. OpenAPI contained 186 paths and 224 methods, all
  required login/session routes were present, and all three retired QR
  refresh/cooldown routes were absent.

Production deployment and rollback evidence:

- The stopped-service rollback snapshot is
  `backups/v1.8.0-final-pre-deploy-20260723-235008`. It contains 2,191
  hash-listed files plus the manifest, an integrity-checked SQLite backup, all
  three local encryption keys, four complete browser profiles, 49 uploads, the
  prior static site, the LaunchAgent plist, a source archive, and a verified Git
  bundle. The earlier alignment snapshot
  `backups/v1.8.0-pre-align-20260723-230353` and local rollback branch
  `codex/prod-pre-align-20260723` at
  `e76c24a74f40cfa51c8151d84f30265d305fb2e9` are retained.
- Production was advanced with `git merge --ff-only`; no reset or working-tree
  overwrite was used. The formal virtual environment then passed the same 302
  backend tests against an isolated database. The formal frontend passed 87
  tests, 6 extension tests, zero-high-severity audit, TypeScript, two builds,
  build verification, and extension verification.
- The LaunchAgent became ready in three seconds with working directory
  `/Users/mac/Library/Application Support/XianyuManager`,
  `WEB_CONCURRENCY=1`, one process listening on `8091`, and no listeners on
  `8092` or `8093`. Local and public readiness reported migration
  `2026072301`.
- Local and public `/`, `/login`, `/register`, `/forgot-password`, `/terms`,
  and `/privacy` returned 200 and matched the deployed HTML byte-for-byte. The
  referenced `index-pN18c_Tf.js`, `index-kJg_YYPB.css`, and extension ZIP all
  returned 200 locally and publicly and matched disk. The extension ZIP SHA-256
  is `373a1b9ebb424ce3276b71a3818def994d04c367066e93947c2e161ebf32f106`.
- Database integrity remained `ok`. Account/key/profile/upload counts remained
  `1/3/4/49`; item, order, knowledge-profile, AI-profile, monitor-task, and user
  counts remained `106/63/12/2/2/2`. The 49 upload files matched the rollback
  snapshot exactly, with aggregate content SHA-256
  `887cc8b078dfa1ee3860af1790401663008470d108e294789ad8057bd4988f63`.
- A byte-offset scan covered 128 new lines across the three production logs.
  Cookie assignments, raw `unb`, Tokens, bearer values, passwords, session
  secrets, QR content, verification URLs, known stored sensitive values,
  tracebacks, and error-level records each had zero matches. No real secret or
  account material was printed during acceptance.

## v1.8.0 Sync Candidate On 2026-07-23

The `codex/sync-production-20260723` branch starts from GitHub `main` at
`ecde9cf1a72d1d63c1971ad59be7357c7a30b22c` and imports the audited production
source snapshot without runtime data. The local-only snapshot branch and its
SHA-256 manifest exclude SQLite, all three local keys, Cookies, Tokens, `.env`,
logs, browser profiles, uploads, backups, generated static assets, archives,
PIDs, virtual environments, and agent metadata. GitHub CI, PR merge, production
source alignment, and the `v1.8.0` tag remain separate gates and are not implied
by the local results below.

Candidate gates completed before the release commit:

- Ruff and the explicit Python compilation list passed.
- All 302 backend tests passed with an isolated temporary `DB_PATH`.
- TypeScript passed; all 17 frontend files with 86 tests passed; all 6 extension
  tests passed; `npm audit --audit-level=high` reported zero vulnerabilities.
- Two production builds retained two generations of 32 assets with zero
  orphans; the 245,239-byte entry remained 71.7% below the baseline.
- OpenAPI contained 186 paths and 224 methods, all required login/session routes
  were present, and all three retired QR refresh/cooldown routes were absent.
- Gitleaks and `git diff --check` passed. The extension source and public ZIPs
  are now built reproducibly from ten allowlisted files; two consecutive builds
  produced the same SHA-256 and package verification passed.

These candidate results are retained as pre-release evidence. The GitHub,
rollback, deployment, local/public health, asset-equality, data-preservation, and
post-start log gates completed in the production release record above.

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
python -m py_compile Start.py app_factory.py application_runtime.py api_routers.py auth_email_service.py auth_registration_service.py settings_service.py db_manager.py schema_migrations.py security_utils.py session_registry.py official_login_sessions.py repositories/auth_repository.py repositories/runtime_session_repository.py services/auth_service.py ai_provider_service.py ai_reply_engine.py account_session_refresh.py order_sync_service.py skill_monitor_scheduler.py reply_server.py XianyuAutoAsync.py utils/xianyu_official_login.py utils/xianyu_session_probe.py
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

- Require the GitHub `secrets` and `test` jobs to pass for the exact release commit; local evidence above does not replace CI.
- Recheck the first account after its next genuinely due 360-minute schedule. Require one background official session at most, no early launch after Token or connection failures, and the same human-verification behavior if the platform asks again.
- Keep the second account Cookie schedule disabled unless the operator explicitly changes it.
- Complete password-reset acceptance with two old sessions: verify the email code before entering a new password, consume the in-memory grant, confirm both old sessions are rejected, confirm replay and the old password fail, and verify the new password through both username and email login.
- Keep Skill schedules default off and keep account-level scheduled Cookie refresh off unless an operator explicitly needs preventive renewal.
- Keep monitoring official page, SMTP, AI-provider, and notification changes; do not weaken human verification, rate limits, or secret-handling boundaries to improve automation rates.
