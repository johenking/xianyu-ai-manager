# 本机浏览器助手

本助手运行在用户自己的 macOS 或 Windows 电脑上，只绑定 `127.0.0.1`。
它通过 Chrome DevTools Protocol 打开用户的 Chrome/Edge 官方页面，等待用户
完成登录，再使用一次性 P-256 设备证明调用监控台的
`/api/client-browser/*` 接口。密码、验证码、Cookie 和 Token 不进入前端页面。

## 安装包

macOS 或 Windows 版本解压后直接打开。首次打开会：

1. 复制助手到当前用户的固定目录；
2. 注册当前用户开机启动；
3. 启动回环服务并显示安装完成。

以后电脑启动后助手会自动运行，用户直接在监控台点击“本机 Chrome 登录”即可。
安装、状态与卸载命令分别是 `--install`、`--status`、`--uninstall`；后台服务使用
`--serve`。

## 源码运行

```bash
python -m native_browser_helper --serve --port 17890
```

Packaged lifecycle commands support `--install`, `--status`, and `--uninstall`.
Windowless packages can write their public lifecycle result atomically with
`--result-file PATH`; no credential or browser-session material is included.

首次运行会在 macOS Keychain 保存设备密钥；Windows 发行包使用 DPAPI 保护同一份
设备密钥。系统密钥服务异常时助手会停止初始化，不会降级写入明文。助手只接受允许的
监控台来源，默认包括正式站点和本地开发端口。

## HTTP API

- `GET /health`
- `GET /v1/device`
- `POST /v1/login/start`
- `GET /v1/login/status?session_id=...`
- `POST /v1/login/cancel`
- `POST /v1/login/close`

`close` 由控制台在服务端账号落库并完成账号确认后调用，确保官方标签页不会过早
关闭。扩展导入不依赖本助手。
