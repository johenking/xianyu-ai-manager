# Handoff

## Current State On 2026-07-10

The application serves at `http://127.0.0.1:8091`; `https://xianyu.cxywjx.top` routes through Cloudflare Tunnel to the same local port. The live runtime directory is `/Users/mac/Documents/Codex/2026-06-09/github-23star-xianyu-super-butler-https-3/work/xianyu-super-butler`. Official-login changes were developed in `/Users/mac/Documents/咸鱼监控台-official-login` on `codex/xianyu-official-login` and deployed over live commit `c94854d`. The release workspace `/Users/mac/Documents/咸鱼监控台` also contains unrelated in-progress Skill Center changes; do not reset or deploy those accidentally. Passwords, Cookies, API keys, deployment tokens, and live database contents remain absent from documentation and source control.

## Working Capabilities

- Multi-account official password, QR, and manual-Cookie binding with listener and auto-reply diagnostics.
- Stable Xianyu identity matching through `xianyu_unb`, so same-user re-login updates the existing account record.
- Persistent official browser profiles under `browser_data/user_<unb>`, with profile-first renewal and encrypted credential fallback after complete logout.
- One official refresh path for manual refresh, scheduled refresh, Token expiry, and repeated connection failures, with account-page secondary verification and cancellation.
- Product management defaults to one selected account and shows all-account products only after the operator chooses “全部账号”.
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
- Scheduled preventive Cookie refresh defaults to off; manual refresh and expired-session recovery remain separate paths.
- Alibaba secondary verification cannot be bypassed. Cloud or overseas IPs can increase verification frequency.
- Goofish currently rejects headless Chromium. Background renewal must use the headed off-screen profile flow in `utils/xianyu_official_login.py`.
- A profile can renew without another scan while its official session remains valid. Automatic credential fallback is not proven for an account until that account completes one new official password login and stores encrypted credentials.
- Scheduled monitoring, AI monitor filtering, and notification delivery remain explicitly unavailable.
- Password login follows the current official page and may require maintenance after page or risk-control changes. Verification must remain human-assisted rather than bypassed.

## Verification Baseline

Run the following before release or handoff:

```bash
source .venv/bin/activate
pip install -r requirements-dev.lock
python -m py_compile Start.py app_factory.py application_runtime.py api_routers.py settings_service.py db_manager.py schema_migrations.py security_utils.py session_registry.py reply_server.py XianyuAutoAsync.py utils/xianyu_official_login.py
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

Verified on 2026-07-10 for the official-login rollout: Ruff, Python compilation, 94 backend tests, TypeScript, 12 frontend test files with 24 tests, two Vite production builds, static-retention verification, and `git diff --check` passed. The entry chunk measured 215,681 bytes versus the 865,910-byte baseline, a 75.1% reduction. The isolated branch contains implementation commit `8911b49` and deterministic polling-test commit `43ab8ce`.

Deployed on 2026-07-10: local and public `/health` returned healthy, public HTML referenced `/static/assets/index-zOeN-w-D.js`, and the public `AccountList-Cx-k3rn-.js` hash matched the deployed file. Startup logs reported one account listener, a successful WebSocket connection, and running heartbeat, Token refresh, Cookie refresh, and product-sync tasks. Rollback material is under live `data/backups/official-login-20260710-154915/` and includes the SQLite database, previous authentication code, previous static assets, and all four browser profiles.

The live account had no saved username or encrypted password at rollout time. A real official password login and any platform-required verification still need operator input before credential fallback, manual renewal, and post-restart profile reuse can be claimed as live-accepted.

Manual `pip-audit` still reports four advisories against `protobuf==3.10.0`, which is an exact transitive requirement of `blackboxprotobuf==1.0.1`. Do not force-upgrade protobuf without first replacing or compatibility-testing the Xianyu protocol decoder.

## Next Useful Work

- Complete one live official password login, then verify the canonical `user_<unb>` profile, manual renewal, service restart, profile reuse, and listener recovery.
- Add a rule-conflict editor that identifies contradictory facts before a reply reaches the model.
- Add integration tests for knowledge generation, copy, publish, rollback, and account session refresh routes.
- Measure rule-audit latency and provider cost so operators can decide whether production auditing should be configurable.
- Implement scheduling and notification delivery only with truthful execution state and retry semantics.
