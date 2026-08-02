# 用户本机 Chrome 登录链路重构进度

- 候选版本：服务端/前端 `1.10.4`，本机助手 `1.0.1`，迁移 `2026080101`。
- 主路径：Web 控制台通过用户电脑的 `127.0.0.1:17890` 连接本机助手；扩展导入和网页二维码保持独立入口，普通用户不启动服务端 Chrome。
- 助手：macOS/Windows 源码与原生 PyInstaller 规格已实现；P-256 私钥严格保存到 macOS Keychain 或 Windows DPAPI；本地接口绑定回环并只接受允许的控制台 Origin。
- Chrome：已有调试端口时新建并只关闭助手标签页；否则使用用户电脑上的应用管理 Profile。真实 Chrome 冒烟验证了官方页、UA、允许域 Cookie 过滤、标签页所有权和托管进程回收。
- 服务端：设备与登录会话按 `native_helper` / `extension` 隔离；验证真实消息 Token、`unb`、Cookie 和 UA 后按稳定账号身份落库；临时 Token/持久化错误保留会话并使用新挑战自动重试。
- 前端：状态机覆盖检测助手、打开 Chrome、等待用户、验证、确认账号和成功；成功确认后才调用助手关闭官方标签页；主路径不发送 `XMC_GET_DEVICE`。
- 最终本地门禁：Ruff 通过；后端 `746 tests`；前端 `24 files / 153 tests`；扩展 `11 tests`；npm audit 0 vulnerabilities；TypeScript、生产构建、静态保留、扩展包和 `git diff --check` 均通过。
- macOS 包：标准 `onedir` `.app` 启动约 1.76 秒，只监听 `127.0.0.1`；`codesign --verify --deep --strict` 通过，版本化 ZIP 已生成。当前仅 ad-hoc 签名，Developer ID 公证仍是大众分发门禁。
- Windows 包：GitHub Windows runner 已构建真实 x64 `.exe.zip`；ZIP、PE32+/COFF 和 PyInstaller 归档结构均已验证，SHA-256 为 `8c492863e1d74c86e34f38ba4f20fe6eab60ef38136468c7e81323765bc3d50a`。
- 正式部署：`1.10.4` 已部署到正式单 worker，迁移 `2026080101`、SQLite integrity `ok`；本地与公网 HTTP/2 readiness、OpenAPI、HTML 入口、静态资源、macOS/Windows 助手包和扩展包均已验证。
- 回滚：完整原子回滚单元位于 `/Users/mac/Library/Application Support/XianyuManager Rollbacks/native-helper-login-20260802-105146`；应用和隧道两份脚本的语法与 `--check` 均通过。
- 剩余门禁：普通用户电脑真实登录金丝雀、macOS Developer ID/公证和 Windows Authenticode。
