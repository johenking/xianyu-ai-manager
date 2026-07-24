# 闲鱼登录与续期策略

## 最终结论

添加账号默认使用 API 扫码。二维码不是浏览器截图：`utils/qr_login.py` 从闲鱼官方接口取得 `codeContent`，后端在本地渲染为 PNG Data URL。只有平台要求二次安全验证、且用户主动继续时，系统才打开本机安装版 Chrome；安全验证页面可以提供脱敏截图。

只有通过账号密码官方登录，并保存了格式有效的登录账号和加密密码时，账号才具备自动续期能力。扫码、手机号验证码、本机 Chrome 扩展和手填 Cookie 到期后进入 `manual_reauth_required`，账号页显示对应的人工重登入口，不重复启动隐藏浏览器。

自动续期和手机号验证码登录使用本机安装版 Chrome，浏览器通道为 `chrome`。系统为每个闲鱼 `unb` 使用独立的 `browser_data/user_<unb>` 档案，不读取或污染用户的日常 Chrome Profile。官方页面要求短信、扫码、人脸或其他风控验证时，必须由用户在可见官方窗口中完成。

## 登录方式

| 登录来源 | 实现 | 自动续期 | 到期后的操作 | 主要限制 |
|---|---|---:|---|---|
| `qr` | 官方二维码 API 返回 `codeContent`，后端本地渲染图片 | 否 | 重新扫码 | 风控时仍需人工验证；没有可复用的密码凭据 |
| `password` | 安装版 Chrome 打开官方登录页，凭据使用独立密钥加密保存 | 是 | 自动续期失败后重新账号密码登录 | 密码错误、短信、人脸和页面变化仍需人工处理 |
| `sms_window` | 可见官方 Chrome 窗口，用户在官方页面收码并输入 | 否 | 重新验证码登录 | 应用不接收验证码；窗口最多等待 15 分钟 |
| `chrome_extension` | 用户主动从日常 Chrome Cookie Store 导入本机回环接口 | 否 | 重新导入 | 五分钟、单次使用、只能从本机发起 |
| `manual_cookie` | 用户手动粘贴 Cookie | 否 | 重新填写 | 格式容易出错，生命周期不可预测 |
| `unknown` | 迁移前保存的历史账号 | 否 | 选择一种登录方式 | 缺少可信来源，不能推断续期能力 |

## 身份与数据规则

`cookies.xianyu_unb` 是稳定账号身份。重新登录使用 `(user_id, xianyu_unb)` 找回原记录，保留备注、规则、知识、订单和其他账号配置。不要通过删除账号恢复登录。

迁移 `2026072301` 为 `cookies` 增加：

- `login_method`: 最后一次成功登录所用的来源。
- `last_login_at`: 该来源成功写入的最新时间戳。
- `last_validated_at`: 平台会话验证成功的最新时间戳。
- `last_expired_at`: 当前登录态首次确认过期的时间；同一次过期不会反复改写。

手填新 Cookie 必须包含 `unb` 和至少一个核心会话字段。`POST /cookies` 的账号身份和返回的 `account_id` 均来自 Cookie 中的真实 `unb`；旧客户端仍可发送 `id`，但服务端忽略其身份含义。同一后台用户再次提交相同 `unb` 时归并到原账号。

更新已有账号时，Cookie 中的 `unb` 必须与记录的稳定 `xianyu_unb` 一致。不一致返回 HTTP `409` 和 `account_identity_mismatch`，且不修改 Cookie、账号身份、过期提醒或关联数据。

`GET /cookies/details` 返回登录来源、时间和能力字段，但不返回密码、密码密文、完整 Cookie、Token 或官方验证 URL。`auto_refresh_supported` 必须同时满足：

1. `login_method == 'password'`。
2. 已保存加密密码。
3. 登录账号非空，且不是 HTTP API 地址。

仅在编辑页填写账号和密码不会改变登录来源。要取得自动续期能力，必须完整走一次账号密码官方登录并通过平台会话验证。

## 官方窗口登录

`POST /api/official-login/sessions` 支持 `mode='qr'`、`mode='password'` 和 `mode='sms'`。当前前端扫码入口使用 API 二维码；手机号验证码和账号密码复用此统一会话协议。短信模式固定打开可见 Chrome，系统只等待官方页面产生经过验证的 Cookie，不接收或保存短信验证码。

客户端轮询 `GET /api/official-login/sessions/{session_id}`。终态包括 `success`、`expired`、`failed`、`cancelled` 和 `interrupted`；需要时可调用 `POST .../{session_id}/show-browser`，关闭弹窗时调用 `POST .../{session_id}/cancel`。兼容端点 `/official-window-login*` 仍映射到同一个会话协调器。

登录成功后按真实 `unb` 保存账号，并把档案归档到 `browser_data/user_<unb>`。短信重登已有账号时会绑定预期 `unb`；登录到其他账号不会覆盖原记录。

## 自动续期与人工重登

密码账号续期先复用 `browser_data/user_<unb>`。只有官方档案已经完全退出时，才解密保存的凭据重新登录。浏览器始终为 headed Chrome；后台续期通过把窗口放到屏幕外实现，不使用 headless Chromium。需要人工验证时会显示同一个窗口并最多等待 15 分钟。

非密码来源调用 `POST /api/accounts/{cookie_id}/session-refresh` 时，后端直接返回 `manual_reauth_required`、固定安全消息和对应 `reauth_action`，不会启动 Chrome。密码续期遇到以下终态时也进入稳定人工重登状态，CTA 固定为 `password_login`：

- `invalid_credentials` 或 `no_credentials`。
- 稳定身份缺失或不一致。
- 人工验证或官方登录超时。
- 官方登录页面结构失配。

已进入 `manual_reauth_required` 后，账号监听进入被动等待，不再建立 WebSocket、探测消息 Token 或启动浏览器；定时刷新、运行时过期处理和手动刷新也不会重复执行。`profile_in_use`、临时浏览器错误、平台探测临时失败和用户取消仍保持可重试。成功完成对应登录后清除过期状态并恢复监听。

`reauth_action` 可能为 `qr_login`、`sms_login`、`password_login`、`chrome_extension_import`、`manual_cookie` 或 `choose_login`。账号页按 `account_id + last_expired_at` 记录一次性提醒，同一次过期不重复弹窗；账号卡持续显示对应入口。

QR 会话进入 `expired` 后至少保留 5 分钟。保留期内重复轮询稳定返回 `status='expired'` 和“二维码已过期，请重新扫码”，保留期结束后才返回 `not_found`，验证截图按期清理。

## 已移除接口与日志规则

以下旧接口已删除，不再出现在 OpenAPI：

- `POST /qr-login/refresh-cookies`
- `POST /qr-login/reset-cooldown/{cookie_id}`
- `GET /qr-login/cooldown-status/{cookie_id}`

登录续期只保留 `XianyuOfficialLoginService` 的安装版 headed Chrome 路径。商品、订单等非认证用途的浏览器逻辑不受此限制。

官方浏览器、档案归档、QR 交接和二次验证失败只记录异常类型和固定摘要。API、日志和运行时会话注册表不得包含完整 Cookie、Token、二维码内容、密码、密码密文或官方验证 URL。

## 调研范围

方案收敛时对照了当前项目以及 `23Star/xianyu-super-butler`、`zhinianboke/xianyu-auto-reply`、`Usagi-org/ai-goofish-monitor`、`11273/goofish-client` 和 `Kaguya233qwq/myfish` 的公开登录路径。可稳定复用的共同模式是官方二维码内容、可见官方浏览器登录、持久浏览器档案或用户主动 Cookie 导入。项目没有采用应用内逆向短信接口，因为它与页面和风控高度耦合，也没有稳定的公开复用契约。
