# 闲鱼登录与续期策略

> **适用前提**：当前唯一活跃生产是云端 Docker 单 worker，回环地址与正式域名都可使用服务端浏览器；回环地址弹出可见窗口，正式域名 `xianyu.cxywjx.top` 在网页内嵌云端 Chrome 画面，其他域名保留网页二维码、浏览器扩展和手动导入。

## 最终结论

网页二维码是当前 UI 推荐主路径：只请求官方二维码并在控制台渲染，零安装且不会启动浏览器。服务端官方浏览器是备选路径；回环地址设置 `show_browser=true` 并显示本机窗口，正式域名设置 `show_browser=false`，复用截图、`frame_revision` 与交互 API 在当前网页显示“云端 Chrome”。未配置 `XIANYU_BROWSER_CHANNEL` 时三条 Playwright 登录路径统一使用 bundled Chromium，配置为 `chrome`/`msedge` 等值时才沿用系统浏览器渠道。浏览器使用隔离 Profile，不复用用户日常浏览器。

服务端浏览器的安全边界是控制台登录态：未登录请求在鉴权层被 401 拒绝，任何持有效会话的请求（回环、正式域名 `xianyu.cxywjx.top`，或经隧道回流的其他 Host）都可以创建、驱动或显示服务端浏览器会话，不要求管理员身份，也不需要确认弹窗。非白名单网络来源仅记录观测日志（`_require_server_browser_access` warning），供将来多用户化时重新收紧；旧版扩展导入（protocol v1）保持"client 与 Host 均回环"的最严格边界不变。

其余通道保持可用并各有定位。`网页二维码` 只调用官方二维码接口并在控制台渲染，适合手机直接扫码；正式域名遇到交互风控时可切换到网页内嵌云端 Chrome，陌生域名仍提示在扩展浏览器中继续。`浏览器扩展导入` 使用当前浏览器 Profile，是陌生域名或指定设备登录与导入的通道，扩展检测只属于这一入口。原 `本机助手` 客户端已彻底移除：源码、安装包与发布流水线均已删除，服务端不再接受 `native_helper` 设备注册或登录会话；历史上以助手登录的账号数据（`login_method='native_helper'`）保留可读。手动 Cookie 与手动配对仍是高级人工导入方式。

无论走哪条通道，只有平台 Token 真实验证、`unb` 身份匹配、Cookie 持久化和账号列表确认全部完成后，登录才算成功；发起登录的通道只关闭自己创建的官方页面或窗口。关闭添加账号弹窗会主动结束仍在进行的登录会话，不会把它判定为成功。

账号密码成功登录后，自动续期必须由用户再次明确授权并绑定一个扩展设备；服务端 Playwright 不参与密码续期（保持关闭）。回环窗口与扩展场景的账号密码、验证码和风控输入不经过控制台；云端嵌入模式的文字和按键会作为一次性交互命令转发到当前浏览器帧，前端立即清空、服务端不记录内容，也不会自动保存。

## 登录方式

| 登录来源 | 实现 | 自动续期 | 到期后的操作 | 主要限制 |
|---|---|---:|---|---|
| `qr` | 默认使用独立网页二维码；回环可选本机窗口，正式域名可选网页内嵌云端 Chrome | 扫码成功写入持久 profile 后可免密续签 | 记忆失效时重新扫码 | 网页二维码遇交互风控转服务端浏览器或扩展；普通扫码不启动浏览器 |
| `sms_window` | 回环在本机窗口完成；正式域名在网页内嵌云端 Chrome；陌生域名使用扩展 | 否 | 重新登录 | 云端嵌入输入只做瞬时转发，不写日志或持久化 |
| `password` | 回环在本机窗口完成；正式域名在网页内嵌云端 Chrome；陌生域名使用扩展 | 否 | 重新登录 | 密码不会自动保存；云端嵌入输入只做瞬时转发 |
| `chrome_extension` | 独立扩展入口使用当前 Profile 登录或手动配对导入；远程访问的推荐通道 | 仅扩展密码登录后显式绑定 | 重新桥接或导入 | P-256 设备证明；手动配对 Token 五分钟、单次使用 |
| `native_helper` | 已移除的历史来源：助手客户端与协议入口均已删除，仅保留历史账号数据的展示 | 否 | 改用其他通道重新登录 | 服务端拒绝新的 `native_helper` 设备注册与登录会话 |
| `manual_cookie` | 用户手动粘贴 Cookie | 否 | 重新填写 | 格式容易出错，生命周期不可预测 |
| `unknown` | 迁移前保存的历史账号 | 否 | 选择一种登录方式 | 缺少可信来源，不能推断续期能力 |

原先独立的“服务器运维登录”类别已取消：服务端官方窗口不再是管理员专属维护面，而是登录备选路径，成功登录按 `qr`、`sms_window`、`password` 记录来源。

## 身份与数据规则

`cookies.xianyu_unb` 是稳定账号身份。重新登录使用 `(user_id, xianyu_unb)` 找回原记录，保留备注、规则、知识、订单和其他账号配置。不要通过删除账号恢复登录。

迁移 `2026072301` 为 `cookies` 增加：

- `login_method`: 最后一次成功登录所用的来源。
- `last_login_at`: 该来源成功写入的最新时间戳。
- `last_validated_at`: 平台会话验证成功的最新时间戳。
- `last_expired_at`: 当前登录态首次确认过期的时间；同一次过期不会反复改写。

手填新 Cookie 必须包含 `unb` 和至少一个核心会话字段。`POST /cookies` 的账号身份和返回的 `account_id` 均来自 Cookie 中的真实 `unb`；旧客户端仍可发送 `id`，但服务端忽略其身份含义。同一后台用户再次提交相同 `unb` 时归并到原账号。

更新已有账号时，Cookie 中的 `unb` 必须与记录的稳定 `xianyu_unb` 一致。不一致返回 HTTP `409` 和 `account_identity_mismatch`，且不修改 Cookie、账号身份、过期提醒或关联数据。

`GET /cookies/details` 返回登录来源、时间和能力字段，但不返回密码、密码密文、完整 Cookie、Token 或官方验证 URL。`auto_refresh_supported` 在以下任一条件成立时为真：

1. 存在未撤销的当前设备续期绑定，且已保存加密密码，登录账号非空且不是 HTTP API 地址。
2. 账号已标记 `has_l3_memory`（扫码或官方窗口登录成功后留下的持久浏览器档案）。

仅在编辑页填写账号和密码不会改变登录来源。密码路径要取得自动续期能力，仍须完整走一次账号密码官方登录、完成账号列表确认，并在五分钟内用该会话的 `login_session_id` 再次明确授权保存。扫码路径在写入 `browser_data/user_<unb>` 并完成 `/bought` 固化后即可开启定时续签。

## 服务端 Chrome 登录（备选）

控制台通过 `/api/official-login/sessions` 创建 `qr`、`sms` 或 `password` 会话。后端安全边界是有效控制台登录态；非白名单来源只记 warning，不以隧道回流 Host 值拒绝本人请求。前端仅在回环与正式域名展示该备选入口，其他域名引导扩展或网页二维码。回环发送 `show_browser=true`，会话显示接口可把窗口带到本机前台；正式域名发送 `show_browser=false`，隐藏“重新显示 Chrome 窗口”，以现有图片、状态和交互接口渲染实时云端画面。浏览器由 Playwright 启动：容器入口后台启动 `Xvfb :99` 并导出 `DISPLAY` 后 `exec python`（不使用 `xvfb-run`——其作为 PID 1 等待 X 就绪信号会死锁且不转发 SIGTERM）；非 root 继续启用 Chromium sandbox，root 容器仅对 Chromium 关闭 sandbox；未配置 `XIANYU_BROWSER_CHANNEL` 时使用 bundled Chromium。服务端调用真实平台 Token 接口，校验 `unb` 身份并完成账号落库，成功后按 `qr`、`sms_window`、`password` 记录登录来源。关闭添加账号弹窗会取消仍在进行的登录会话并停止轮询。

跨主机迁移 `browser_data/` 时不得复制 Chromium 的 `SingletonLock`、`SingletonCookie` 或 `SingletonSocket`。如历史卷已经包含这些锁，必须先停止唯一应用容器，私有归档锁文件，仅删除这三类临时锁后再启动；Cookie、Preferences、Profile 目录和账号数据不得随锁清理。`profile_in_use` 只用于 `ProcessSingleton` 或 `SingletonLock` 错误，sandbox、`DISPLAY` 和普通 `profile` 文本均归为 `browser_error`。

扩展通过 `/api/client-browser/*` 设备证明协议登录：设备以 `client_type=extension` 注册（这是唯一被接受的设备类型，`native_helper` 注册与会话一律被拒绝），创建 `qr`、`sms` 或 `password` 会话后在用户浏览器打开官方页，把 P-256 签名及结构化 Cookie 直接提交到 `/api/client-browser/import`；Cookie、Token、密码和验证码不经过前端页面。服务端调用真实平台 Token 接口，校验 `unb` 身份并完成账号落库；`/api/client-browser/sessions/{session_id}/confirm` 只有在账号列表确认后才允许对应客户端关闭自己拥有的标签页。挑战最长 60 秒、登录会话最长 5 分钟，挑战单次使用。

网页二维码使用 `/qr-login/generate`、`/qr-login/check/{session_id}` 和 `/qr-login/cancel/{session_id}`。二维码状态进入 `continue_in_client_browser` 时，控制台提示改用本机官方窗口或浏览器扩展继续；`/qr-login/continue/{session_id}` 仅保留兼容状态更新，不会因此启动服务端浏览器。取消原因限定为 `user_cancelled`、`switched_method` 或兼容值 `switched_to_extension`。

扩展流程不依赖服务器 Page 或 Profile，使用当前浏览器 Profile，只关闭自己创建的标签页，并且只有真实 Token 验证、身份匹配、Cookie 落库和前端确认全部完成后才关闭。

统一 Session Probe 在首次响应明确表示 H5 Token 过期、且响应 Cookie 提供了不同的 `_m_h5_tk` 时，会先合并全部 `Set-Cookie`（包括 `x5sec`、`cookie2` 等），再用新时间戳和新签名在同一 HTTP 客户端中重试一次。没有新 Token、人工验证、身份过期或普通临时错误不重试，也不因此启动浏览器。成功合并的 Cookie 仍由调用方通过现有 compare-and-swap 保存。

## 自动续期与人工重登

密码账号续期仍可由已绑定的当前设备扩展执行。扫码账号优先使用持久浏览器档案免密续签：打开 `browser_data/user_<unb>`，如出现 passport 弹窗则点击「快速进入」，再访问 `/bought` 取得完整会话 Cookie（必须含 `_m_h5_tk`、`unb`、`cookie2`）。服务端 Playwright 不把未验证结果当成功：免密续签以进入浏览器前的 Cookie 为基线，`cookie2` 与 `_m_h5_tk` 均未换新时判 `session_not_renewed` 并保持可重试；passport iframe 已加载但「快速进入」按钮缺失时判 `fast_entry_unavailable`，进入人工重登并发送一次性告警。

非密码且无 L3 记忆的来源调用 `POST /api/accounts/{cookie_id}/session-refresh` 时，后端直接返回 `manual_reauth_required`。密码或免密续期遇到以下终态时也进入稳定人工重登状态：

- `invalid_credentials` 或 `no_credentials`。
- 稳定身份缺失或不一致。
- 人工验证或官方登录超时。
- 官方登录页面结构失配。
- 免密续签「快速进入」按钮缺失（`fast_entry_unavailable`，需重新扫码建立记忆）。

已进入 `manual_reauth_required` 后，账号监听进入被动等待，不再建立 WebSocket、探测消息 Token 或启动浏览器；定时刷新、运行时过期处理和手动刷新也不会重复执行。`profile_in_use`、`session_not_renewed`、临时平台错误和用户取消仍保持可重试。成功完成对应登录后清除过期状态并恢复监听。

`reauth_action` 可能为 `qr_login`、`sms_login`、`password_login`、`chrome_extension_import`、`manual_cookie` 或 `choose_login`。账号页按 `account_id + last_expired_at` 记录一次性提醒，同一次过期不重复弹窗；账号卡持续显示对应入口。

QR 会话进入 `expired` 后至少保留 5 分钟。保留期内重复轮询稳定返回 `status='expired'` 和“二维码已过期，请重新扫码”，保留期结束后才返回 `not_found`，验证截图按期清理。

## 已移除接口与日志规则

以下旧接口已删除，不再出现在 OpenAPI：

- `POST /qr-login/refresh-cookies`
- `POST /qr-login/reset-cooldown/{cookie_id}`
- `GET /qr-login/cooldown-status/{cookie_id}`

登录续期优先使用持久浏览器档案免密续签（扫码成功后写入的 `browser_data/user_<unb>`）。账密滑块是次选。`XianyuOfficialLoginService` 的 headed Chrome 仍服务人工官方窗口登录；配置了 `XIANYU_CHROME_CDP_ENDPOINT` 时，可额外接管本机已开启调试端口的真实 Chrome，连不上或身份不符一律失败关闭。商品、订单等非认证用途的浏览器逻辑不受此限制。

官方浏览器、档案归档、QR 交接和二次验证失败只记录异常类型和固定摘要。API、日志和运行时会话注册表不得包含完整 Cookie、Token、二维码内容、密码、密码密文、短信验证码、交互文字或官方验证 URL。交互帧只保存在内存中，限制大小、队列深度和提交速率，并在会话结束时清空。

## 调研范围

方案收敛时对照了当前项目以及 `23Star/xianyu-super-butler`、`zhinianboke/xianyu-auto-reply`、`Usagi-org/ai-goofish-monitor`、`11273/goofish-client` 和 `Kaguya233qwq/myfish` 的公开登录路径。可稳定复用的共同模式是官方二维码内容、可见官方浏览器登录、持久浏览器档案或用户主动 Cookie 导入。项目没有采用应用内逆向短信接口，因为它与页面和风控高度耦合，也没有稳定的公开复用契约。
