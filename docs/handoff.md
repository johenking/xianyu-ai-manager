# Handoff

## Current State On 2026-07-05

When started locally, the application serves at `http://127.0.0.1:8091`. The public source repository is `johenking/xianyu-ai-manager`. Passwords, Cookies, API keys, deployment tokens, and live database contents are intentionally absent from documentation and source control.

## Working Capabilities

- Multi-account QR and manual-Cookie binding with listener and auto-reply diagnostics; password login remains a page-sensitive compatibility path.
- Stable Xianyu identity matching through `xianyu_unb`, so same-user re-login updates the existing account record.
- Structured Cookie refresh state, immediate refresh after Token failure, and account-page secondary verification.
- Product-scoped knowledge with seller overview, AI draft generation, confirmation, publishing, versions, rollback, and draft-only copy to similar products.
- Product-scoped and global training rules, with applied/excluded/disabled reporting and one audit-driven regeneration attempt.
- Buyer-style multi-turn training dialog that does not affect production until rules are explicitly saved.
- User-scoped AI provider profiles, encrypted keys, model discovery, custom model IDs, and test-before-apply account switching.
- Typed basic, AI, and optional SMTP settings sections with secret masks and connection verification.
- Skill Center manual monitoring, expert prompts, and real operational diagnostics.
- Responsive account, product, order, card, keyword, Skill Center, and settings pages.
- Recent-order discovery and reconciliation with completed, refunding, refunded, refund-cancelled, and login-required states.

## Important Boundaries

- Training uses the current product draft; real buyer replies use only the published knowledge snapshot.
- Copying knowledge writes target drafts only, defaults to no overwrite, and never publishes automatically.
- A rule being stored does not guarantee compliance if it is disabled, scoped to another product, or contradicted by another rule. Use the lab's rule context and audit result.
- Deleting an account removes account-linked data. Re-login or update the Cookie instead of deleting when the goal is session recovery.
- Alibaba secondary verification cannot be bypassed. Cloud or overseas IPs can increase verification frequency.
- Scheduled monitoring, AI monitor filtering, and notification delivery remain explicitly unavailable.
- QR remains the recommended login path. Password login may fail after Xianyu page or risk-control changes and must not be documented as guaranteed.

## Verification Baseline

Run the following before release or handoff:

```bash
source .venv/bin/activate
pip install -r requirements-dev.lock
python -m py_compile Start.py app_factory.py application_runtime.py api_routers.py settings_service.py db_manager.py schema_migrations.py security_utils.py session_registry.py reply_server.py XianyuAutoAsync.py
python -m unittest discover -s tests -v
ruff check .

cd frontend
npm run typecheck
npm test
npm run build
npm run build
npm run verify:build
```

Also exercise one desktop and one mobile viewport for account management, AI training, product knowledge, provider selection, and settings. Record the actual pass counts at release time rather than treating an old count as permanent evidence.

Verified on 2026-07-05: Python compilation, Ruff, 68 backend unit tests, and the 197-method OpenAPI contract passed. TypeScript, 9 frontend test files with 17 tests, two consecutive Vite production builds, static-retention verification, and `npm audit` with 0 vulnerabilities passed. The entry chunk measured 215,727 bytes versus the v1.1.0 baseline of 865,910 bytes, a 75.1% reduction. Gitleaks reported no secrets.

Manual `pip-audit` still reports four advisories against `protobuf==3.10.0`, which is an exact transitive requirement of `blackboxprotobuf==1.0.1`. Do not force-upgrade protobuf without first replacing or compatibility-testing the Xianyu protocol decoder.

## Next Useful Work

- Add a rule-conflict editor that identifies contradictory facts before a reply reaches the model.
- Add integration tests for knowledge generation, copy, publish, rollback, and account session refresh routes.
- Measure rule-audit latency and provider cost so operators can decide whether production auditing should be configurable.
- Implement scheduling and notification delivery only with truthful execution state and retry semantics.
