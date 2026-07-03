# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## [Unreleased]

### Added

- Add user-scoped AI provider profiles for DeepSeek, OpenAI, Qwen, OpenRouter, SiliconFlow, Gemini and custom compatible endpoints.
- Discover provider models with a manual model ID fallback.
- Require a successful generated test reply before applying a provider or model change to an account.

### Security

- Encrypt provider API keys at rest and return only configuration state and masks through the API.

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
