# 闲鱼登录与续期策略

## 最终结论

扫码面板有两个并列的主要入口。`当前设备浏览器登录` 通过 Chrome/Edge 扩展桥接，在用户自己的浏览器中打开官方页面；QR、短信、密码、滑块、人脸和未知交互验证都在该浏览器中完成，普通用户路径在服务 Mac 上启动零个 Chrome。`网页二维码` 只调用官方二维码接口并在控制台渲染；扫码后的 `mobile_scan` 或其他交互风控会提示用户回到当前设备浏览器，不启动服务端浏览器。

只有平台 Token 真实验证、`unb` 身份匹配、Cookie 持久化和账号列表确认全部完成后，当前设备扩展才关闭官方标签页并显示成功。隐藏或关闭添加账号弹窗不会终止轮询，也不会把页面关闭当作成功。扩展缺失、来源不匹配或设备注册失败时，不创建服务端登录会话，并提供安装/刷新和网页二维码回退。

服务器 Chrome 只保留为“服务器运维登录”高级入口。它要求管理员身份、服务 Mac 回环请求和两次确认，使用隔离 Profile，不复用管理员日常 Chrome；普通用户和公网请求不能调用它。手动 Cookie 与手动配对仍是高级人工导入方式。

账号密码成功登录后，自动续期必须由用户再次明确授权并绑定一个当前设备。账号密码、验证码和风控输入不经过控制台，也不会自动保存。

## 登录方式

| 登录来源 | 实现 | 自动续期 | 到期后的操作 | 主要限制 |
|---|---|---:|---|---|
| `qr` | 当前设备扩展桥接打开官方页面；网页二维码作为独立入口 | 否 | 重新扫码或回当前设备浏览器 | 所有扫码后风控在用户浏览器完成；网页二维码本身不启动服务端 Chrome |
| `password` | 当前设备 Chrome/Edge 官方页面完成登录，成功后可单独授权续期 | 仅显式绑定设备后 | 当前设备重新登录 | 密码、短信、人脸和页面交互不进入控制台 |
| `sms_window` | 当前设备 Chrome/Edge 官方页面完成手机号验证码登录 | 否 | 当前设备重新登录 | 验证码只留在用户浏览器 |
| `chrome_extension` | 当前设备桥接或高级手动配对导入固定正式 HTTPS 控制台 | 仅显式绑定设备后 | 重新桥接或导入 | P-256 设备证明；手动配对 Token 五分钟、单次使用 |
| `server_maintenance` | 管理员回环控制台显式打开隔离服务器 Chrome | 否 | 仅管理员运维重试 | 双确认；普通用户与公网请求拒绝 |
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

1. 存在未撤销的当前设备续期绑定。
2. 已保存加密密码。
3. 登录账号非空，且不是 HTTP API 地址。

仅在编辑页填写账号和密码不会改变登录来源。要取得自动续期能力，必须完整走一次账号密码官方登录、完成账号列表确认，并在五分钟内用该会话的 `login_session_id` 再次明确授权保存。

## 官方窗口登录

普通用户使用 `/api/client-browser/devices` 注册或撤销当前浏览器设备，使用 `/api/client-browser/sessions` 创建 `qr`、`sms` 或 `password` 会话，再由扩展请求挑战、提交 P-256 签名和结构化 Cookie 到 `/api/client-browser/import`。服务端会调用真实平台 Token 接口，校验 `unb` 身份并完成账号落库；`/api/client-browser/sessions/{session_id}/confirm` 只有在账号列表确认后才允许扩展关闭标签页。挑战最长 60 秒、登录会话最长 5 分钟，挑战单次使用。

网页二维码使用 `/qr-login/generate`、`/qr-login/check/{session_id}` 和 `/qr-login/cancel/{session_id}`。二维码状态进入 `continue_in_client_browser` 时，控制台显示当前设备浏览器入口；`/qr-login/continue/{session_id}` 仅保留兼容状态更新，不再为普通用户启动服务器 Chrome。取消原因限定为 `user_cancelled`、`switched_method` 或 `switched_to_extension`。

服务器运维登录仍使用 `/api/official-login/sessions` 及其状态、交互、显示和取消接口，但每个入口都检查管理员身份与回环来源；`show_browser:true` 只在该维护面可用。旧兼容接口遇到普通用户请求时返回 `client_browser_required`，不创建协调器会话。隐藏账号弹窗只隐藏界面，当前设备和网页二维码轮询继续。

当前设备流程不依赖服务器 Page 或 Profile。扩展跟踪用户浏览器中官方标签页的跳转，只有真实 Token 验证、身份匹配、Cookie 落库和前端确认全部完成后才关闭标签页。

统一 Session Probe 在首次响应明确表示 H5 Token 过期、且响应 Cookie 提供了不同的 `_m_h5_tk` 时，会先合并全部 `Set-Cookie`（包括 `x5sec`、`cookie2` 等），再用新时间戳和新签名在同一 HTTP 客户端中重试一次。没有新 Token、人工验证、身份过期或普通临时错误不重试，也不因此启动浏览器。成功合并的 Cookie 仍由调用方通过现有 compare-and-swap 保存。

## 自动续期与人工重登

密码账号续期由已绑定的当前设备扩展执行。服务端先创建 60 秒一次性任务，用 P-256 ECDH/HKDF/AES-GCM 将凭据密封给该设备；设备首次领取后服务端立即清空密文，任务不能第二次领取。扩展只在内存中短暂解密并在用户浏览器打开官方页面；滑块、人脸或其他风控出现时保留非敏感任务元数据并暂停，等待用户完成后再验证 Cookie。

非密码来源调用 `POST /api/accounts/{cookie_id}/session-refresh` 时，后端直接返回 `manual_reauth_required`、固定安全消息和对应 `reauth_action`，不会启动 Chrome。密码续期遇到以下终态时也进入稳定人工重登状态，CTA 固定为 `password_login`：

- `invalid_credentials` 或 `no_credentials`。
- 稳定身份缺失或不一致。
- 人工验证或官方登录超时。
- 官方登录页面结构失配。

已进入 `manual_reauth_required` 后，账号监听进入被动等待，不再建立 WebSocket、探测消息 Token 或启动浏览器；定时刷新、运行时过期处理和手动刷新也不会重复执行。`profile_in_use`、临时平台错误和用户取消仍保持可重试。成功完成对应登录后清除过期状态并恢复监听。

`reauth_action` 可能为 `qr_login`、`sms_login`、`password_login`、`chrome_extension_import`、`manual_cookie` 或 `choose_login`。账号页按 `account_id + last_expired_at` 记录一次性提醒，同一次过期不重复弹窗；账号卡持续显示对应入口。

QR 会话进入 `expired` 后至少保留 5 分钟。保留期内重复轮询稳定返回 `status='expired'` 和“二维码已过期，请重新扫码”，保留期结束后才返回 `not_found`，验证截图按期清理。

## 已移除接口与日志规则

以下旧接口已删除，不再出现在 OpenAPI：

- `POST /qr-login/refresh-cookies`
- `POST /qr-login/reset-cooldown/{cookie_id}`
- `GET /qr-login/cooldown-status/{cookie_id}`

登录续期只保留当前设备扩展路径；`XianyuOfficialLoginService` 的 headed Chrome 仅供管理员回环运维入口使用。商品、订单等非认证用途的浏览器逻辑不受此限制。

官方浏览器、档案归档、QR 交接和二次验证失败只记录异常类型和固定摘要。API、日志和运行时会话注册表不得包含完整 Cookie、Token、二维码内容、密码、密码密文、短信验证码、交互文字或官方验证 URL。交互帧只保存在内存中，限制大小、队列深度和提交速率，并在会话结束时清空。

## 调研范围

方案收敛时对照了当前项目以及 `23Star/xianyu-super-butler`、`zhinianboke/xianyu-auto-reply`、`Usagi-org/ai-goofish-monitor`、`11273/goofish-client` 和 `Kaguya233qwq/myfish` 的公开登录路径。可稳定复用的共同模式是官方二维码内容、可见官方浏览器登录、持久浏览器档案或用户主动 Cookie 导入。项目没有采用应用内逆向短信接口，因为它与页面和风控高度耦合，也没有稳定的公开复用契约。
