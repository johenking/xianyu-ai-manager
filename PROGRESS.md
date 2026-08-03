# 用户本机 Chrome 登录链路重构进度

- 正式版本：服务端/前端 `1.10.4`，本机助手 `1.0.2`，迁移 `2026080101`。
- 主路径：Web 控制台通过用户电脑的 `127.0.0.1:17890` 连接本机助手；扩展导入和网页二维码保持独立入口，普通用户不启动服务端 Chrome。
- 助手：macOS/Windows 原生包首次运行后安装到当前用户并注册开机启动；后续点击登录无需扩展或手动重启助手。P-256 私钥严格保存到 macOS Keychain 或 Windows DPAPI；本地接口绑定回环并只接受允许的控制台 Origin。
- Chrome：已有调试端口时新建并只关闭助手标签页；否则使用用户电脑上的应用管理 Profile。真实 Chrome 冒烟验证了官方页、UA、允许域 Cookie 过滤、标签页所有权和托管进程回收。
- 服务端：设备与登录会话按 `native_helper` / `extension` 隔离；验证真实消息 Token、`unb`、Cookie 和 UA 后按稳定账号身份落库；临时 Token/持久化错误保留会话并使用新挑战自动重试。
- 前端：状态机覆盖检测助手、打开 Chrome、等待用户、验证、确认账号和成功；成功确认后才调用助手关闭官方标签页；主路径不发送 `XMC_GET_DEVICE`。
- 最终门禁：Ruff 通过；后端 `757 tests`；助手/包 `30 tests`；前端 `24 files / 153 tests`；扩展 `11 tests`；npm audit 0 vulnerabilities；TypeScript、生产构建、静态保留、扩展包、Actionlint、Gitleaks 和 `git diff --check` 均通过。
- macOS `1.0.2` 包：arm64 Mach-O、双版本字段、安装/单实例/重启恢复/卸载和 `codesign --verify --deep --strict` 均通过；SHA-256 为 `c4e9b9be03816738859933ff68ae1f68e92c2b71838b065e7fcc69e55919e305`。当前仅 ad-hoc 签名，Developer ID 公证仍是大众分发门禁。
- Windows `1.0.2` 包：原生 runner 上完成安装、启动项、健康、单实例、停止/重启、状态和卸载；PE32+ x64 校验通过，SHA-256 为 `95a548fd739a37d015dea77a00910930d89f15c17952bb092eac8a7b5438e67e`。
- 浏览器回环：使用正式 macOS `1.0.2` 包临时启动助手后，真实 Chrome 从正式页面 Origin 成功读取 `127.0.0.1:17890/health`；PNA 预检返回 `204`，非允许 Origin 返回 `403`。测试后的监听、隔离状态和钥匙串记录均已清除。这是浏览器传输证据，不是真实账号金丝雀。
- 正式部署：生产功能和证据提交均已同步，生产源码树与当前 `origin/main` 一致。单 worker、迁移 `2026080101`、SQLite `ok`，本地/公网连续 `5/5` readiness、HTML/JS、OpenAPI 和三个公开下载哈希均通过；最新复核仍为 PID `82448`、单监听、本地/公网 `200/ready`。精确提交链记录在 `docs/handoff.md` 和完整回滚单元。
- 回滚：完整原子回滚单元位于 `/Users/mac/Library/Application Support/XianyuManager Rollbacks/native-helper-persistence-20260803-082948`；包含三份停服数据库证据，最终使用 `xianyu_data.final-stopped.db`，脚本语法与 `--check` 已通过。两次错误验收断言均真实触发并验证了完整回滚，第三次部署成功。
- 剩余门禁：普通用户电脑真实登录金丝雀、macOS Developer ID/公证和 Windows Authenticode。
