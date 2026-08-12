# SAMPLE feasibility and Mac route

Date: 2026-08-10. Scope: static analysis of the one user-supplied `SAMPLE` only. The original file was not opened for execution. All extracted material stays under `/private/tmp/sample-analysis-20260810-161325`.

## Conclusions (4)

1. **Mac route: do not translate the PE wrapper.** `file` and `rz-bin` identify a 32-bit Windows GUI PE (`PE32`, `i386`) with 11 PE sections, 153 imports from five Windows DLLs, 28 resources, and a zero Security Directory. Forced-PE local control-flow analysis places `entry0` at VA `0x4acfe0`: 503 bytes, 11 basic blocks, 17 edges, and 18 call references, all inside the installer startup stub. This is not a portable business module. The practical Mac route is the repository's existing per-user native helper (`native_browser_helper/packaging/macos.spec`) and a source-level reimplementation only after a payload and its interfaces are recovered. Reproduce with `file SAMPLE.exe`, `rz-bin -F pe -ej/-Ij/-ij/-Uj SAMPLE.exe`, then `rizin -2 -q -F pe -c 's 0x4acfe0; af; afi; afbj; q' SAMPLE.exe`; outputs are in `logs/pe-summary.log` and `logs/outer-entry-control-flow-pe.log`.

2. **The payload is not currently extractable on this Mac.** The PE footer identifies Inno Setup 6.4.3 and `--data-version` prints `6.4.3 (unicode)`, but both the fixed PR build (`f1fe6f8da13e0e60aa0010afc2428c549c26aca0`) and official HEAD (`6e9e34ed0876014fdb46e684103ef8c3605e382e`) exit `2` while parsing setup headers. The overlay is a separately hashed `93,961,052` byte slice with entropy `7.99999824/8`; no payload file was written. Reproduce with `innoextract --list`, `innoextract --output-dir`, `bsdtar -tf`, and `7zz l`; literal stderr is in `logs/innoextract-upstream-list.log`, `logs/innoextract-upstream-extract.log`, `logs/bsdtar-list.log`, and `logs/sevenzip-list.log`.

3. **No reusable business IPC/API contract was observed.** The only statically visible strings and imports belong to the outer Windows installer; the high-entropy overlay yielded no reliable Electron/CEF/.NET/Python/Qt/JS/WebSocket/HTTP marker. The Delphi-style export names (`dbkFCallWrapperAddr`, `__dbk_fcall_wrapper`) are an observed naming clue, not proof of the payload's implementation language. Dynamic process, registry, port, DNS, and protocol behavior remains unknown because `TARGET_WINDOWS_RUNNER` is unset and no password guesses were made.

4. **Unique recommended optimization: helper preflight diagnostics.** Extend the already-existing loopback helper health response with a bounded, secret-free `platform`, `arch`, `protocolVersion`, `installed`, `startupRegistered`, and `running` view, then let the account-login preflight display that state before starting a browser session. This directly addresses the only evidenced cross-platform gap (a Windows-only wrapper and a Mac/Windows packaged helper with health currently limited to service/version) without inventing a sample API. Patch-ready scope: `native_browser_helper/server.py` and `native_browser_helper/installer.py` for the response; `frontend/services/api/nativeBrowser.ts` and `frontend/components/AccountList.tsx` for the preflight; `tests/test_native_browser_helper.py` and `frontend/components/AccountList.test.tsx` for Darwin/Windows, stale-version, unavailable, and malformed-response fixtures. No schema or production route changes are required. Rollback is deletion of that additive response/read path and its focused tests.

## Reproduction inputs

```text
SAMPLE=/Users/mac/Downloads/AgisoIdleClient_x64_1.2.4.exe
COPY=/private/tmp/sample-analysis-20260810-161325/original/SAMPLE.exe
SAMPLE_SHA256=52bc5a4711cb5d0ddab4e19146d123dad50a5101ccf891d7b88717699f41ef54
OVERLAY_SHA256=d29234c926455377eb646915ccf8f743903e14b7ded0c70315bee49e8733e372
```

The copy and original hashes match. The 64-byte negative fixture is `corrupt/SAMPLE-64.exe`; `rz-bin -F pe -I` returns exit `1` and `ERROR: File is not PE`.

## Tooling context

- The [innoextract project site](https://constexpr.org/innoextract/) was checked on 2026-08-10 for the documented release/support and build context.
- The [support patch for Inno Setup 6.4.2 through 6.5.4](https://github.com/dscharrer/innoextract/pull/202) was checked on 2026-08-10; the exact fork commit is recorded above.
- The [Inno Setup 6.4.3 revision notes](https://jrsoftware.org/files/is6.4-whatsnew.htm) were checked on 2026-08-10. These sources describe tooling/version context only; sample-specific conclusions come from the commands and hashes above.
