# Integration Guide

## Base URL And Authentication

Local base URL: `http://127.0.0.1:8091`. Use your own deployment domain outside the local workspace.

```bash
export BASE_URL=http://127.0.0.1:8091

curl -sS -X POST "$BASE_URL/login" \
  -H 'Content-Type: application/json' \
  -d '{"identifier":"admin-or-email","password":"<password>"}'
```

Pass the returned token to protected APIs:

```bash
curl -sS "$BASE_URL/verify" \
  -H "Authorization: Bearer $TOKEN"
```

Backend sessions are persisted in `auth_sessions` and expire after 30 days. Never put a real token, Cookie, password, or API key in scripts committed to the repository.

## API Map

| Capability | Routes |
|---|---|
| Health | `GET /health/live`, `GET /health/ready`, compatibility `GET /health` |
| Public registration and recovery | `GET /api/auth/registration-config`, `POST /api/auth/captcha`, `POST /api/auth/email-code`, `POST /register`, `POST /api/auth/password-reset/verify-code`, `POST /api/auth/password-reset` |
| Registration administration | `/api/admin/registration/status`, `/limit`, `/users`, `/enabled`; legacy `/invites` returns 410 |
| Settings | User-owned `GET /api/settings/user-summary`, `PUT /api/settings/user-basic`; administrator-only `/api/settings/summary`, `/sections/{section}`, `/verify/{section}`, `/verify/smtp/confirm` |
| AI providers | `GET/POST /api/ai/providers`, `PUT/DELETE /api/ai/providers/{id}`, `POST .../models/refresh`, `POST .../test` |
| AI training | `POST /ai-reply-lab/reply/{cookie_id}`, `POST /ai-reply-lab/save/{cookie_id}`, `/ai-training-rules/{cookie_id}*` |
| Product knowledge | `/ai-item-knowledge/{cookie_id}/{item_id}*` |
| Official password login | `POST /password-login`, `GET /password-login/check/{session_id}` |
| Account session | `GET /api/accounts/{cookie_id}/session-status`, `POST .../session-refresh`, `POST .../session-refresh/cancel`, `PUT /cookies/{cid}/cookie-refresh-settings` |
| Auto-reply diagnostics | `GET /api/diagnostics/auto-reply/{cookie_id}` |
| Dashboard and orders | `GET /api/dashboard/summary`, `POST /api/orders/sync`, `GET /api/orders`, `POST /api/orders/{order_id}/refresh` |
| Skill Center | `/api/skills/monitor/*`, `/api/skills/agent/*`, `/api/skills/ops/*` |

Routes below require `Authorization: Bearer $TOKEN` unless they are explicitly described as public.

## Direct Registration And Password Recovery

The public registration status is deliberately narrow and fail-closed:

```bash
curl -sS "$BASE_URL/api/auth/registration-config"
```

It returns `enabled`, `ready`, `invite_required: false`, `terms_version`, local terms/privacy links, a public support email, and a user-facing message. It never returns SMTP configuration, verification fingerprints, user counts, or capacity. Treat `enabled: false` as authoritative even when the service itself is healthy.

Request a one-time image CAPTCHA, then submit it when requesting a registration email code:

```bash
curl -sS -X POST "$BASE_URL/api/auth/captcha" \
  -H 'Content-Type: application/json' \
  -d '{}'

curl -sS -X POST "$BASE_URL/api/auth/email-code" \
  -H 'Content-Type: application/json' \
  -d '{
    "purpose":"register",
    "email":"person@example.com",
    "captcha_challenge_id":"<captcha-challenge-id>",
    "captcha_code":"<captcha-answer>"
  }'
```

The email response returns a new `challenge_id`, a 10-minute expiry, and a 60-second resend cooldown. After a successful send, the public UI keeps the completed CAPTCHA state and does not request another CAPTCHA. When the cooldown ends, an explicit resend first requests a fresh CAPTCHA and requires its answer before another email is sent. Complete registration with the email challenge:

```bash
curl -sS -X POST "$BASE_URL/register" \
  -H 'Content-Type: application/json' \
  -d '{
    "email":"person@example.com",
    "challenge_id":"<email-challenge-id>",
    "verification_code":"<six-digit-code>",
    "username":"new-user",
    "password":"<new-password>",
    "terms_version":"v2",
    "terms_accepted":true
  }'
```

Success returns the same bearer-token shape as `/login`. The switch and capacity recheck, user insert, and email-challenge consumption commit together. Legacy clients may still send `invite_code`, but it is ignored. Usernames accept 3–24 Unicode letters or numbers plus `_` and `-`. Passwords require at least eight characters, must not contain the username or match the common-password denylist, and cannot exceed bcrypt's 72-byte UTF-8 input limit.

Password recovery uses a fresh image CAPTCHA and `purpose: "password_reset"` in `/api/auth/email-code`; it does not accept a registration email challenge. The supported v1.7.2 flow verifies that code before asking for a new password:

```bash
curl -sS -X POST "$BASE_URL/api/auth/password-reset/verify-code" \
  -H 'Content-Type: application/json' \
  -d '{
    "email":"person@example.com",
    "challenge_id":"<reset-email-challenge-id>",
    "verification_code":"<six-digit-code>"
  }'
```

The response contains `reset_grant_id`, `reset_grant_token`, and `expires_in`. Treat both grant fields as secrets. The public frontend keeps them only in component memory, while the server stores only a purpose-isolated digest in the existing `auth_challenges` table. The grant is bound to the normalized email, expires after 10 minutes, and can be consumed once:

```bash
curl -sS -X POST "$BASE_URL/api/auth/password-reset" \
  -H 'Content-Type: application/json' \
  -d '{
    "email":"person@example.com",
    "reset_grant_id":"<reset-grant-id>",
    "reset_grant_token":"<reset-grant-token>",
    "new_password":"<new-password>"
  }'
```

A successful reset consumes the grant, revokes every old session, and returns the user to login. The legacy reset payload containing `challenge_id`, `verification_code`, and `new_password` remains temporarily accepted, but clients should migrate to the grant flow. `/send-verification-code` is retired and returns HTTP 410 with migration guidance.

Authentication errors use a non-echoing structure:

```json
{
  "success": false,
  "code": "REGISTRATION_CLOSED",
  "message": "注册暂未开放",
  "retry_after": null,
  "request_id": "<request-id>"
}
```

HTTP 429 responses include `retry_after`. CAPTCHA issuance is limited by client IP; email delivery is limited by normalized email and client IP; login cooldown is tracked independently by account and IP. Forwarded headers affect these limits only when the direct peer is configured in `auth_trusted_proxies`.

Do not log request bodies for these endpoints. Default administrator passwords, OTPs, reset grant IDs or tokens, full email addresses, and passwords must stay out of application logs, client diagnostics, screenshots, shell history, and support transcripts.

Administrators can read readiness and capacity, set the 1–1000 ordinary-user limit, list recent ordinary users, enable or disable an ordinary user, and change the guarded registration switch:

```bash
curl -sS -X PUT "$BASE_URL/api/admin/registration/limit" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"limit":20}'

curl -sS -X PUT "$BASE_URL/api/admin/registration/enabled" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"enabled":true}'
```

The administrator status includes `user_limit`, `user_count`, and `remaining_slots`. Disabled ordinary users still count; the administrator does not. Lowering the limit to the current count or below closes registration without deleting users, and raising it does not reopen registration. Enabling returns HTTP 409 until SMTP is currently receipt-confirmed and capacity remains. The user-management API intentionally excludes the administrator and provides no destructive delete action. Legacy invite create/list/revoke endpoints return HTTP 410.

## Settings Sections

Administrators read typed global values, secret masks, and section states:

```bash
curl -sS "$BASE_URL/api/settings/summary" \
  -H "Authorization: Bearer $TOKEN"
```

Save only one section at a time. Secret fields use `keep`, `set`, or `clear`; an empty input does not implicitly remove a stored secret.

```bash
curl -sS -X PUT "$BASE_URL/api/settings/sections/ai" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "settings":{"ai_api_url":"https://api.example.com/v1","ai_model":"model-id"},
    "secret_actions":{"ai_api_key":"keep"}
  }'
```

Use `/api/settings/verify/ai` to test AI values. `POST /api/settings/verify/smtp` saves the candidate SMTP settings as unverified, sends a six-digit code to the required support email, and returns `challenge_id`, `expires_in`, and a masked recipient. Confirm real receipt with `POST /api/settings/verify/smtp/confirm` and `{"challenge_id":"...","verification_code":"123456"}`. Only confirmation saves the verified fingerprint; changing any SMTP field consumes pending confirmations and closes registration. The QQ preset uses `smtp.qq.com:465`, SSL enabled, and STARTTLS disabled.

Ordinary users receive HTTP 403 from global `/system-settings` and administrator setting routes. They use the personal item-sync endpoints instead:

```bash
curl -sS "$BASE_URL/api/settings/user-summary" \
  -H "Authorization: Bearer $TOKEN"

curl -sS -X PUT "$BASE_URL/api/settings/user-basic" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"item_sync_enabled":true,"item_sync_interval":600,"item_sync_max_pages":5}'
```

The summary reports each value's source as `user` or `global`. The update accepts only changed fields, preserving global inheritance for omitted values. The interval accepts 60–86400 seconds and page count accepts 1–50. These settings apply only to Xianyu instances owned by the authenticated backend user. AI provider APIs remain user-scoped and are available to ordinary users.

## Dashboard Summary

```bash
curl -sS "$BASE_URL/api/dashboard/summary?range=7days" \
  -H "Authorization: Bearer $TOKEN"
```

Ranges are `today`, `yesterday`, `3days`, `7days`, `30days`, or `custom`; custom requests also send `start_date` and `end_date` as `YYYY-MM-DD`. The response declares `scope: user` for ordinary users and `scope: system` for administrators, then returns `stats`, `current`, `previous`, `item_names`, and resolved date boundaries. Frontends should render the summary first and request `/analytics/orders/valid` afterward for detail rows.

## AI Provider Profiles

Create a user-scoped OpenAI-compatible profile:

```bash
curl -sS -X POST "$BASE_URL/api/ai/providers" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name":"Example Gateway",
    "provider_type":"openai_compatible",
    "preset":"custom",
    "base_url":"https://api.example.com/v1",
    "api_key":"<api-key>",
    "default_model":"model-id",
    "is_default":false
  }'
```

Refresh a provider's model list with `POST /api/ai/providers/{id}/models/refresh`. Test a model with:

```json
{"model_name":"model-id"}
```

Provider responses never return the cleartext key. Accounts may only apply a new provider/model after a successful generated-reply test; a failed test leaves the active configuration unchanged.

## Product Knowledge

For product management screens, load a single account by default with `GET /items/cookie/{cookie_id}`. Use `GET /items` only for an explicit all-account view. Manual product sync remains account-scoped through `POST /items/get-all-from-account`.

Read the current draft, published snapshot, source product, and version state:

```bash
curl -sS "$BASE_URL/ai-item-knowledge/$COOKIE_ID/$ITEM_ID" \
  -H "Authorization: Bearer $TOKEN"
```

Generate a structured draft only after the seller provides an overview:

```bash
curl -sS -X POST "$BASE_URL/ai-item-knowledge/$COOKIE_ID/$ITEM_ID/generate" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "overview":"这是卖家确认的商品用途、规格、限制和交付方式。",
    "profile":{}
  }'
```

The generation call saves the overview first, then combines it with the synchronized title, price, and detail text. Save edits with `PUT .../draft`. Publishing is a separate `POST .../publish` action and fails while generated fields remain unconfirmed.

Copy the source profile to other products:

```bash
curl -sS -X POST "$BASE_URL/ai-item-knowledge/$COOKIE_ID/$ITEM_ID/copy" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"target_item_ids":["target-item-id"],"overwrite":false}'
```

Copy writes target drafts only, never publishes, and skips targets that already contain draft or published knowledge unless `overwrite` is explicitly true. Use `GET .../versions` and `POST .../rollback/{version}` for history.

The copy response keeps `copied_item_ids`, `skipped_item_ids`, and `missing_item_ids`, and may also include `source_kind`, `copied_count`, `skipped_count`, `missing_count`, and `skipped_reasons` so clients can explain whether the source came from the draft or published snapshot and why a target was skipped.

## Training Rules And Lab

Get all current-item rule states:

```bash
curl -sS "$BASE_URL/ai-training-rules/$COOKIE_ID?item_id=$ITEM_ID" \
  -H "Authorization: Bearer $TOKEN"
```

The response distinguishes `applied_rules`, `excluded_rules`, and `disabled_rules`. The lab accepts temporary rules without changing production:

```bash
curl -sS -X POST "$BASE_URL/ai-reply-lab/reply/$COOKIE_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id":null,
    "message":"买家问题",
    "item_id":"item-id",
    "item_title":"fallback title",
    "item_price":100,
    "item_desc":"fallback detail",
    "training_rules":[],
    "prompt_override":""
  }'
```

The result includes the reply, warnings, rule context, rule audit, regeneration state, and knowledge source. Reuse `session_id` for a multi-turn lab conversation. Save rules explicitly through `/ai-reply-lab/save/{cookie_id}` or `/ai-training-rules/{cookie_id}`.

Price, plan, package, and warranty-price rules are hard guarded. If the model still violates a price rule after one regeneration, the lab response returns a safe rule-based reply and may include `guarded_by_rule`, `guard_reason`, and `guarded_rule_ids`. If price rules conflict with each other, the guard blocks model guessing and reports the conflict for manual cleanup.

## Account Binding And Refresh

Supported binding paths:

- QR: `POST /qr-login/generate`, then poll `GET /qr-login/check/{session_id}`.
- Official password login: `POST /password-login`, then poll `GET /password-login/check/{session_id}`.
- Manual Cookie: `POST /cookies` for a new account or `PUT /cookies/{cid}` to update an existing account.

Start an official password login without supplying an account ID:

```bash
curl -sS -X POST "$BASE_URL/password-login" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "account":"<xianyu-account-or-phone>",
    "password":"<password>",
    "show_browser":true
  }'
```

The request returns a `session_id`. Poll `/password-login/check/{session_id}` until `success`, `verification_required`, `failed`, `timeout`, `cancelled`, or `interrupted`. Legacy clients may still send `account_id`, but the backend ignores it and resolves the account from the authenticated Cookie's real `unb`. Re-login updates the existing account within the same backend user, preserving its settings and related data.

On first success, the browser session is stored as `browser_data/user_<unb>`, and the password is encrypted with the independent account-credential key. Status and account-detail responses never return the password or ciphertext. Do not delete the account merely to refresh authentication; deletion removes account-linked data.

Read or trigger structured refresh state:

```bash
curl -sS "$BASE_URL/api/accounts/$COOKIE_ID/session-status" \
  -H "Authorization: Bearer $TOKEN"

curl -sS -X POST "$BASE_URL/api/accounts/$COOKIE_ID/session-refresh" \
  -H "Authorization: Bearer $TOKEN"
```

Manual refresh, scheduled refresh, and expired-session recovery use the same official profile. The service first reuses `browser_data/user_<unb>`; a valid profile can renew without another QR scan. It uses saved credentials only after the profile has fully logged out. Manual refresh requires the account listener to be running. Scheduled preventive refresh is configured per account and defaults to off:

```bash
curl -sS -X PUT "$BASE_URL/cookies/$COOKIE_ID/cookie-refresh-settings" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"cookie_refresh_enabled":false,"cookie_refresh_interval_minutes":1440}'
```

When enabled, `cookie_refresh_interval_minutes` must be between 60 and 10080. Turning scheduled refresh off does not disable manual refresh or refreshes triggered by an expired session.

A `verification_required` state means the platform requires human verification; it is not a refresh failure that can be bypassed. The backend keeps the same profile open visibly for up to 15 minutes and may return a safe screenshot path, but it never exposes the official verification URL. After verification, success is shown only when the login and security surfaces disappear and the backend detects both the expected `unb` and a valid session Cookie. Cancel with `POST .../session-refresh/cancel`.

An active refresh is account-scoped and single-flight. Repeated `POST .../session-refresh` requests return the current persisted status and do not queue another browser session. Listener restarts restore the latest attempt or success as the scheduled-refresh anchor and set a fresh item-sync anchor, so a successful manual refresh cannot immediately trigger scheduled renewal or item-detail browser work.

The password flow follows the current official Goofish page and remains sensitive to page and risk-control changes. QR and manual Cookie binding remain recovery options, but normal renewal should reuse the persisted official profile instead of repeatedly asking for QR login.

## Recent Order Sync

Discover and reconcile the last 90 days by default:

```bash
curl -sS -X POST "$BASE_URL/api/orders/sync" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"days":90,"cookie_id":null}'
```

The response reports `discovered`, `status_updated`, `details_updated`, `unchanged`, and `failed` counts. It also returns account IDs in `requires_login`. If every selected account has an expired session, the API returns HTTP 409 and does not overwrite order data. Statuses include `unknown`, `processing`, `pending_ship`, `shipped`, `completed`, `refunding`, `refunded`, `refund_cancelled`, and `cancelled`.

## Skill Center

Create and list monitor tasks at `/api/skills/monitor/tasks`, update one with `PUT /api/skills/monitor/tasks/{task_id}`, run one immediately with `POST /api/skills/monitor/tasks/{task_id}/run`, and read results from `/api/skills/monitor/results`. A scheduled task uses these additional fields:

```json
{
  "schedule_enabled": true,
  "schedule_interval_minutes": 60,
  "ai_filter": "只保留价格明显低于市场价的商品",
  "notify_enabled": true,
  "account_id": "<owned-cookie-id>"
}
```

Schedules default off and the interval must be at least 15 minutes. Task responses include `next_run_at`, `last_status`, `last_error`, and `last_run_at`. Manual and scheduled calls share one task lock; an overlapping manual run returns HTTP 409. Failed scheduled runs record the error and still compute their next attempt.

AI filtering requires an owned account with an enabled provider, API key, base URL, and model. Missing configuration fails the run explicitly. Results are deduplicated across runs by task and `item_url`, falling back to platform item ID. Existing matches are not inserted or notified again.

When notifications are enabled, the backend uses enabled Webhook, WeChat, DingTalk, Feishu, Bark, and Telegram channels. It attempts all supported channels and stores `sent`, `partial`, or `failed`; `skipped_no_channel` means no supported enabled channel was available. QQ and email channel records are not used by Skill Center monitoring.

Expert prompts live at `/api/skills/agent/prompts`; test them against a real account and product with `/api/skills/agent/test-reply`. Runtime diagnostics are available at:

```bash
curl -sS "$BASE_URL/api/skills/ops/health" -H "Authorization: Bearer $TOKEN"
curl -sS "$BASE_URL/api/skills/ops/browser-status" -H "Authorization: Bearer $TOKEN"
curl -sS "$BASE_URL/api/skills/ops/delivery-diagnostics" -H "Authorization: Bearer $TOKEN"
```

`GET /api/skills/capabilities` reports whether account AI configuration and at least one supported notification channel are currently available. Capability state reflects configuration readiness; it does not claim that a future run or external notification endpoint will succeed.
