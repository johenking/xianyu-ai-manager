# v1.10.0 Security Closeout

This ledger closes the 20 reportable candidates from scan
`dedc03aa-b178-4ca5-8f08-04c4d15faabf` without launching another repository
scan. The original report directory was an operating-system temporary path and
was removed before release closeout; candidate IDs, severities and final titles
were recovered from the local Codex session audit. No account data, Cookie,
Token, database content or key material is reproduced here.

| Candidate | Severity | Conclusion | Repository evidence |
|---|---:|---|---|
| `CUR-FR001-001` | Medium | Fixed: backup-provided image references cannot seed deletion outside the managed upload root. | `db_manager.py`, `utils/image_utils.py`, `tests/test_database_hardening.py`, `tests/test_image_security.py` |
| `CUR-FR013-001` | Medium | Fixed: provider tests, AI reply tests and the training lab have bounded input, sessions, tokens, concurrency and total runtime. | `reply_server.py`, `ai_provider_service.py`, `ai_reply_engine.py`, `tests/test_ai_providers.py` |
| `CUR-FR015-001` | Medium | Fixed: Chromium search work has bounded input/pages, global and account concurrency, total timeout and deterministic close. | `search_limits.py`, `utils/item_search.py`, `skill_monitor_scheduler.py`, `tests/test_item_search_resource_limits.py` |
| `CUR-FR014-006` | Medium | Fixed: card ownership is checked before the multipart image body is read. | `reply_server.py`, `tests/test_image_security.py` |
| `CUR-FR014-002` | Low | Fixed: default-reply local images resolve only to regular files below the managed root. | `utils/image_utils.py`, `tests/test_image_security.py` |
| `CUR-FR005-001` | Medium | Fixed: backup import and user deletion await listener reconciliation and fail closed on incomplete reconciliation. | `reply_server.py`, `tests/test_cookie_manager_handoff.py`, `tests/test_security_hardening.py` |
| `CUR-FR014-005` | Medium | Fixed: generic image upload reads in bounded chunks and stops at the first over-limit byte. | `reply_server.py`, `tests/test_image_security.py` |
| `CUR-FR014-004` | Medium | Fixed: image-keyword upload uses the same pre-decode streaming limit. | `reply_server.py`, `tests/test_image_security.py` |
| `CUR-FR004-002` | Medium | Fixed: both password-change routes update the password and revoke durable sessions in one transaction, then clear memory sessions. | `db_manager.py`, `reply_server.py`, `tests/test_registration_api.py` |
| `CUR-FR012-001` | Medium | Fixed: public Geetest fields and the process-local status store are length-, TTL- and count-bounded. | `reply_server.py`, `tests/test_security_hardening.py` |
| `CUR-FR002-001` | High | Fixed: the item-only reply lookup was removed; runtime reply selection remains account scoped. | `XianyuAutoAsync.py`, `db_manager.py`, `tests/test_human_verification_policy.py` |
| `CUR-FR005-002` | Medium | Fixed: a listener that fails to stop remains tracked and the operation reports failure. | `cookie_manager.py`, `tests/test_cookie_manager_handoff.py` |
| `CUR-FR013-002` | Low | Fixed: AI account and item identifiers are logged only as one-way short references; exception bodies are not logged or returned. | `ai_reply_engine.py`, `reply_server.py`, `tests/test_ai_providers.py` |
| `CUR-FR014-003` | Low | Fixed: fulfillment image upload accepts only a managed regular file and rejects traversal or symlinks. | `XianyuAutoAsync.py`, `utils/image_uploader.py`, `tests/test_image_security.py` |
| `CUR-FR009-001` | Low | Fixed: cross-origin redirects strip credentials and redirect method/body state is not replayed. | `utils/outbound_http.py`, `tests/test_outbound_http.py`, `tests/test_outbound_integrations.py` |
| `CUR-FR014-001` | Low | Fixed: multipart image upload does not follow redirects. | `utils/image_uploader.py`, `tests/test_image_security.py` |
| `CUR-FR014-007` | Low | Fixed: image-upload responses are streamed under a 256 KiB hard limit. | `utils/image_uploader.py`, `tests/test_image_security.py` |
| `CUR-FR004-004` | Low | Fixed: missing, disabled and active accounts all perform a bcrypt verification workload before login returns. | `services/auth_service.py`, `tests/test_registration_api.py` |
| `CUR-FR004-003` | Low | Existing implementation verified: forwarded identity headers are ignored unless the direct peer is in `auth_trusted_proxies`; positive and negative `/login` tests were added. | `auth_registration_service.py`, `tests/test_registration_api.py` |
| `CUR-FR014-008` | Low | Fixed: user-backup image fields are normalized or cleared before transactional insertion. | `db_manager.py`, `tests/test_database_hardening.py`, `tests/test_image_security.py` |

Release acceptance still requires the normal Ruff, compilation, complete test,
frontend, dependency, Gitleaks, OpenAPI, migration and candidate-runtime gates.
This ledger is evidence for the known report only; it is not a claim that a new
security scan was performed.
