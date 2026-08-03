# Native helper packaging

The source archive is reproducible on any platform. PyInstaller builds are
platform-native: run `build-macos.sh` on macOS for the `.app`, and
`build-windows.ps1` on Windows for the `.exe`. The repository workflow
`native-helper-release.yml` builds both platforms on their native runners and
publishes versioned artifacts. The application should be signed/notarized by
the release job before it is published.

The macOS spec builds a standard `onedir` `.app`. Public distribution requires
a Developer ID Application signature and Apple notarization. The Windows build
requires Authenticode signing before broad distribution. Do not label an ad-hoc
or unsigned artifact as notarized or production-signed.

The helper itself still binds only to loopback. Packaging does not change its
origin allow-list or device-proof protocol.

Opening a packaged helper without arguments performs a per-user install and
startup registration. macOS installs the app under `~/Applications` and creates
a user LaunchAgent. Windows installs a versioned executable under `%LOCALAPPDATA%`
and registers the current-user `Run` value. The service child always runs with
`--serve`; `--status` and `--uninstall` are available for lifecycle management.
