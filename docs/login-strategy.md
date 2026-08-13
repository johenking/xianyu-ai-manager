# 闲鱼登录与续期策略

> **适用前提**：本策略面向单机自用形态——生产服务作为 LaunchAgent 运行在用户本机 Mac（本地 8091），用户从同一台机器经 `127.0.0.1` 访问监控台。服务端浏览器登录通道以此为前提设计：从公网或局域网访问控制台时该通道自动关闭，只保留网页二维码、浏览器扩展和手动导入。

## 最终结论

服务端本机官方窗口是零安装主登录路径。服务直接在这台 Mac 上用 Playwright 打开闲鱼官方登录页：未配置 `XIANYU_BROWSER_CHANNEL` 时使用 Playwright 自带的 chromium（无需另装系统 Chrome，也无需安装任何插件或客户端），配置为 `chrome`/`msedge` 等值时沿用对应系统浏览器渠道；窗口使用隔离 Profile，不复用用户日常浏览器。QR、短信、密码、滑块、人脸和未知交互验证都在这个真实浏览器窗口中完成。

服务端浏览器的安全边界是控制台登录态：未登录请求在鉴权层被 401 拒绝，任何持有效会话的请求（回环、正式域名 `xianyu.cxywjx.top`，或经隧道回流的其他 Host）都可以创建、驱动或显示服务端浏览器会话，不要求管理员身份，也不需要确认弹窗。非白名单网络来源仅记录观测日志（`_require_server_browser_access` warning），供将来多用户化时重新收紧；旧版扩展导入（protocol v1）保持"client 与 Host 均回环"的最严格边界不变。

其余通道保持可用并各有定位。`网页二维码` 只调用官方二维码接口并在控制台渲染，适合远程访问和手机直接扫码；扫码后的 `mobile_scan` 或其他交互风控会提示用户在当前设备浏览器继续，不启动服务端浏览器。`浏览器扩展导入` 使用当前浏览器 Profile，是远程访问时的主要登录与导入通道，扩展检测只属于这一入口。原 `本机助手` 客户端已彻底移除：源码、安装包与发布流水线均已删除，服务端不再接受 `native_helper` 设备注册或登录会话；历史上以助手登录的账号数据（`login_method='native_helper'`）保留可读。手动 Cookie 与手动配对仍是高级人工导入方式。

无论走哪条通道，只有平台 Token 真实验证、`unb` 身份匹配、Cookie 持久化和账号列表确认全部完成后，登录才算成功；发起登录的通道只关闭自己创建的官方页面或窗口。关闭添加账号弹窗会主动结束仍在进行的登录会话，不会把它判定为成功。

账号密码成功登录后，自动续期必须由用户再次明确授权并绑定一个扩展设备；服务端 Playwright 不参与密码续期（保持关闭）。账号密码、验证码和风控输入不经过控制台，也不会自动保存。

## 登录方式

| 登录来源 | 实现 | 自动续期 | 到期后的操作 | 主要限制 |
|---|---|---:|---|---|
| `qr` | 本机监控台默认由服务端官方窗口在这台 Mac 显示二维码；网页二维码独立渲染，可远程使用 | 否 | 在官方窗口或网页二维码重新扫码 | 官方窗口内的风控直接在窗口中完成；网页二维码遇风控转当前设备浏览器，不启动服务端浏览器 |
| `sms_window` | 服务端官方窗口在本机完成手机号验证码登录（仅回环）；远程时由扩展在当前设备完成 | 否 | 重新登录 | 验证码只留在浏览器窗口，不进入控制台 |
| `password` | 服务端官方窗口在本机完成账号密码登录（仅回环）；远程时由扩展在当前设备完成 | 否 | 重新登录 | 密码、短信、人脸和页面交互不进入控制台 |
| `chrome_extension` | 独立扩展入口使用当前 Profile 登录或手动配对导入；远程访问的推荐通道 | 仅扩展密码登录后显式绑定 | 重新桥接或导入 | P-256 设备证明；手动配对 Token 五分钟、单次使用 |
| `native_helper` | 已移除的历史来源：助手客户端与协议入口均已删除，仅保留历史账号数据的展示 | 否 | 改用其他通道重新登录 | 服务端拒绝新的 `native_helper` 设备注册与登录会话 |
| `manual_cookie` | 用户手动粘贴 Cookie | 否 | 重新填写 | 格式容易出错，生命周期不可预测 |
| `unknown` | 迁移前保存的历史账号 | 否 | 选择一种登录方式 | 缺少可信来源，不能推断续期能力 |

原先独立的“服务器运维登录”类别已取消：服务端官方窗口不再是管理员专属维护面，而是主路径本身，成功登录按 `qr`、`sms_window`、`password` 记录登录来源。

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

服务端官方窗口是主路径。控制台通过 `/api/official-login/sessions` 创建 `qr`、`sms` 或 `password` 会话，每个入口只校验回环来源：服务端 Playwright 浏览器即使窗口隐藏也运行在本机，因此所有模式（不只 `show_browser:true`）都要求请求来自本机监控台。非回环请求要么收到 `client_browser_required` 回落提示，要么被直接拒绝，都不会创建协调器会话；旧兼容接口 `/official-window-login` 同样只受回环门禁约束。`show_browser:true` 与会话的显示接口把官方窗口带到本机前台。浏览器由 Playwright 启动：`XIANYU_BROWSER_CHANNEL` 未配置时使用自带 chromium 实现零安装，配置后沿用系统浏览器渠道；Profile 与用户日常浏览器隔离。服务端调用真实平台 Token 接口，校验 `unb` 身份并完成账号落库，成功后按 `qr`、`sms_window`、`password` 记录登录来源。关闭添加账号弹窗会取消仍在进行的登录会话并停止轮询。

扩展通过 `/api/client-browser/*` 设备证明协议登录：设备以 `client_type=extension` 注册（这是唯一被接受的设备类型，`native_helper` 注册与会话一律被拒绝），创建 `qr`、`sms` 或 `password` 会话后在用户浏览器打开官方页，把 P-256 签名及结构化 Cookie 直接提交到 `/api/client-browser/import`；Cookie、Token、密码和验证码不经过前端页面。服务端调用真实平台 Token 接口，校验 `unb` 身份并完成账号落库；`/api/client-browser/sessions/{session_id}/confirm` 只有在账号列表确认后才允许对应客户端关闭自己拥有的标签页。挑战最长 60 秒、登录会话最长 5 分钟，挑战单次使用。

网页二维码使用 `/qr-login/generate`、`/qr-login/check/{session_id}` 和 `/qr-login/cancel/{session_id}`。二维码状态进入 `continue_in_client_browser` 时，控制台提示改用本机官方窗口或浏览器扩展继续；`/qr-login/continue/{session_id}` 仅保留兼容状态更新，不会因此启动服务端浏览器。取消原因限定为 `user_cancelled`、`switched_method` 或兼容值 `switched_to_extension`。

扩展流程不依赖服务器 Page 或 Profile，使用当前浏览器 Profile，只关闭自己创建的标签页，并且只有真实 Token 验证、身份匹配、Cookie 落库和前端确认全部完成后才关闭。

统一 Session Probe 在首次响应明确表示 H5 Token 过期、且响应 Cookie 提供了不同的 `_m_h5_tk` 时，会先合并全部 `Set-Cookie`（包括 `x5sec`、`cookie2` 等），再用新时间戳和新签名在同一 HTTP 客户端中重试一次。没有新 Token、人工验证、身份过期或普通临时错误不重试，也不因此启动浏览器。成功合并的 Cookie 仍由调用方通过现有 compare-and-swap 保存。

## 自动续期与人工重登

密码账号续期由已绑定的当前设备扩展执行。服务端先创建 5 分钟（300 秒）一次性任务，用 P-256 ECDH/HKDF/AES-GCM 将凭据密封给该设备；设备首次领取后服务端立即清空密文，任务不能第二次领取。扩展只在内存中短暂解密并在用户浏览器打开官方页面；滑块、人脸或其他风控出现时保留非敏感任务元数据并暂停，等待用户完成后再验证 Cookie。

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

登录续期只保留扩展设备路径。`XianyuOfficialLoginService` 的 headed Chrome 只服务回环限定的官方窗口登录入口，不参与自动续期。商品、订单等非认证用途的浏览器逻辑不受此限制。

官方浏览器、档案归档、QR 交接和二次验证失败只记录异常类型和固定摘要。API、日志和运行时会话注册表不得包含完整 Cookie、Token、二维码内容、密码、密码密文、短信验证码、交互文字或官方验证 URL。交互帧只保存在内存中，限制大小、队列深度和提交速率，并在会话结束时清空。

## 调研范围

方案收敛时对照了当前项目以及 `23Star/xianyu-super-butler`、`zhinianboke/xianyu-auto-reply`、`Usagi-org/ai-goofish-monitor`、`11273/goofish-client` 和 `Kaguya233qwq/myfish` 的公开登录路径。可稳定复用的共同模式是官方二维码内容、可见官方浏览器登录、持久浏览器档案或用户主动 Cookie 导入。项目没有采用应用内逆向短信接口，因为它与页面和风控高度耦合，也没有稳定的公开复用契约。
