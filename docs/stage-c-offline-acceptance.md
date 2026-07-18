# Stage C Offline MTop Acceptance

## Current Gate

Stage C implements an offline-testable MTop search adapter, but it is not
connected to manual or scheduled monitor execution. It has not been deployed,
has not read or called either existing Xianyu account, and has not made a real
Xianyu/MTop, external Webhook, or AI Provider request.

The registered canary remains unverified:

- keyword: `iPhone 15 Pro`
- sort: latest (`create/desc`)
- region: unrestricted
- price: unrestricted
- pages: 1
- status: `unverified`

There is no approved dedicated test account. Shadow comparison and end-to-end
value acceptance must therefore remain blocked with
`dedicated_test_account_required`. A zero-result run cannot satisfy value
acceptance for this canary.

## Provenance And Reuse Boundary

- Primary AGPL lineage: `zhinianboke/xianyu-auto-reply`, fixed at
  `0553ea131243651631445baeba9cca403a0324d2` for the observed PC search request
  shape and latest-sort mapping. This project remains AGPL-3.0 and retains the
  upstream attribution in `NOTICE`.
- Behavioral reference: `Usagi-org/ai-goofish-monitor`, fixed at
  `f85d140b6b45029d9a0925feb96dad733b41396d`, MIT. Stage C uses its pagination
  stopping and failure-guard ideas only; no MIT expression was copied into the
  Stage C source.

The adapter does not import either project's process model, JSON-file state,
account rotation, proxy pool, worker topology, or notification architecture.

## Fail-Closed Execution Contract

Every request and retry rechecks three independent gates:

1. persisted `skill_monitor_enabled`;
2. persisted `skill_monitor_mtop_enabled`;
3. process environment `SKILL_MONITOR_MTOP_NETWORK_ALLOWED=true`.

All are off by default. Setting only one or two never permits a request. The
response is discarded before Cookie CAS or result parsing when a gate changes
during the request. The runtime monitor entrypoint continues to claim
`source_adapter=playwright`; Stage C does not select MTop.

The only allowed endpoint is:

`https://h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/`

Redirects and arbitrary URLs are rejected. Risk-control, validation, or
session-expiry responses enter `action_required`/`risk_control`; the adapter
does not bypass or rotate around them.

## Limits, Budget, And Retry

Default absolute limits are:

| Limit | Default |
| --- | ---: |
| Rows per page | 30 |
| Pages per run | 3 |
| Accepted results | 90 |
| Run time | 45 seconds |
| Request timeout | 15 seconds |
| Attempts per page | 3 |
| Response bytes | 1,000,000 |
| Per-account fixed-window budget | 6 requests / 60 seconds |
| Global fixed-window budget | 30 requests / 60 seconds |
| Consecutive-failure threshold | 3 |
| Failure cooldown | 60 minutes |
| Half-open probe lease | 60 seconds |

`skill_monitor_request_budgets` atomically consumes the account and global
slot in one SQLite transaction and stores only an owner/account digest. A
denied claim consumes neither budget. Each retry consumes a new slot.
`Retry-After` is preferred when valid; otherwise bounded exponential backoff
adds injectable jitter. The global kill switch is checked again before sleep,
after the response, and before the next attempt.

`skill_monitor_mtop_breakers` stores only the same account-scope digest, safe
error code, counters, timestamps, and a half-open probe token/lease. Three
consecutive remote/protocol failures open the circuit for 60 minutes;
risk-control, session/identity action-required, and missing-account failures
open it immediately. After cooldown, only one half-open probe may run. Success
closes the circuit; a failed probe reopens it.

Migration `2026071802` only adds the budget and breaker tables plus retention
indexes. It does not drop, rename, or rewrite existing rows. The prior code can
ignore these new tables and continue to read the expanded database.

## Identity And Response Boundary

Before every attempt the adapter reads an owner-scoped context containing
`user_id`, `account_id`, `xianyu_unb`, Cookie revision, Cookie, and browser
User-Agent. A response is accepted only while owner, account, stable identity,
and revision still match.

When MTop returns refreshed Cookie fields, the existing database service
performs compare-and-swap with all four expected identity values. A stale or
identity-changing response cannot overwrite a newer login. When no Cookie is
returned, the revision is still re-read before the result is accepted.

Responses are size-bounded and schema-validated. Only these normalized item
fields leave the adapter: item ID, title, numeric price, region, canonical
Goofish item URL, image URL, seller display name, publish time, wanted count,
and source rank. Full responses, request signatures, Cookies, raw cards, and
unknown fields are not returned or persisted. Synthetic fixtures under
`tests/fixtures/skill_monitor_mtop/` contain no captured account/provider data.
Fake-transport results always carry `is_real_data=false`; only the fixed-endpoint
network transport can mark a complete response chain as network-observed.

## Deterministic Normalization And Shadow Rules

Queries normalize Unicode and whitespace, validate price ranges, cap pages,
and map sorting as follows:

| UI sort | MTop fields | Offline normalization |
| --- | --- | --- |
| latest | `create/desc` | known publish time descending, stable source rank fallback |
| relevance | empty | source rank |
| price ascending | `price/asc` | known numeric price ascending |
| price descending | `price/desc` | known numeric price descending |

Region and price filters are also applied deterministically after parsing;
items without a required numeric price do not pass a price filter. Item ID is
the deduplication identity.

The shadow comparator receives only normalized allowlisted items and reports:

- recall against Playwright;
- Jaccard overlap;
- price mismatch ratio;
- region mismatch ratio;
- rank displacement ratio;
- explicit reasons for every failed threshold.

Default thresholds are recall `>= 0.70`, Jaccard `>= 0.50`, price mismatch
`<= 0.10`, region mismatch `<= 0.20`, and rank displacement `<= 0.35`.
Both-empty results pass only for a query explicitly declared legally empty.
An expected-non-empty canary never passes with zero results.

## AI And Webhook Offline Contracts

Monitor AI output must be exactly one JSON object with only
`recommended:boolean`, `score:integer 0..100`, and a non-empty bounded
`reason`. Markdown fences, refusal text, extra action fields, non-JSON, timeout,
provider failure, or a lost run lease all fail closed. The real provider is not
called in Stage C acceptance.

The local Webhook contract receiver binds only to `127.0.0.1`, deliberately
fails one pre-accept attempt, then verifies that retry and duplicate delivery
reuse the same `Idempotency-Key`. This is local contract evidence, not a real
HTTPS delivery receipt. Ambiguous third-party delivery remains `unknown` and
does not become exactly-once.

## Retention And Logging

- runs, results, first-seen events, AI-decision evidence, and deliveries keep
  the existing 30-day `retention_until` policy;
- request-budget rows expire one day after their fixed window closes;
- breaker rows retain only digest/counters/safe codes and expire after 30 days
  without activity;
- logs keep only safe error codes/classes and masked identifiers;
- full responses, Cookie values, signatures, AI text, and destination secrets
  are forbidden from logs and fixtures.

## Offline Verification Evidence

Fresh local evidence on 2026-07-18, before any commit or live deployment:

- `ruff check .` and the project `py_compile` list exited `0`.
- `python -m unittest discover -s tests -p 'test_skill_monitor_*.py' -v`
  passed 61 tests; the full `python -m unittest discover -s tests -v`
  passed 330 tests.
- `npm run typecheck` exited `0`; Vitest passed 17 files and 77 tests;
  `npm audit --audit-level=high` found zero vulnerabilities.
- Two consecutive `npm run build` executions each retained 31 assets;
  `npm run verify:build` reported 31 assets, zero orphans, and a
  245,200-byte entry bundle.
- `pip-audit --no-deps --disable-pip` found no known vulnerabilities in
  either `requirements.lock` or `requirements-dev.lock`.
- Gitleaks 8.30.1 scanned all 229 tracked and prospective source files from a
  symlink-preserving temporary snapshot and reported no leaks.
- A fresh SQLite database reached migration `2026071802`, with 43
  application tables, 9 migration rows, both Stage C tables and retention
  indexes, `integrity_check=ok`, and zero foreign-key violations. The exact
  pre-Stage-C source at `0272de1` opened a copy of the expanded database and
  preserved the same schema version, table row counts, integrity result, and
  foreign-key result.
- Local mocked UI evidence is stored outside the repository at
  `/Users/mac/Documents/Codex/evidence/xianyu-monitor-stage-c-ui-20260718-075104`.
  Full-scroll checks covered 1440x900 and 390x844 plus default-off, loading,
  empty, missing-evidence, and synthetic-error states. Document and main
  scroll widths matched their client widths; the MTop panel was not clipped;
  page errors and external request attempts were zero. The intentional error
  fixture emitted only its expected local HTTP 500 resource message.

These are synthetic/local contract checks. They are not Xianyu, MTop,
notification-provider, AI-provider, live deployment, shadow, or value evidence.

## Remaining Real Acceptance

Do not enable MTop, schedule, delivery, or AI until an operator supplies and
explicitly approves one dedicated account. Then, in a non-live canary
environment:

1. confirm the canary is non-empty on the official page;
2. use the same account, query, page, filter, and near-time window for
   Playwright and MTop shadow runs;
3. retain only the redacted comparator report;
4. require the configured overlap/difference thresholds across multiple
   non-empty queries, one legitimate empty query, pagination, price, time, and
   sort cases;
5. run one controlled end-to-end canary through deterministic filtering,
   deduplication, transactional persistence, and the local idempotent receiver;
6. keep every non-canary task, old empty-account task, scheduler, external
   channel, and AI Provider disabled.

Until all six pass, capability reporting must continue to say code present,
not real-search or value verified.
