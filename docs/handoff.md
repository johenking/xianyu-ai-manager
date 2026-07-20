# Handoff

## Live rollout attempt and hard rollback on 2026-07-20

The dark-deployment candidate at `b027c4b` was staged, migrated, and then
rolled back before acceptance because the original LaunchAgent could not spawn
its Python runtime. This is a deployment-environment blocker, not evidence that
the candidate application started or failed:

- The external rollout backup is
  `/Users/mac/Documents/Codex/backups/xianyu-monitor-rollout-20260720-110814`.
  Its final manifest covers 2,453 files (including 13 migration-rehearsal
  files), has root SHA-256
  `0bea0c78f08c4a93407df9f4172d8646c48d63f4a4a94f7bb2eb2d0cd2ffeaad`,
  contains zero symlinks, and passed full file-hash and 0700/0600 permission
  verification.
- The candidate staging copy ran under `sandbox-exec` with outbound networking
  denied, `env -i`, one worker, and `127.0.0.1:18092`. `/health`,
  `/health/live`, and `/health/ready` returned 200 with migration
  `2026071802`; the candidate entry bundle and all referenced assets returned
  200. It had zero account listeners, schedules, delivery channels, Cookie
  refreshes, or remote sockets and shut down gracefully.
- The live source preconditions matched immutable baseline `dd0e8f2`. The
  allowlisted runtime files and candidate static entry were copied, the live DB
  migrated from `2026071701` to `2026071802`, and all four monitor flags were
  explicitly false. Before restart, SQLite integrity was `ok`, foreign-key
  violations were zero, all new workflow/outbox tables were empty, and the two
  accounts, two listener flags, one Cookie-refresh setting, two old tasks, zero
  schedules, zero notification channels, three key hashes, uploads, and legacy
  static helper were unchanged.
- `launchctl` then reported `EX_CONFIG 78` without starting the application or
  updating its application logs. Fresh unified-log evidence identified the
  actual error as `posix_spawn(.../.venv/bin/python): Operation not permitted`.
  A system-Python probe exited 0, while a shell-to-Anaconda probe with the live
  `Documents` working directory stopped at `getcwd: ... Operation not
  permitted`; this confirms a macOS TCC/Files-and-Folders restriction on the
  current runtime location. All probes were stopped and removed.
- The bound rollback condition was therefore applied. Runtime source, generated
  static assets, SQLite, all three keys, configuration, LaunchAgent definition,
  browser profiles, and uploads now match the immutable pre-rollout set. The DB
  is back at `2026071701` with SHA-256
  `8397593b8d3e088399d12acd278d110f848c880413766ce346d914d436a55fa7`,
  integrity `ok`, and zero foreign-key violations. The LaunchAgent is unloaded
  and port 8091 is not listening, matching the pre-rollout outage state.

Redacted evidence is under
`/Users/mac/Documents/Codex/evidence/xianyu-monitor-rollout-20260720-1134`.
No production search, MTop request, Webhook, AI call, schedule, or monitor
delivery was enabled. The candidate remains offline-only and must not be called
deployed. Before another rollout, move the persistent runtime out of the
macOS-protected `Documents` tree (preferred) or complete and verify the required
macOS privacy grant, then take a fresh external backup and repeat the full
staging/migration/rollback gates.

## Monitor Integration Candidate On 2026-07-18

The monitor work is isolated in `/Users/mac/Documents/Codex/integration/xianyu-monitor-20260718` on branch `codex/monitor-integration-20260718`. The immutable source baseline is commit `dd0e8f2`, tagged `codex/live-source-baseline-20260718-043547`; its provenance records 201 allowlisted source files, zero symlinks, sanitized remotes, and Stage A backup root hash `e6983cb07117c477bab2366bc26b2355789797255d5735b541b4c9ca39d3a08d`. Stage B and its evidence record end at `0272de1`; Stage C is the following isolated offline-integration commit.

## Monitor hardening and dark-deployment candidate on 2026-07-20

This candidate is still isolated on the integration branch and has not been
deployed to live. It adds the minimum hardening found in the pre-deployment
audit:

- risk-control/captcha detection is fail-closed and never invokes a slider
  solver; an account action-required result atomically pauses its schedule;
- empty-account claims atomically write an auditable `action_required` run,
  clear `schedule_enabled`/`next_run_at`, and due-task polling excludes blank
  account bindings;
- expired monitor deliveries, events, result identities/results, runs,
  request budgets, and MTop breaker rows are cleaned child-first by a
  local-only startup janitor plus a six-hour loop; `retention_until IS NULL`
  and live run/delivery leases are preserved, and cleanup is idempotent;
- schedule creation/activation, notification activation, and account binding
  are enforced at the API boundary as well as in the UI. The Skill Center
  derives run/schedule/delivery gates from capability evidence, keeps the
  existing shell/tokens/components unchanged, and leaves the emergency
  “close an already-enabled schedule” action available while dark;
- the truthful six-field capability matrix now includes ready account/task
  identifiers and explicit operation-gate evidence. Code presence still does
  not imply configuration, real search, scheduled execution, AI judgment, or
  confirmed delivery.

Fresh verification for this candidate:

- `ruff check .` and the complete project compilation list exited 0;
  `python -m unittest discover -s tests -v` exited 0 with **349 tests**.
- `cd frontend && npm run typecheck` exited 0; `npm test` exited 0 with
  **17 files / 87 tests**; `npm test -- SkillCenter.test.tsx` exited 0 with
  **16 tests**. `npm audit --audit-level=high` found 0 vulnerabilities.
- Two independent production builds were byte-identical at the retained
  entry/manifest hashes; `npm run verify:build` exited 0 with 31 assets,
  zero orphans, and a 245,200-byte entry bundle (71.7% below baseline).
  Build evidence is under
  `/Users/mac/Documents/Codex/evidence/xianyu-monitor-build-20260720`.
- `pip-audit --disable-pip --no-deps -r requirements.lock` and the same
  command for `requirements-dev.lock` both exited 0 with no known
  vulnerabilities. Gitleaks 8.30.1 scanned the current worktree and exited 0.
- Fresh mocked production-build UI evidence is under
  `/Users/mac/Documents/Codex/evidence/xianyu-skill-center-dark-ui-20260720-105635`.
  Both 1440x900 and 390x844 reached the bottom with equal document/client
  widths, no console/page/request errors, no blocked or successful external
  requests, and one initial request per Skill Center endpoint. Runtime
  assertions confirmed run, schedule activation, schedule/notification form
  switches were disabled while dark, while creating an unscheduled draft
  remained enabled. The evidence is local/mocked only.

The rollout gate remains: no dedicated test account is available, so no
Playwright/MTop search, shadow comparison, real Webhook, real AI call, or
schedule enablement is permitted. The proposed live state keeps all four
monitor flags false, preserves the two existing account listeners, Cookie
refresh, and item sync, and requires a fresh external backup plus migration
rehearsal before any service restart. Rollback is the pre-deployment source,
SQLite, keys, browser-data, static, uploads, configuration, and LaunchAgent
snapshot as one set.

### Skill Center refresh fencing on 2026-07-19

The Skill Center keeps the accepted v1.7.3 shell, tokens, and shared
`PageHeader`, `WorkSurface`, `StatusBadge`, and `InlineNotice` components. No
shared UI component, stylesheet, API, schema, database, or live runtime was
changed. The monitor snapshot fetch is now side-effect free; accounts,
capabilities, tasks, and results commit together only for the latest mounted
request generation. Effect cleanup invalidates the development StrictMode
probe and real unmounts, stale success/error/finally paths cannot write state,
and the test-account fallback uses a functional state update so refresh does
not replace an operator selection. A failed refresh after a successful
snapshot keeps the prior data with an explicit “current display is the last
successful data” notice; the next successful refresh clears that notice.

The pre-fix focused reproduction passed 10 of 13 tests and failed the three
race/stale-state cases: an older StrictMode response overwrote the newer
snapshot, an older rejection surfaced after a newer success, and retained
data lacked a stale marker whose error also survived recovery. Fresh post-fix
local checks passed:

- `cd frontend && npm test -- SkillCenter.test.tsx` — exit 0, 1 file and 13 of
  13 tests. The deferred tests cover older success/rejection fencing,
  unmount, monitor rejection propagation, stale/recovery UI, a real refresh
  action, one tasks/results request per non-StrictMode refresh, and preservation
  of the selected test account.
- `cd frontend && npm run typecheck` — exit 0; `npm test` — exit 0, 17 files
  and 84 of 84 tests. `npm audit --audit-level=high` — exit 0, zero
  vulnerabilities.
- With the integration worktree as the current directory and the existing
  local dependency runtime at `/Users/mac/Documents/咸鱼监控台/.venv`,
  `ruff check .` and the project `py_compile` gate exited 0;
  `python -m unittest discover -s tests -v` exited 0 with 335 of 335 tests.
  The integration worktree intentionally contains no `.venv`.
- Two independent `npm run build` executions against the exact working source
  each produced 31 assets. The project `npm run verify:build` script exited 0
  against that repository-external output: two retained 31-asset generations,
  31 asset files, zero orphans, and a 245,200-byte entry bundle (71.7% below
  the retained baseline). Both Python lock files passed `pip-audit --no-deps
  --disable-pip` with no known vulnerabilities. Gitleaks 8.30.1 scanned a
  symlink-preserving archive of all 230 current tracked files (about 4.23 MB)
  with the repository allowlist and reported no leaks.

Fresh browser evidence is under
`/Users/mac/Documents/Codex/evidence/xianyu-skill-center-ui-20260719-022100`
and is local mocked production-build evidence only. It contains 43 files,
zero symlinks, mode-0700 directories, mode-0600 files, a passing SHA-256
manifest, and manifest hash
`8ec5a39bab80fb0788a38851149ddb8ae81a2b49e7cb83f845376b9487801135`.
A loopback synthetic API
and a browser rule that blocked every non-loopback request covered 1440x900
and 390x844 stale-error and recovered states, full-page scrolling, and the
existing disabled monitor controls. Both viewports reached the bottom; their
document and main content scroll widths equalled their client widths. The
mobile segmented navigation retained its existing internal horizontal scroll,
and its final segment was reachable. There were zero console warnings/errors,
page errors, failed network requests, blocked external attempts, unknown API
routes, or successful external requests. Each production-preview initial load
called accounts, capabilities, monitor tasks, and monitor results exactly once.
Development StrictMode still intentionally starts two effect rounds, while the
deferred regression tests prove that only the newest generation can commit.

This refresh-fencing evidence did not deploy live, select or read either
existing Xianyu account, issue a Playwright or MTop search, enable a schedule,
send a notification, or call an AI provider. The dedicated-test-account gate,
the `iPhone 15 Pro` official-page canary, shadow comparison, and real value
acceptance therefore remain blocked/unverified.

Stage A remains recoverable from `/Users/mac/Documents/Codex/backups/xianyu-monitor-stage-a-20260718-043547`. Stage B adds fail-closed monitor, scheduler, delivery, and experimental MTop feature flags; expand-only schema changes; persistent run and delivery claims with independent tokens, leases, heartbeats, and stale recovery; transactional first-seen events and delivery outbox rows; stable delivery idempotency keys with explicit `unknown` outcomes; Cookie revision compare-and-swap; and owner/account/revision-bound Playwright search with isolated profiles and no anonymous fallback. No complete MTop response is retained.

Stage C adds a default-off, offline-testable MTop adapter without selecting it from manual or scheduled monitor execution. It provides a fixed-endpoint request contract, response-size and schema validation, item-field allowlisting, deterministic pagination/filter/sort normalization, account/global budgets, bounded jitter and `Retry-After`, a persistent circuit breaker with one leased half-open probe, Cookie revision CAS, a normalized shadow comparator, and strict lease-scoped AI JSON parsing. The runtime monitor path still explicitly claims `source_adapter=playwright`; the MTop adapter has made no real request.

The API and Skill Center UI now report six independent claims: `code_present`, `config_ready`, `last_real_search`, `last_scheduled_run`, `last_ai_decision`, and `last_real_delivery`. Missing evidence is rendered as never verified, never run, never judged, or never confirmed. Code presence is not configuration, runtime, AI, or delivery evidence.

Stage F adds an internal-only mocked search-provider seam for offline runtime
acceptance. Injected runs are forced to source_adapter=mocked,
provider_mode=mocked, evidence_scope=mocked_provider, and
is_real_data=false; the production default remains Playwright with the real
data gate. Mocked results and loopback deliveries are excluded from
last_real_search and last_real_delivery. The delivery dispatcher now fences
every still-owned sending claim to unknown synchronously during shutdown
before cancelling worker tasks, so a slow transport cannot leave a permanent
sending row.

Fresh Stage F local runtime acceptance passed on 2026-07-18 with
scripts/stage_f_offline_runtime_acceptance.py. Evidence is outside the
repository at
/Users/mac/Documents/Codex/evidence/xianyu-monitor-stage-f-20260718-154307-51511;
the evidence directory is mode 0700, its files are mode 0600, and its root
hash is
3811e17fcbeae4066b8c1b2fba8080267b8c73abb6d4b26b69afa674ddb2869a.
The run used a disposable DB, three temporary 0600 keys, one disabled
synthetic account, a local Idempotency-Key receiver, and a sandbox profile
whose non-loopback canary was blocked (exit 6). The real scheduler loop
completed 2 raw -> 1 accepted result, first-seen event, outbox claim, and
loopback sent delivery; truthful real-search and real-delivery candidates
remained zero. Graceful shutdown recorded
interrupted/shutdown_interrupted for the run and
unknown/dispatcher_interrupted for the slow delivery. After SIGKILL, stale
run recovery recorded interrupted/lease_expired, stale delivery recovery
recorded unknown/send_outcome_unknown, a successor run reached attempt 2 with
the correct predecessor, no delivery remained sending, and all five
old-token writes were rejected. Every worker was a single process on
127.0.0.1:18091; health was 200, migration was 2026071802, integrity was ok,
foreign-key violations were 0, and all temporary runtime data was removed.

Current capability matrix after Stage F:

- code present — local: monitor, scheduler, outbox, leases, mocked seam, and
  MTop offline contract are present in this branch.
- config ready — unverified/not live: production flags remain default-off and
  no approved dedicated account, real notification endpoint, or real AI
  configuration is available.
- last real search — never/real unverified: Stage F used only the mocked
  provider; no Playwright or MTop shadow request was made.
- last scheduled run — mocked/local only: the scheduler loop completed in the
  disposable DB, with source_adapter=mocked.
- last AI decision — mocked contract only/no real decision: Stage F disabled
  AI calls; existing fake-provider tests are not provider acceptance.
- last real delivery — never/real unverified: the only delivery was to the
  loopback Idempotency-Key receiver and is excluded from real evidence.
- MTop shadow and value acceptance — unverified/blocked: the dedicated test
  account and official-page non-empty canary have not been supplied.

Fresh Stage C local verification on 2026-07-18 passed:

- `ruff check .` and the project `py_compile` gate exited 0; `python -m unittest discover -s tests -v` passed 330 of 330 tests. The Stage C monitor-only discovery passed 61 of 61 tests.
- `npm audit --audit-level=high` reported zero vulnerabilities; TypeScript exited 0; Vitest passed 17 files and 77 tests.
- Two independent Vite builds each produced 31 assets. `verify:build` found 31 of 31 retained assets, zero orphans, and a 245,200-byte entry bundle, 71.7% smaller than the retained baseline.
- Both exact Python lock files passed `pip-audit --no-deps --disable-pip` with no known vulnerabilities.
- Gitleaks 8.30.1 scanned all 229 tracked and prospective source files (about 4.11 MB) from a symlink-preserving temporary snapshot and reported no leaks.
- A fresh database reached `2026071802` with 43 application tables, 9 migration rows, `integrity_check=ok`, zero foreign-key violations, both new tables, and both retention indexes. The exact pre-Stage-C source at `0272de1` opened a copy of that expanded database with the same schema version, row counts, integrity result, and foreign-key result.
- Local mocked UI review covered default-off, loading, empty, missing-evidence, and synthetic error states at 1440x900 and 390x844. Default and loading runs had zero console warnings/errors, page errors, failed requests, blocked external attempts, or successful external requests; the intentional error fixture produced only its expected local HTTP 500 resource message. Full-scroll document/main widths were 1440/1440 and 390/390, and the MTop panel stayed inside the content rail. Evidence is under `/Users/mac/Documents/Codex/evidence/xianyu-monitor-stage-c-ui-20260718-075104` and is local mocked evidence only.

This branch has not been deployed. It has not read or called either existing Xianyu account, used a production or dedicated test Cookie, made an MTop or Playwright shadow request, sent a real Webhook or other notification, or called a real AI provider. Schedule and delivery switches were enabled only inside the disposable Stage F harness; they remain unchanged and default-off in live. The existing live service does not yet expose this truthful capability matrix. The registered `iPhone 15 Pro` canary remains `unverified`; the historical live evidence below belongs to the official-login deployment and must not be treated as monitor-integration or real-provider acceptance.

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
