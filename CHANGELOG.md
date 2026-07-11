# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## [1.7.2] - 2026-07-11

### Added

- Add a shared `BrandLockup` for the authenticated sidebar and the public login, registration, password-recovery, terms, and privacy views.
- Add `POST /api/auth/password-reset/verify-code` to consume a password-reset email code and issue a short-lived, one-time reset grant.

### Changed

- Move the public authentication and legal views onto the main application brand system, and derive the displayed frontend version from `frontend/package.json` through a Vite compile-time define instead of a hard-coded label.
- Keep the successful email-send state without immediately refreshing CAPTCHA. An explicit resend after cooldown obtains and requires a fresh CAPTCHA.
- Split the public password-reset flow into email verification followed by password entry. The frontend keeps the returned grant only in component memory, and `POST /api/auth/password-reset` consumes it when applying the new password.
- Keep the legacy `challenge_id` plus `verification_code` reset payload temporarily compatible while clients migrate to the grant flow.
- Reuse the existing `auth_challenges` table for reset-grant digests; v1.7.2 adds no database migration and leaves `2026071104` as the latest schema migration.

### Security

- Store only a purpose-isolated digest of each reset grant on the server and make the grant email-bound, expiring, and single-use.
- Keep authentication logs free of the default administrator password, email OTPs, reset grants, full email addresses, and passwords.

## [1.7.1] - 2026-07-11

### Added

- Add user-owned item synchronization settings with global-default inheritance and typed validation from 1 minute to 24 hours and 1 to 50 pages.
- Add one user-aware dashboard summary endpoint that returns current and previous periods, owned account and inventory counters, and product names in one first-paint response.
- Add migration `2026071104` with order analysis indexes on Cookie/date and status/date.

### Changed

- Keep the full system, SMTP, registration, runtime, and global-security settings visible only to administrators while ordinary users retain personal synchronization and AI provider configuration.
- Lazy-load dashboard charts and defer order details until after summary cards render; use indexed timestamp boundaries and account-owner joins for analytics.
- Make `/system-settings`, password-login polling, QR-login polling, AI reply tests, account sessions, and account diagnostics enforce their intended administrator or owner boundaries.

### Fixed

- Stop ordinary-user dashboards from waiting forever after the administrator statistics endpoint returns HTTP 403; failed summary requests now show a retryable terminal state.
- Prevent Token or Session failures from opening Chrome for Testing when scheduled Cookie refresh is disabled; manual immediate refresh remains available.

## [1.7.0] - 2026-07-11

### Added

- Add direct registration without invite codes, guarded by the administrator switch, image CAPTCHA, purpose-bound email codes, and a configurable 1–1000 ordinary-user capacity with a default of 20.
- Add two-step SMTP verification: sending a six-digit receipt code persists an unverified configuration, and only entering the code from the real support mailbox verifies the current configuration fingerprint.
- Add a QQ Mail preset for `smtp.qq.com:465` with SSL enabled and STARTTLS disabled, plus administrator capacity and remaining-slot controls.

### Changed

- Upgrade the agreement version to `v2`, consume pending invitation-era registration challenges, and force registration closed during migration `2026071103`.
- Count disabled ordinary users toward capacity while excluding the administrator. Filling the final slot closes registration automatically; raising the limit does not reopen it.
- Keep legacy invite fields compatible but ignored. Retained invite administration routes now return HTTP 410, while historical invite rows remain untouched.
- Send authentication mail through the same path for eligible and decoy targets so the public email endpoint does not reveal whether an address is registered.

### Security

- Require the SMTP receipt code to match the current configuration fingerprint before registration can become ready, and invalidate pending confirmation challenges after SMTP changes.
- Serialize frontend SMTP verification, confirmation, settings saves, and reloads so stale requests cannot overwrite newer configuration state.
- Keep decoy registration and password-reset challenges unusable even though their public responses and delivery behavior match eligible accounts.

## [1.6.0] - 2026-07-11

### Added

- Add fail-closed invitation registration with one-time codes, image CAPTCHA challenges, purpose-bound email codes, agreement version tracking, and automatic login after a successful transaction.
- Add username-or-email login, password recovery by verified email, and revocation of every previous session after a password reset.
- Add public `/login`, `/register`, `/forgot-password`, `/terms`, and `/privacy` views with History API navigation and mobile layouts.
- Add administrator registration management for SMTP readiness, one-time invite creation and revocation, ordinary-user enablement, and the guarded registration switch.
- Add persistent authentication rate events for IP, email, and account dimensions, including trusted-proxy-aware client address resolution.

### Changed

- Make new and migrated installations keep registration disabled until the current SMTP fingerprint has delivered a verification message and at least one active invite exists.
- Send authentication mail only through the configured SMTP service; remove the legacy third-party mail fallback and retire `/send-verification-code` with HTTP 410.
- Normalize usernames with NFKC and emails case-insensitively before uniqueness checks. Registration accepts 3–24 Unicode letters, numbers, `_`, and `-` for usernames.
- Make SMTP configuration changes invalidate verification and require another real delivery before registration can reopen.

### Security

- Store invite codes, CAPTCHA answers, email codes, and network identifiers as purpose-isolated HMAC digests rather than plaintext.
- Encrypt SMTP authorization codes with an independent system-secret key and include that key in pre-migration backups.
- Reject weak passwords and passwords longer than bcrypt's 72-byte input limit, redact authentication validation inputs, and keep registration failures free of passwords, codes, invites, and full email addresses.

## [1.5.0] - 2026-07-11

### Added

- Add an official Goofish browser-session service that promotes successful temporary profiles to `browser_data/user_<unb>` and reuses them for Cookie renewal.
- Add profile-first renewal with encrypted credential fallback, visible secondary-verification waiting, cancellation, and transactional profile replacement backups.
- Add a hard guard for price, plan, package, and warranty-price training rules so a final AI reply cannot keep a conflicting price after audit.
- Add copy-result metadata for product knowledge drafts, including source kind, counts, and skipped reasons.
- Add account-level scheduled Cookie refresh controls with a conservative default-off state and 1-hour to 7-day intervals.
- Add an explicit product account filter so item management shows one account's products by default and all products only after choosing all accounts.
- Add a single-loop Skill Center scheduler with default-off task schedules, 30-second polling, and a 15-minute minimum interval.
- Add AI monitor filtering using an enabled account AI configuration.
- Add result notifications for Webhook, WeChat, DingTalk, Feishu, Bark, and Telegram channels with sent, partial, and failed outcomes.
- Add cross-run result deduplication by task and item URL, falling back to item ID.

### Changed

- Make `POST /password-login` identify accounts from the authenticated Cookie `unb`; legacy `account_id` input is accepted but ignored.
- Route manual refresh, scheduled refresh, Token-expiry recovery, and repeated connection-failure recovery through the same official browser profile.
- Improve the product-knowledge copy panel with select all, clear, overwrite explanation, sticky action, and save-before-copy behavior for dirty drafts.
- Show when the training lab used a rule guard fallback instead of returning the model's violating reply.
- Keep manual Cookie refresh independent from scheduled preventive refresh settings.
- Run manual and scheduled Skill Center tasks through the same account-scoped concurrency guard and reschedule both successful and failed scheduled runs.

### Fixed

- Remove the duplicate temporary-browser refresh after a successful password login so CookieManager is updated once.
- Detect completed Xianyu face-verification refresh sessions using login-state and Cookie checks, and add an account-page action to recheck after the user finishes verification.
- Continue notification delivery after one channel fails instead of returning after the first successful channel.

### Security

- Keep password-login task credentials out of session-status storage and do not expose official verification URLs or encrypted account passwords through APIs.

## [1.4.0] - 2026-07-05

### Added

- Add page-level React lazy loading while preserving the existing tab navigation contract.
- Split frontend API and type definitions into domain modules with compatibility re-exports.
- Add exact Python runtime and development lock files generated for Python 3.11.
- Add Ruff correctness checks, Gitleaks, and an OpenAPI path/method contract snapshot to CI.
- Add build verification for entry size, source-map policy, and retained static generations.

### Changed

- Reduce the production entry chunk from 865.91 kB to 215.73 kB (75.1%) before lazy page chunks load.
- Disable production source maps by default; enable them only with `VITE_BUILD_SOURCEMAP=true`.
- Retain only the current and previous successful static asset generations after builds.
- Keep `requirements.txt` as a compatibility entry point backed by `requirements.lock`.

### Fixed

- Initialize the SMTP notification port before selecting TLS or SSL behavior.

## [1.3.0] - 2026-07-05

### Added

- Add a unified, owner-scoped Session Registry for QR login, password login, AI training, and Cookie refresh metadata.
- Add `/health/live` and `/health/ready`, request IDs, and structured HTTP error responses.
- Persist only safe runtime status, TTL, ownership, and redacted errors in `runtime_sessions`.

### Changed

- Mark nonrecoverable browser sessions as `interrupted` after a service restart.
- Default SQL statement logging to DEBUG and reject multi-worker configuration explicitly.
- Include migration and runtime-session summaries in operational health responses.

## [1.2.0] - 2026-07-05

### Added

- Add a FastAPI application factory and lifespan-owned single-loop runtime.
- Group the full compatible API surface into domain `APIRouter` registries.
- Add authentication repository and service boundaries as the first incremental database extraction.

### Changed

- Reduce `Start.py` to a one-worker Uvicorn entrypoint and stop account listeners and browser contexts during shutdown.
- Mark retained default-reply and order-refresh compatibility aliases as deprecated in OpenAPI without removing them.
- Allow a clean source checkout to start before frontend assets have been built.

## [1.1.1] - 2026-07-05

### Added

- Add ordered, transactional SQLite migrations with database and local-key backups.
- Add an independent Fernet key for Xianyu account login credentials.

### Changed

- Hash new backend passwords with bcrypt cost 12 and upgrade legacy SHA-256 hashes after a successful login.
- Store new backend Session tokens by SHA-256 digest while retaining legacy-session compatibility.

### Security

- Remove plaintext Xianyu login passwords after encryption and never expose the ciphertext through APIs.

## [1.1.0] - 2026-07-05

### Added

- Add user-scoped AI provider profiles for DeepSeek, OpenAI, Qwen, OpenRouter, SiliconFlow, Gemini and custom compatible endpoints.
- Discover provider models with a manual model ID fallback.
- Require a successful generated test reply before applying a provider or model change to an account.
- Add seller-overview-first product knowledge generation with draft confirmation, publishing, version history and rollback.
- Copy a knowledge profile to other product drafts without automatic publishing or default overwrites.
- Report applied, excluded and disabled training rules, audit each generated reply, and regenerate once after a detected violation.
- Persist structured Cookie refresh and secondary-verification state on the account page.
- Discover and reconcile recent seller orders, including completed, refunding, refunded and refund-cancelled states.
- Persist raw platform order status, sync source, sync time, errors and unmatched status events for later reconciliation.

### Changed

- Match re-login and Cookie updates by the stable Xianyu `unb` identity so existing account-scoped data is retained.
- Replace the model `datalist` with an explicit searchable selector plus separate custom-model entry.
- Make training prefer the current product draft while production replies continue to use only published knowledge.
- Rework the AI training dialog into a fixed two-column workbench with independent scrolling and a persistent input area.
- Standardize account and rule switches, mask Cookie content by default, add avatar fallbacks, and tighten mobile keyword, Skill Center and product layouts.

### Fixed

- Stop unknown order responses from overwriting a reliable status and report expired sessions as login-required failures.
- Continue checking shipped and completed orders so signed and refunded orders can advance to their real platform state.
- Show refund success as `refunded` instead of collapsing it into a generic cancellation.

### Security

- Encrypt provider API keys at rest and return only configuration state and masks through the API.
- Keep account Cookie contents hidden in the edit dialog until the operator explicitly reveals them.

## [1.0.1] - 2026-07-03

### Fixed

- Show live `checking`, `available` and `unavailable` states on configuration capsules.
- Keep failed settings saves expanded and cover the behavior with component tests.
- Report real account-listener, AI readiness and Playwright launch status in operations diagnostics.
- Replace remaining blocking browser alerts with non-blocking status notices.

## [1.0.0] - 2026-07-03

### Added

- Product-scoped AI knowledge profiles and training rules.
- Buyer-style AI training dialog with explicit save-to-production flow.
- Atomic basic, AI and SMTP settings sections with verification states.
- Real-account and real-product expert prompt testing.
- Skill capability API and Chinese operational diagnostics.
- QR secondary-verification browser session support.

### Changed

- Reworked account cards, navigation and major pages for mobile and desktop.
- Made product facts authoritative over expert and account-level prompts.
- Replaced fake monitor AI scores and notification queue claims with truthful states.
- Masked global and account secrets in settings responses.

### Security

- Removed hard-coded Cookie samples.
- Redacted Cookie, token, verification link and prompt content from logs.
- Added secret-safe export rules and deployment guidance.
