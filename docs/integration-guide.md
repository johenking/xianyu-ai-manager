# Integration Guide

## Base URL And Authentication

Local base URL: `http://127.0.0.1:8091`. Use your own deployment domain outside the local workspace.

```bash
export BASE_URL=http://127.0.0.1:8091

curl -sS -X POST "$BASE_URL/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<password>"}'
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
| Health | `GET /health` |
| Settings | `GET /api/settings/summary`, `PUT /api/settings/sections/{section}`, `POST /api/settings/verify/{section}` |
| AI providers | `GET/POST /api/ai/providers`, `PUT/DELETE /api/ai/providers/{id}`, `POST .../models/refresh`, `POST .../test` |
| AI training | `POST /ai-reply-lab/reply/{cookie_id}`, `POST /ai-reply-lab/save/{cookie_id}`, `/ai-training-rules/{cookie_id}*` |
| Product knowledge | `/ai-item-knowledge/{cookie_id}/{item_id}*` |
| Official password login | `POST /password-login`, `GET /password-login/check/{session_id}` |
| Account session | `GET /api/accounts/{cookie_id}/session-status`, `POST .../session-refresh`, `POST .../session-refresh/cancel`, `PUT /cookies/{cid}/cookie-refresh-settings` |
| Auto-reply diagnostics | `GET /api/diagnostics/auto-reply/{cookie_id}` |
| Orders | `POST /api/orders/sync`, `GET /api/orders`, `POST /api/orders/{order_id}/refresh` |
| Skill Center | `/api/skills/monitor/*`, `/api/skills/agent/*`, `/api/skills/ops/*` |

All routes below require `Authorization: Bearer $TOKEN` unless stated otherwise.

## Settings Sections

Read typed values, secret masks, and section states:

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

Use `/api/settings/verify/ai` or `/api/settings/verify/smtp` to test unsaved values. SMTP verification connects and authenticates but does not send mail.

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

Manual monitor tasks require a real account. Create/list tasks at `/api/skills/monitor/tasks`, run one with `POST /api/skills/monitor/tasks/{task_id}/run`, and read results from `/api/skills/monitor/results`.

Expert prompts live at `/api/skills/agent/prompts`; test them against a real account and product with `/api/skills/agent/test-reply`. Runtime diagnostics are available at:

```bash
curl -sS "$BASE_URL/api/skills/ops/health" -H "Authorization: Bearer $TOKEN"
curl -sS "$BASE_URL/api/skills/ops/browser-status" -H "Authorization: Bearer $TOKEN"
curl -sS "$BASE_URL/api/skills/ops/delivery-diagnostics" -H "Authorization: Bearer $TOKEN"
```

Scheduled monitoring, AI monitor filtering, and notification delivery are not implemented. The API reports them as unavailable instead of simulating a queue or delivery.
