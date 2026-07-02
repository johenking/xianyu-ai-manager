# Integration Guide

## Base URLs

- Local app: `http://127.0.0.1:8091`
- 服务地址：使用您自己的部署域名，或本地 `http://127.0.0.1:8091`

Use the local URL for account binding when possible. Cloud account binding can fail because Xianyu may reject datacenter IPs or headless browser fingerprints.

## Authentication

Login returns a bearer token:

```bash
curl -sS -X POST "$BASE_URL/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<password>"}'
```

Use the returned token on protected APIs:

```bash
curl -sS "$BASE_URL/verify" \
  -H "Authorization: Bearer $TOKEN"
```

The frontend saves the token in browser `localStorage`. Backend sessions are persisted in SQLite `auth_sessions` and expire after 30 days.

## Health

```bash
curl -sS "$BASE_URL/health"
```

Expected shape:

```json
{
  "status": "healthy",
  "services": {
    "cookie_manager": "ok",
    "database": "ok"
  }
}
```

## Skill Center APIs

All Skill Center APIs require `Authorization: Bearer $TOKEN`.

### Monitor Tasks

Create a mockable monitor task:

```bash
curl -sS -X POST "$BASE_URL/api/skills/monitor/tasks" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "iPhone local deals",
    "keyword": "iPhone",
    "min_price": 1000,
    "max_price": 5000,
    "region": "上海",
    "published_within_hours": 24,
    "ai_filter": "优先同城自提",
    "notify_enabled": false,
    "account_id": "",
    "enabled": true
  }'
```

List tasks:

```bash
curl -sS "$BASE_URL/api/skills/monitor/tasks" \
  -H "Authorization: Bearer $TOKEN"
```

Run a task:

```bash
curl -sS -X POST "$BASE_URL/api/skills/monitor/tasks/$TASK_ID/run" \
  -H "Authorization: Bearer $TOKEN"
```

List results:

```bash
curl -sS "$BASE_URL/api/skills/monitor/results?task_id=$TASK_ID" \
  -H "Authorization: Bearer $TOKEN"
```

### AI Expert Prompts

List prompts:

```bash
curl -sS "$BASE_URL/api/skills/agent/prompts" \
  -H "Authorization: Bearer $TOKEN"
```

Update one prompt:

```bash
curl -sS -X PUT "$BASE_URL/api/skills/agent/prompts/bargain" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt_type": "bargain",
    "title": "议价专家",
    "content": "在不低于底价的前提下礼貌议价。",
    "enabled": true
  }'
```

Test a reply:

```bash
curl -sS -X POST "$BASE_URL/api/skills/agent/test-reply" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "还能便宜吗？",
    "item_title": "二手显示器",
    "price": 599,
    "context": "买家第一次询价"
  }'
```

### Ops Diagnostics

```bash
curl -sS "$BASE_URL/api/skills/ops/health" \
  -H "Authorization: Bearer $TOKEN"

curl -sS "$BASE_URL/api/skills/ops/browser-status" \
  -H "Authorization: Bearer $TOKEN"

curl -sS "$BASE_URL/api/skills/ops/delivery-diagnostics" \
  -H "Authorization: Bearer $TOKEN"
```

## Xianyu Account Binding

Supported paths:

- QR login: `POST /qr-login/generate` then poll `GET /qr-login/check/{session_id}`.
- Password login: `POST /password-login` then poll `GET /password-login/check/{session_id}`.
- Cookie update: `PUT /cookies/{cid}` with a new Cookie value.

Prefer local QR/password login or manual Cookie entry. Overseas cloud QR login may return a QR code but fail during confirmation or real-cookie refresh.
