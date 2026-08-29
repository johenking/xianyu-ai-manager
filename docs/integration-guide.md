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
| AI providers | `GET/POST /api/ai/providers`, `PUT/DELETE /api/ai/providers/{id}`, `POST /api/ai/providers/discover-models`, `POST .../models/refresh`, `POST .../test` |
| AI training | `POST /ai-reply-lab/reply/{cookie_id}`, `POST /ai-reply-lab/save/{cookie_id}`, `/ai-training-rules/{cookie_id}*` |
| Product knowledge | `/ai-item-knowledge/{cookie_id}/{item_id}*` |
| Official account login | Web QR through `/qr-login/*`; headed Chrome QR/SMS/password through `POST /api/official-login/sessions`, `GET .../{session_id}`, owner-scoped `POST .../interact`, administrator-loopback `POST .../show-browser`, and `POST .../cancel`; compatibility `/password-login*` and `/official-window-login*` |
| Account session | `GET /api/accounts/{cookie_id}/session-status`, `POST .../session-refresh`, `POST .../session-refresh/cancel`, `POST .../session-refresh/show-browser`, `PUT /cookies/{cid}/cookie-refresh-settings` |
| Auto-reply diagnostics | `GET /api/diagnostics/auto-reply/{cookie_id}` |
| Dashboard, orders and analytics | `GET /api/dashboard/summary`, `POST /api/orders/sync`, `GET /api/orders`, `POST /api/orders/{order_id}/refresh`, `GET /analytics/items/performance`, `GET /analytics/items/traffic`, `GET /analytics/items/metrics/status`, `POST /analytics/items/metrics/sync` |
| Invitation fulfillment bridge | HMAC-only internal `POST /internal/invite/order-events`, `POST /internal/invite/send-message`, `POST /internal/invite/mark-fulfilled`, `GET /internal/invite/operations/{operation_key}` |
| Product delivery center | `/cards*`, `POST /cards/{id}/stock/import`, `POST /cards/{id}/api/validate`, `PUT /items/{cookie_id}/{item_id}/delivery-mode`, `POST /items/delivery-modes/batch`, `GET /fulfillment-records`, `POST /fulfillment-records/{id}/resend` |
| Seller auto-rating | Account-owned `PUT /cookies/{cid}/auto-rate` |

Routes below require `Authorization: Bearer $TOKEN` unless they are explicitly described as public.

## Direct Registration And Password Recovery

The public registration status is deliberately narrow and fail-closed:

```bash
curl -sS "$BASE_URL/api/auth/registration-config"
```

It returns `enabled`, `ready`, `invite_required`, `terms_version`, local terms/privacy links, a public support email, and a user-facing message. `invite_required` reflects the administrator's live setting; when it is `true`, `POST /register` must include a valid `invite_code` or it fails with `INVITE_CODE_REQUIRED`. The endpoint never returns SMTP configuration, verification fingerprints, user counts, or capacity. Treat `enabled: false` as authoritative even when the service itself is healthy.

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

时间范围支持 `today`、`yesterday`、`3days`、`7days`、`30days` 和 `custom`；自定义请求还要用 `YYYY-MM-DD` 传入 `start_date` 与 `end_date`。响应始终声明 `scope: user`，管理员也不例外，并返回 `stats`、`current`、`previous`、`item_names` 和解析后的日期边界。单日范围的 `trend_granularity` 为 `hour`，多日范围为 `day`。单日 `current.hourly_stats` 使用东八区保存的平台注册下单时间；服务端桶可能稀疏，仪表盘会补齐 24 小时。多日趋势继续使用 `current.daily_stats`。仪表盘营收使用 `paid_amount_fen`：`refunding` 保留，`refunded` 和 `cancelled` 排除，`refund_cancelled` 重新计入。前端应先渲染汇总，再请求 `/analytics/orders/valid` 获取明细行。

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

Use `POST /api/ai/providers/discover-models` to fill the model selector before saving. A new profile sends `provider_type`, `preset`, `base_url`, and `api_key`; an existing profile sends `profile_id` and may leave `api_key` empty to reuse its saved key. The response contains only normalized model IDs. Pass the selected IDs as `models` when creating or updating a profile; an empty discovery response keeps the existing cache.

Provider responses never return the cleartext key. Accounts may only apply a new provider/model after a successful generated-reply test; a failed test leaves the active configuration unchanged.

### Inbound Images

Platform image messages are accepted from both legacy payloads and the newer `operation.content` shape. The service keeps the original CDN reference out of the model request: it only accepts HTTPS image hosts on the platform allowlist, checks the response type and size limits, decodes the image, and embeds it as an OpenAI-compatible `image_url` data part. This path requires a vision-capable model behind the selected OpenAI-compatible endpoint, including a compatible relay URL. A text-only DeepSeek model or an unverified Gemini/DashScope multimodal profile keeps the existing text/fallback behavior.

## Product Knowledge

For product management screens, load a single account by default with `GET /items/cookie/{cookie_id}`. Use `GET /items` only for an explicit all-account view. Manual product sync remains account-scoped through `POST /items/get-all-from-account`; paginated sync uses `POST /items/get-by-page`. Both routes use short-lived clients with `register_instance=False`, so neither route replaces the account's long-lived WebSocket listener.

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
    "overview":"这是卖家确认的商品用途、规格、限制和交付方式。"
  }'
```

The generation call uses the submitted overview with the synchronized title, price, and detail text to build a fresh draft. A successful generation replaces the whole current draft; a failed generation leaves the previous draft intact. Save edits with `PUT .../draft`. Publishing is a separate `POST .../publish` action and fails while generated fields remain unconfirmed.

Copy the source profile to other products:

```bash
curl -sS -X POST "$BASE_URL/ai-item-knowledge/$COOKIE_ID/$ITEM_ID/copy" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"target_item_ids":["target-item-id"]}'
```

Copy writes target drafts only, always replaces the selected target draft, and keeps the target published snapshot and history unchanged. It never publishes a target. Use `GET .../versions` and `POST .../rollback/{version}` for history.

The response keeps `copied_item_ids`, `missing_item_ids`, and the legacy skip fields for client compatibility. Valid selected targets are reported as copied; missing or unowned targets remain in `missing_item_ids`.

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

Supported binding paths, in current UI order:

- Web QR (recommended): `POST /qr-login/generate`, then poll `GET /qr-login/check/{session_id}`. The first QR image is rendered locally from official `codeContent`; `mobile_scan` keeps a scannable image while slider, face, SMS, interactive, and unknown verification hand off to the server browser or extension according to the current UI surface.
- Server-side Chrome (fallback): `POST /api/official-login/sessions` with `{"mode":"qr","show_browser":true}` opens a headed Chromium window on the service host. The backend requires an authenticated console session and records unfamiliar source/Host values as warnings; the UI exposes this path only on loopback and the formal production hostname. Token validation, `unb` identity, persistence, and frontend confirmation are required before success.
- Browser extension import: register an `extension` client-browser device for the signed session flow, or create a five-minute, owner-bound, single-use protocol-v2 pairing through `/api/browser-extension/pairings` for manual import. Extension detection is isolated to this entry.
- Manual Cookie: `POST /cookies` for a new account or `PUT /cookies/{cid}` to update an existing account.

Start a server-side Chrome QR session from an authenticated console where the UI exposes that path:

```bash
curl -sS -X POST "$BASE_URL/api/official-login/sessions" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"mode":"qr","show_browser":true}'
```

Poll `GET /api/official-login/sessions/{session_id}` for this flow. Session ownership remains user-scoped, and a successful status still requires real platform Token validation and persistence; network-source warnings are observation only, not a substitute for authentication.

Start the web QR session:

```bash
curl -sS -X POST "$BASE_URL/qr-login/generate" \
  -H "Authorization: Bearer $TOKEN"
```

Poll `GET /qr-login/check/{session_id}`. Generation and scanning never start a browser. A `mobile_scan` verification remains a scannable image. Slider, face, SMS, `interactive`, and unknown verification return `continue_in_client_browser`; end the web QR session, then start either the server-browser fallback or extension path. Hide/reopen keeps polling alive. Explicit cancellation uses `POST /qr-login/cancel/{session_id}` with `ended_by`; an expired QR remains queryable for at least five minutes before becoming `not_found`.

The SMS example below starts the same authenticated server-browser flow; remote devices use the extension entry instead.

```bash
curl -sS -X POST "$BASE_URL/api/official-login/sessions" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"mode":"sms","account":"<optional-phone>","show_browser":false}'
```

The user requests and enters the SMS code on the official page through the live console frame. The application forwards the bounded input to the owning browser thread and never stores or echoes the code. Poll `GET /api/official-login/sessions/{session_id}`. The safe response contains state, a fixed message, a revisioned frame URL when interaction is required, expiry, and account metadata only. Valid states are `preparing`, `waiting_user`, `verification_required`, `persisting`, `restarting_listener`, `success`, `expired`, `failed`, `cancelled`, and `interrupted`. Submit actions through `POST .../{session_id}/interact`; show the physical window only from an administrator loopback console, or cancel through `POST .../{session_id}/cancel`.

Start an explicit password session without supplying an account ID:

```bash
curl -sS -X POST "$BASE_URL/api/official-login/sessions" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "mode":"password",
    "account":"<xianyu-account-or-phone>",
    "password":"<password>",
    "show_browser":false
  }'
```

Legacy clients may still send `account_id`, but the backend ignores it and resolves the account from the authenticated Cookie's real `unb`. Re-login updates the existing account within the same backend user, preserving its settings and related data.

Every successful browser binding stores the complete persistent Chrome profile as `browser_data/user_<unb>`; there is no parallel `storage_state.json`. On startup the login service best-effort removes only six-hour-old `.login_*`, `.window_*`, and `user_*.backup-*` directories. Canonical `user_*` profiles, unknown legacy directories, and fresh temporary directories are never cleanup targets. After an explicit password-mode login succeeds, the submitted password is encrypted with the independent account-credential key; QR and SMS sessions do not create a saved password. Status and account-detail responses never return the password or ciphertext. Do not delete the account merely to refresh authentication; deletion removes account-linked data.

The shared message-session probe retries at most once only when the first payload explicitly reports an H5 Token expiry and the same response rotates `_m_h5_tk`. It merges all response Cookies before rebuilding the timestamp and signature in the same HTTP client. Missing fresh Token, human verification, identity expiry, and ordinary transient errors do not retry. Successful response Cookies continue through the existing compare-and-swap persistence path.

`POST /cookies` accepts a compatibility `id` field but ignores it for identity. The Cookie must contain `unb` and at least one core session field; the response returns the actual `account_id` selected from the real `unb`. `PUT /cookies/{cid}` rejects a different Cookie `unb` with HTTP 409 and `account_identity_mismatch` without changing the record or its related data.

`GET /cookies/details` includes `login_method`, `login_method_label`, `auto_refresh_supported`, `reauth_required`, `reauth_action`, `last_login_at`, `last_validated_at`, and `last_expired_at`. It never exposes passwords, ciphertext, full Cookies, Tokens, or verification URLs.

Read or trigger structured refresh state:

```bash
curl -sS "$BASE_URL/api/accounts/$COOKIE_ID/session-status" \
  -H "Authorization: Bearer $TOKEN"

curl -sS -X POST "$BASE_URL/api/accounts/$COOKIE_ID/session-refresh" \
  -H "Authorization: Bearer $TOKEN"
```

The status response includes `state` and `browser_active`. `action_required` is passive and means no official browser exists; present one “start verification” action. `verification_required` exposes local show/cancel controls only when `browser_active` is `true`. `manual_reauth_required` is a stable terminal state: route the user through `reauth_action` and do not keep calling refresh.

Only `login_method='password'` with a valid login account and stored encrypted password supports automatic refresh. Manual refresh and a genuinely due scheduled refresh use the same official profile. The service first reuses `browser_data/user_<unb>`; only after that profile is completely logged out may it decrypt the saved credentials and perform official password login. Non-password sources return `manual_reauth_required` immediately and do not start Chrome. Manual refresh requires the account listener to be running. Scheduled preventive refresh is configured per account and defaults to off:

```bash
curl -sS -X PUT "$BASE_URL/cookies/$COOKIE_ID/cookie-refresh-settings" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"cookie_refresh_enabled":false,"cookie_refresh_interval_minutes":1440}'
```

When enabled, `cookie_refresh_interval_minutes` must be between 60 and 10080. Accounts without automatic-refresh capability cannot enable the schedule. Password renewal errors that require human action, including invalid or missing credentials, identity mismatch, verification/login timeout, and official-page structure mismatch, persist `manual_reauth_required`; later manual, scheduled, and runtime triggers do not reopen Chrome. `profile_in_use`, temporary browser/probe failures, and cancellation remain retryable.

A `verification_required` state means the platform requires human verification in an existing browser Worker. The backend keeps the same headed system-Chrome context open for up to 15 minutes and may return a safe screenshot path, but it never exposes the official verification URL. Use `POST .../session-refresh/show-browser` after an explicit local-user action. Success requires the expected `unb` and a real message `accessToken`; the Cookie and actual browser User-Agent must be saved and one listener generation installed before the browser closes. Cancel with `POST .../session-refresh/cancel` while `browser_active` remains true.

An active refresh is account-scoped and single-flight. Repeated `POST .../session-refresh` requests return the current persisted status and do not queue another browser session. Listener restarts restore the latest attempt or success as the scheduled-refresh anchor and set a fresh item-sync anchor, so a successful manual refresh cannot immediately trigger scheduled renewal or item-detail browser work.

The password flow follows the current official Goofish page and remains sensitive to page and risk-control changes. Web QR, server-side Chrome, the extension, and manual Cookie binding remain human recovery options. The old `/qr-login/refresh-cookies`, `/qr-login/reset-cooldown/{cookie_id}`, and `/qr-login/cooldown-status/{cookie_id}` routes have been removed and are intentionally absent from OpenAPI.

## Invitation Fulfillment Bridge

The invitation bridge is opt-in through `XIANYU_INVITE_BRIDGE_ENABLED` and uses the shared HMAC secret for internal service calls; it is separate from administrator bearer-token routes. Product scope comes only from `item_info.invite_auto_fulfillment`, and new synced products stay off until an operator enables the switch. Order entry still requires a positively classified `ordinary` order with a current direct-API `pending_ship` result; `lead` and `unknown` fail closed.

`POST /internal/invite/send-message` is idempotent by `operationKey`. Both existing and newly created conversations wait for the matching `/r/MessageSend/sendByReceiverScope` response by message `mid`: a matching platform success returns `succeeded` with `platformAcknowledged=true`, an explicit rejection returns `failed`, and a missing or unknown post-write result returns `ambiguous`. Only `succeeded` may release the invitation service to `mark-fulfilled`; `submitted`, `ambiguous`, and `needs_review` stay closed and are not blindly resent.

The maintained source is `/Users/mac/Documents/咸鱼监控台`; the installed runtime on this Mac is `/Users/mac/Library/Application Support/XianyuManager`. They are separate trees. Inspect the process listening on `8091` before a deployment, preserve runtime data, and compare the task-specific source/runtime diff instead of copying either tree wholesale. The counterpart invitation service currently runs from `/Users/mac/Projects/wo-f`.

## Recent Order Sync

Discover and reconcile the last 90 days by default:

```bash
curl -sS -X POST "$BASE_URL/api/orders/sync" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"days":90,"cookie_id":null}'
```

The response reports `discovered`, `status_updated`, `details_updated`, `unchanged`, and `failed` counts. It also returns account IDs in `requires_login`. If every selected account has an expired session, the API returns HTTP 409 and does not overwrite order data. Statuses include `unknown`, `processing`, `pending_ship`, `shipped`, `completed`, `refunding`, `refunded`, `refund_cancelled`, and `cancelled`; `order_business_type` is independently classified as `ordinary`, `lead`, or `unknown` and remains a required fulfillment gate.

## Product Delivery And Seller Auto-Rating

The current delivery center has four resource types: `text` (fixed资料), `data` (一次一密), `image`, and `api`. Create or update a resource through `/cards`; the API type requires `api_config.protocol: "fulfillment_api_v1"`, an HTTPS URL, and `POST`. Public card reads never return the API Token.

Replenish one-time secrets with TXT/line input or a CSV containing a `secret` column:

```bash
curl -sS -X POST "$BASE_URL/cards/$CARD_ID/stock/import" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"format":"csv","content":"secret,note\nCODE-001,first\nCODE-002,second\n"}'
```

Validate an API resource with a fixed HTTPS POST. Only an exact `{"status":"validated"}` response is accepted:

```bash
curl -sS -X POST "$BASE_URL/cards/$CARD_ID/api/validate" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{}'
```

Set one product's atomic delivery mode, or update up to 500 products in the batch route:

```bash
curl -sS -X PUT "$BASE_URL/items/$COOKIE_ID/$ITEM_ID/delivery-mode" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"mode":"resource","card_id":123}'
```

`mode` is exactly `off`, `resource`, or `invite`. A partial batch response reports `updated` and `failed` item IDs. Once an explicit mode is selected, a missing, disabled, empty, out-of-stock, or invalid resource fails closed; keyword rules are a compatibility fallback only for products with no explicit mode. The legacy `/items/{cookie_id}/{item_id}/delivery-binding` routes remain for older clients, but `card_id: null` now means explicit `off`, not keyword fallback. Resource and invitation delivery stay mutually exclusive.

`GET /fulfillment-records?state=succeeded` returns owner-scoped masked history. `POST /fulfillment-records/{id}/resend` is available only for a committed record after UI confirmation; it reuses the immutable committed payload, does not consume inventory or call the provider, and records the platform message ACK state.

Seller auto-rating is an account-owned, default-off switch:

```bash
curl -sS -X PUT "$BASE_URL/cookies/$COOKIE_ID/auto-rate" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"auto_rate_enabled":true}'
```

Enabling requires an owned account whose identity is ready. Only orders created after enablement and carrying the exact platform `RATE` action are scheduled, each with a 5-15 minute delay. AI generates one bounded positive sentence; a fixed positive sentence is used when AI is unavailable. The merchant write uses `tradeIdList`; only `data.module.success` plus membership in `successOrderIds` is accepted as success. Explicit rejection is `failed`; an ambiguous result is `needs_reconcile` and is not automatically retried. This contract is seller-to-buyer only. Buyer-to-seller automation remains pending a real write-and-readback canary.
