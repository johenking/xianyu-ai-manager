# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## [Unreleased]

### Added

- Add an official Goofish browser-session service that promotes successful temporary profiles to `browser_data/user_<unb>` and reuses them for Cookie renewal.
- Add profile-first renewal with encrypted credential fallback, visible secondary-verification waiting, cancellation, and transactional profile replacement backups.
- Add a hard guard for price, plan, package, and warranty-price training rules so a final AI reply cannot keep a conflicting price after audit.
- Add copy-result metadata for product knowledge drafts, including source kind, counts, and skipped reasons.
- Add account-level scheduled Cookie refresh controls with a conservative default-off state and 1-hour to 7-day intervals.
- Add an explicit product account filter so item management shows one account's products by default and all products only after choosing all accounts.

### Changed

- Make `POST /password-login` identify accounts from the authenticated Cookie `unb`; legacy `account_id` input is accepted but ignored.
- Route manual refresh, scheduled refresh, Token-expiry recovery, and repeated connection-failure recovery through the same official browser profile.
- Improve the product-knowledge copy panel with select all, clear, overwrite explanation, sticky action, and save-before-copy behavior for dirty drafts.
- Show when the training lab used a rule guard fallback instead of returning the model's violating reply.
- Keep manual Cookie refresh independent from scheduled preventive refresh settings.

### Fixed

- Remove the duplicate temporary-browser refresh after a successful password login so CookieManager is updated once.
- Detect completed Xianyu face-verification refresh sessions using login-state and Cookie checks, and add an account-page action to recheck after the user finishes verification.

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
