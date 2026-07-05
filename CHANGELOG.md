# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## [Unreleased]

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
