# Handoff

## v1.10.1 Login Verification Hotfix Release On 2026-07-29

This hotfix keeps QR, SMS and password login in the same server-Chrome session
through slider, face, SMS and unknown interactive verification. Page or tab
replacement no longer ends monitoring. Browser shutdown still follows real
message-Token validation, identity matching and account persistence; a scanned
QR, page text, an ordinary Cookie or one Page closing is not success.

The web console now exposes owner-scoped, no-store browser frames plus bounded
gesture, wheel, text and safe-key actions. Remote ordinary users may start an
offscreen SMS login and complete it in this surface. Displaying the physical
server window remains restricted to an administrator on the loopback console.
Mobile-scan verification remains a scannable image. The Chrome extension is an
explicit advanced import option rather than the automatic risk-control path.

Local release gates completed on the candidate source: Ruff, the explicit
Python compilation list, Gitleaks preparation, the OpenAPI snapshot and
`git diff --check` passed. The complete backend suite passed 704 tests in
153.939 seconds. The frontend passed 24 files with 147 tests, TypeScript, npm
audit with zero vulnerabilities and two byte-identical production builds; the
extension passed 6 tests. The OpenAPI contract adds two owner-scoped interaction
methods and now contains 229 methods. No schema migration or dependency change
is part of this hotfix.

An isolated single-worker candidate on `127.0.0.1:8092` was exercised as an
ordinary web-console user at desktop and mobile widths. It opened the real
public Goofish SMS-login page in the authenticated live interaction surface and
exposed bounded text, pointer and safe-key controls. Cancellation returned in
0.330 seconds; a fresh process inspection found no candidate Chrome/Profile
residue and the candidate log contained no error or traceback. This rehearsal
also found and fixed a cross-thread Playwright cancellation path before the
complete backend suite was rerun.

No registered Goofish account credentials, SMS code, slider or face challenge
were submitted during this isolated rehearsal. Consequently, real account Token
validation, identity matching and persistence through a naturally triggered
platform risk-control chain remain an explicit post-release human canary, not a
claimed result.

Production release evidence:

- Application commit `8d6e78c66bfec3a631a99fbe02b376f6a8634d3d`
  was pushed directly to `main`. GitHub Actions run `30447116945` passed
  `secrets` in 9 seconds and the complete `test` job in 4 minutes 15 seconds.
- The mode-`0700` rollback unit
  `/Users/mac/Library/Application Support/XianyuManager Rollbacks/v1.10.1-pre-deploy-20260729-192456`
  retains the previous `v1.10.0` source, SQLite backup, local keys, browser
  Profile, static assets, uploads, extension, LaunchAgent and Git bundle. Its
  SQLite integrity check and all 2,338 SHA-256 entries passed after the service
  stopped.
- Production fast-forwarded to the exact application commit and restarted as
  one worker with PID `40759`. Local and public readiness passed, migration
  `2026072703` remained unchanged, SQLite integrity was `ok`, and the runtime
  session count remained zero.
- Static publication retained two complete generations of 36 assets with zero
  orphan files. The new entry SHA-256 is
  `c78c80574ecf365e9c64cf8b8e0eb8f761586377da59afd3c36e1b98b09a947c`;
  the extension ZIP SHA-256 is
  `e344821ef36e4aecc7da6e0f9a8c0ce22ee31ed0fe934b30ff637ec96cd450ef`.
  All 76 checked files matched byte-for-byte on disk, localhost and the public
  HTTPS origin.
- A 67-second zero-retry window passed 10 local and 10 public readiness samples
  with a stable PID. One earlier public request exceeded an 8-second client
  timeout; five immediate probes and the complete replacement window all
  returned HTTP 200, with the slowest replacement request taking 3.195 seconds.
- An existing authenticated console session was used read-only to confirm that
  the public account modal advertises same-page QR verification and keeps the
  extension under advanced methods. No login session was started and no account
  state was changed. New production log output contained no traceback, error
  level, HTTP 5xx or detected sensitive value.

The remaining human canary is deliberately unchanged: a registered user must
complete a naturally triggered slider, face or SMS verification and confirm
that real Token validation, identity matching and account persistence occur
before the browser closes. This release does not claim that external platform
canary.

## v1.10.0 Production Release On 2026-07-29

PR `#45` squash-merged the order-sync, analytics and security hardening release
as `7a4f4726b3facea7a9e0a50e785a5275c97b4799`. Production runs that exact
application commit and annotated tag `v1.10.0` points to it. The production
schema is `2026072703`.

Release contract:

- The latest schema is `2026072703`: item metric rows and canary state are bound
  to their account owner, while `fulfillment_attempts` and
  `fulfillment_card_reservations` persist `prepared`, `sending`, `committed`,
  `released`, and `manual_review` outcomes across restarts. Possible external
  side effects after `sending` never permit automatic inventory release.
- Seller metric collection is default-off. A real adapter must be registered
  and each account must independently pass three live canaries before the
  four-hour serial scheduler starts. Synthetic tests do not verify a seller
  backend response path.
- A canary advances only when its batch inserts a new observation newer than
  the account's previous canary observation. Duplicate, reset or out-of-order
  snapshots cannot enable collection.
- Traffic deltas are assigned to the complete interval between consecutive
  snapshots. The API and dashboard expose approximate observation windows and
  duration metadata; they do not turn a four-hour sample into one-hour traffic.
- Order timing uses the saved platform order-time snapshot and its source. It is
  not described as a guaranteed payment, settlement, shipment, or completion
  timestamp.
- The recovered 20-item pre-release security ledger is closed in
  `docs/security-v1.10-closeout.md`; no replacement full scan was launched.

Local release gates completed on 2026-07-29:

- Ruff, the explicit Python compilation list, Gitleaks over the complete
  `origin/main...HEAD` delta, the OpenAPI snapshot and `git diff --check`
  passed. The complete backend suite passed 689 tests in 104.419 seconds with
  an integrity-checked isolated SQLite database.
- The frontend passed 23 files with 145 tests, TypeScript, npm audit with zero
  vulnerabilities and two production builds. Build verification retained 36
  assets with zero orphans and a 71.6% entry-size reduction. The locked Python
  dependency audit reported no vulnerability.
- The repository-outside candidate at
  `/Users/mac/Library/Application Support/XianyuManager Candidates/v1.10.0-20260729-135100`
  used an integrity-checked production database copy and copied all three
  local keys into a mode-`0700` directory. After migration, every candidate
  account was explicitly disabled before the acceptance start; no production
  account state was modified.
- The acceptance start used one Uvicorn process on `127.0.0.1:8092`, migration
  `2026072703`, zero account listener tasks and zero runtime sessions. Local
  live/readiness, SQLite integrity, version `1.10.0`, root HTML, authenticated
  empty states and all four unauthenticated order/metric probes passed. The
  unauthenticated probes returned 401 and the metric adapter remained
  unavailable. The candidate was stopped and port 8092 was verified free.
- Full-page 1440-pixel desktop and 390-pixel mobile captures of the dashboard
  and order center are retained in the candidate `evidence` directory. The
  automated browser recorded no page error, console error or failed request.

The first candidate rehearsal was stopped immediately when two legacy accounts
without matching post-migration status rows defaulted to enabled. The candidate
copy was normalized only after migration and the acceptance restart then had
zero listeners. This is why candidate rehearsals must migrate first and disable
every copied account by joining the final `cookies` table, rather than assuming
that historical `cookie_status` rows are complete.

Production release evidence:

- PR run `30427347536` passed both `secrets` and `test` on the final branch
  head. The exact squash commit then passed both jobs again on main in run
  `30427623552` before deployment.
- The stopped-service, mode-`0700` rollback unit is
  `/Users/mac/Library/Application Support/XianyuManager Rollbacks/v1.10.0-pre-deploy-20260729-142216`.
  Its 2,323-entry SHA-256 manifest, Git bundle, SQLite integrity check, three
  local keys, prior tracked source, static and upload files, browser profiles,
  browser extension and LaunchAgent all verified before the production
  checkout moved.
- The clean production checkout fast-forwarded from `8e9f056` to `7a4f472`.
  The production frontend build reported version `1.10.0`, retained 36 and 35
  assets across two generations with zero orphans, and npm reported zero known
  vulnerabilities.
- Launchd restored one listener with PID `19929`. Local and public live and
  readiness passed, the OpenAPI document reported version `1.10.0` with 227
  methods, SQLite integrity remained `ok`, and 13 local/public readiness pairs
  passed across the post-deploy observation window on migration `2026072703`.
- The two entry assets and HTML matched disk, local HTTP and public HTTPS
  byte-for-byte. Ten critical legacy table counts, all three keys, uploads and
  browser profiles had zero deployment drift. Unauthenticated order, refresh,
  item-performance, item-traffic, metric-status and metric-sync probes failed
  closed with HTTP 401 locally and publicly.
- The post-start slice covered 20 log files and 73,329 new bytes with zero
  Traceback, HTTP 5xx, error/critical entries, raw authorization or Cookie
  material, passwords, or verification URLs.

Real platform order-detail and seller-metric canaries remain external gates.
The unverified detail path and metric adapter therefore stay closed; synthetic
and production infrastructure checks are not recorded as live seller-backend
verification.

## v1.9.1 Login Risk-Control Relay Production Release On 2026-07-29

The login risk-control relay was deployed production-first from commit
`8e9f056d6c8937f4a8a97d80b93b2b84b8dc6a1d`. It keeps interactive slider,
face, and unknown verification inside a user-controlled Chrome window, retains
mobile QR verification as an image flow, and does not report success until a
real platform Token has been validated and the account identity has been
persisted. The browser-extension import protocol is version 2, owner-bound,
five-minute, and single-use; protocol version 1 remains loopback-only.

Deployment evidence:

- The release gate passed Ruff, Python compilation, 484 backend tests, 136
  frontend tests, 34 focused AccountList tests, 6 extension tests, TypeScript,
  npm audit with zero vulnerabilities, the 231-method OpenAPI snapshot,
  Gitleaks with zero findings, and `git diff --check`.
- Two isolated frontend and extension builds were byte-identical. Production
  serves entry `assets/index-D6rsP8-n.js`; the disk, local, and public HTML
  SHA-256 is `531b0f5f...`, the entry SHA-256 is `b0bf7c82...`, and the extension
  ZIP SHA-256 is `e344821e...`. Both retained asset generations contain 35
  references, with 69 unique files, zero missing files, and zero orphaned
  files; all 69 assets matched disk, local HTTP, and public HTTPS byte-for-byte.
- The mode-`0700` rollback unit is
  `v1.9.1-pre-deploy-20260729-001005` outside the repository. Its final
  2,129-entry SHA-256 manifest covers the integrity-checked SQLite backup,
  three local keys, browser profiles, uploads, prior source/static/extension,
  LaunchAgent, and verified old and candidate Git bundles.
- The production checkout fast-forwarded from `6975deb` to the release commit
  through the verified local bundle. The LaunchAgent restarted as the only
  worker with PID `67698`; local and public readiness each passed all 13 samples
  across 62 seconds with migration `2026072609`, zero active runtime sessions,
  and SQLite integrity `ok`. The deploy log slice contained no application
  error, traceback, HTTP 5xx, Cookie value, authorization value, pairing Token,
  verification URL, or password value.
- The live OpenAPI document reports version `1.9.1`, 231 methods, and
  `POST /qr-login/cancel/{session_id}`. Unauthenticated local and public cancel
  probes both returned 401. The current page, both asset generations, and the
  extension ZIP were verified through the public host after restart.

The operator explicitly waived the registered ordinary-user live canary before
GitHub publication. A roughly nine-minute observation window recorded zero
browser-extension pairing or import requests and no ordinary-user account
transition to `chrome_extension`; therefore real slider/face/mobile-scan relay,
live single-use Token replay, modal-hide polling, and the ordinary-user public
server-Chrome denial remain unobserved in production. Those behaviors are
covered by the release tests, including the expected `pairing_already_used`
replay failure, but test coverage is not recorded as live-account evidence.

## v1.9.0 Production Release On 2026-07-27

The operations cockpit and dashboard business-insights work was deployed from
commit `6975deb352de5b5be060b3e3f599885fd97a79a2`. It adds hourly order-time and
buyer-behavior analysis (behavioral and quantifiable only, no customer
profiling), order status and regional distribution charts, a product
hot-sellers board with period-over-period growth detection, and inline account
identification plus a settled-date range filter on the order list. Migration
`2026072609` repairs legacy order-snapshot source markers with a defensive,
DDL-free update.

Deployment evidence:

- Production `origin/main` fast-forwarded from `2e9b950` (`v1.8.3`) to
  `6975deb` via `git merge --ff-only`; PR #41 (order-data-completeness) and
  PR #42 (dashboard-business-insights) had already merged to `origin/main`
  through green CI (`secrets` + `test`). An annotated tag `v1.9.0` was created
  on the exact production SHA `6975deb`.
- Migration `2026072609` was rehearsed on a read-only copy of the production
  database before deployment: all three defensive `UPDATE` conditions matched
  zero rows and the content fingerprint was unchanged, confirming an idempotent
  no-op. On the live restart it applied cleanly; `/health/ready` reports
  migration `2026072609` and database integrity `ok`, and the startup created
  an automatic pre-schema backup.
- The frontend was rebuilt from the production worktree at `6975deb`. Local and
  public HTML both reference entry assets `index-BoejLjfT.js` /
  `index-BZ0fOHdb.css`; the entry JS served over the public host is
  byte-identical (SHA-256 `ebb2dee5…`) to the local file, and the build carried
  zero orphaned assets across two retained generations. The production OpenAPI
  method count is 230, matching the repository snapshot.
- The four new analytics endpoints (`/analytics/traffic`, `/analytics/buyers`,
  `/analytics/orders`, `/analytics/orders/valid`) all return `401` without
  authentication, confirming the fail-closed tenant guard. The single launchd
  worker restarted with a new PID and stayed stable across a 60-second,
  10-sample readiness observation with zero anomalies; the last 200 process log
  lines contained no error or traceback entries. The service interruption was
  limited to the restart itself.
- The pre-deploy rollback unit is the mode-`0700` snapshot
  `predeploy-dashboard-insights-20260727-135601` outside the repository under
  `/Users/mac/Library/Application Support/XianyuManager Rollbacks/`. It contains
  an integrity-checked SQLite backup, all three local keys, the prior static
  assets, browser profiles, the LaunchAgent, a source archive, and a verified
  Git bundle, with a 1,969-entry SHA-256 manifest.
- A shorter 60-second observation replaced the customary 15-minute/31-sample
  window; extend the window if a longer soak is required. Real end-user order
  and per-tenant acceptance on the live host remains for the operator to
  confirm through the authenticated UI.

## v1.8.3 Production Release On 2026-07-26

The Skill Center closeout was deployed from commit
`45dd8f1e2438f039d38ebfd0c025b467cb504e31`, authored on 2026-07-26. It rebuilds
the capability area as a single-row horizontal status track, merges the
standalone AI expert entry into per-account reply-strategy settings, demotes
runtime diagnostics to a bottom section, adds `runtime_mode` and
`operation_gates` to the capability API with a collection-level
`PUT /api/ai/reply-strategies`, restricts skill runtime evidence to persisted
playwright/mtop records, fixes the architecture method count (227) and OpenAPI
snapshot, and upgrades PostCSS. No schema migration was added; the production
migration remains `2026072301`.

Ledger reconciliation:

- This release ran in production before its ledger existed: the commit was
  deployed to the production worktree ahead of any push to `origin/main`, tag,
  CHANGELOG entry, or handoff record. On 2026-07-26 the ledger was reconciled:
  `origin/main` fast-forwarded from `b310a76` to `45dd8f1`, annotated tag
  `v1.8.3` was created on the exact production SHA, and this docs-only closeout
  records the drift as it happened. The `main` push CI run `30192564473` passed
  both `secrets` and `test` jobs.
- Deploy-time evidence (a dedicated pre-deploy snapshot name, the 15-minute
  observation window, and the byte-offset log scan) was not recorded when the
  release went live and is not reconstructed here. Live verification on
  2026-07-26 confirmed the production worktree at `45dd8f1`, local
  `/health/ready` reporting `ready` with migration `2026072301`, and production
  static serving entry assets `index-26oHvBgF.js` / `index-DvxrGUwO.css`.
- The Verification Baseline compilation list below and the operator runbook
  were synchronized with `.github/workflows/ci.yml`, which had gained
  `browser_extension_pairing.py`, two skill-monitor modules, and the two QR
  utility modules since the lists were last updated.

## v1.8.2 Production Release On 2026-07-24

The dual-QR login release was merged through PR `#35` at
`a860834a8d73694b7f6c383b7e6b27f96e3c9abb`. Live QR acceptance then exposed
one raw stable account identifier in an early database persistence log; PR
`#36` moved that redaction to the log source. The final application commit is
`29f523659c091526bd393bc3b797dce7fb4570ea`. The release-evidence merge is
`39eac9fbd129b700f0673cabd63cbc2850994070`; production fast-forwarded to it
without restarting the loaded application, and annotated tag `v1.8.2` resolves
to that merge. Any later docs-only closeout commit leaves the loaded application
payload and static assets byte-equivalent to `29f5236`.

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
python -m py_compile Start.py app_factory.py application_runtime.py api_routers.py auth_email_service.py auth_registration_service.py settings_service.py db_manager.py schema_migrations.py security_utils.py session_registry.py official_login_sessions.py repositories/auth_repository.py repositories/runtime_session_repository.py services/auth_service.py ai_provider_service.py ai_reply_engine.py account_session_refresh.py order_sync_service.py item_metric_service.py item_metric_scheduler.py backfill_order_snapshots.py browser_extension_pairing.py skill_monitor_scheduler.py skill_monitor_delivery_dispatcher.py skill_monitor_retention_janitor.py reply_server.py XianyuAutoAsync.py utils/browser_interaction.py utils/xianyu_official_login.py utils/xianyu_session_probe.py utils/qr_login.py utils/qr_verification_browser.py utils/outbound_http.py utils/outbound_smtp.py utils/verification_images.py
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

## v1.10.3 Local Browser Login Deployment On 2026-08-01

The main current-device browser button now performs the bridge handshake, device
registration, session creation, and `XMC_START_LOGIN` in one flow. The extension
opens the official login URL in the user's existing browser profile, focuses and
reuses an active session tab when possible, and closes the official tab only after
the backend import and frontend confirmation succeed. Extension detection remains
diagnostic; web QR remains a separate fallback.

Temporary probe, cookie persistence, and listener handoff failures now persist as
retryable `failed` states with the original error code and login state retained.
The refresh loop converts stale retryable `action_required` rows back to `failed`
and continues on the next due probe; expiry, explicit human verification, and
identity mismatch remain manual handling states.

Local gates passed with 720 backend tests, 153 frontend tests, 11 extension
tests, type checking, Ruff, production build and build verification, extension
package verification, Python compilation, and `git diff --check`. The production
LaunchAgent is a single worker on port 8091; local and public readiness both
return HTTP 200 with migration `2026073101`. The deployed frontend entry and CSS
match local/public responses byte-for-byte, and both versioned extension ZIPs
return HTTP 200 with matching hashes.

The complete rollback unit is outside the repository at
`/Users/mac/Library/Application Support/XianyuManager Rollbacks/client-browser-login-20260801-011335`.
It contains the pre-deploy source archive, SQLite and runtime data snapshots,
browser profiles, prior static assets, patch/diff files, `verification.md`, and
`rollback.sh`. The rollback script passed syntax and `--check`; no live account
login was performed during deployment, so the remaining real-platform gate is a
manual user canary after the one-time extension installation.

After deployment, the existing HTTP/2 Cloudflare tunnel briefly returned 502/530
while its edge connections cycled. A reversible LaunchAgent `kickstart` restored
the two connections; the following low-frequency local/public readiness sample
was 10/10 HTTP 200 with `ready` and migration `2026073101`. The application
process, database, static files, and rollback unit were unchanged by this tunnel
reload.

## v1.10.4 User-Machine Native Helper Candidate On 2026-08-02

The primary “本机 Chrome 登录” path now calls a loopback native helper on the
user's own macOS or Windows computer. The helper creates and owns one official
Chrome/Edge target, watches the resulting page and platform-domain Cookies, and
submits them directly to the existing one-time P-256 device-proof protocol. The
frontend never receives Cookie, password, verification-code, or Token material.
Extension import and web QR remain independent entries.

An existing remote-debugging browser keeps its prior pages and process. Without
one, the helper starts an application-managed Profile on the user's computer and
releases that browser after confirmation or cancellation. The server verifies a
real message Token, Cookie identity, `unb`, and User-Agent before persistence. A
temporary Token or persistence failure returns to `waiting_user` and retries with
a fresh challenge; replayed, expired, transport-confused, and identity-mismatched
submissions remain rejected.

Final local gates passed with Ruff, 746 backend tests, 24 frontend files with 153
tests, 11 extension tests, TypeScript, npm audit, production build, build/static
verification, extension-package verification, and `git diff --check`. Real Chrome
smokes covered both an application-managed Profile and an existing CDP endpoint.
The standard macOS `.app` started in about 1.76 seconds, listened only on
`127.0.0.1`, returned helper version `1.0.1`, and passed deep strict bundle
verification.

GitHub CI run `30729570218` and native package run `30729574347` passed for
`012db7495c0180a312b60055a09dea388397e40c`. The Windows runner produced a real
x64 PE executable; its ZIP SHA-256 is
`8c492863e1d74c86e34f38ba4f20fe6eab60ef38136468c7e81323765bc3d50a`.
The macOS CI ZIP SHA-256 is
`bf9952e3771e946101cfb7dc40d9fd5882dd14de67002717afe78e49cf450f1c`;
the downloaded app passed strict deep verification, started from the public ZIP,
listened only on loopback, and accepted the production console Origin with PNA
headers. macOS remains ad-hoc signed, and Windows remains unsigned.

The first production rollout passed every local gate but the public tunnel
returned HTTP/2 `530/1033`, so the complete application rollback ran immediately
and restored version `1.10.3`, migration `2026073101`, SQLite integrity, static
assets, and the single worker. The public failure persisted after rollback.
Tunnel prechecks showed QUIC available while TCP 7844 was blocked; the dedicated
LaunchAgent was switched from HTTP/2 to QUIC after a temporary connector proved
two registered edge connections and public HTTP 200. Its separate rollback script
is retained with the application rollback unit.

The second rollout deployed the exact `012db74` source tree, migration
`2026080101`, the 36-asset production generation, the versioned macOS and Windows
helper ZIPs, and extension `1.2.1`. The production checkout uses local merge
commit `a8caed4` only to preserve the pre-existing production candidate history;
its source tree has zero file differences from `origin/main@012db74`. Local and
public readiness, OpenAPI `1.10.4`, HTML entry `assets/index-B3_Qlwcs.js`, all
three public package hashes, SQLite integrity, one listener, repeated public
HTTP/2 samples, and both rollback checks passed. The complete record is
`/Users/mac/Library/Application Support/XianyuManager Rollbacks/native-helper-login-20260802-105146/verification.md`.

The remaining live-provider gate is a real ordinary-user login from that user's
computer: start the downloaded helper, let it open that user's Chrome, complete
the platform verification, verify Token and `unb`, confirm the persisted account
in the frontend, and confirm only the helper-owned tab closes.
