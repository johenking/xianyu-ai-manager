# Xianyu AI Manager

闲鱼多账号商品管理、自动回复、自动发货、卖家自动好评与商品级 AI 知识管理平台。

[![CI](https://github.com/johenking/xianyu-ai-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/johenking/xianyu-ai-manager/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)

> 本项目会操作真实闲鱼账号、Cookie 与订单数据。请仅在您有权管理的账号上使用，并遵守平台规则。自动化登录与浏览器操作可能触发平台风控，项目不承诺绕过验证。

## 功能

- 多账号管理：网页二维码推荐登录、服务端本机 Chrome 备选、浏览器扩展导入和手动 Cookie 入口，按真实 `unb` 保留账号数据；自动续期只在扩展密码登录后由用户明确授权并绑定当前设备时启用。
- 商品管理：按账号筛选和同步真实商品，只有选择“全部账号”时才展示全量商品；每件商品可维护独立知识档案和训练规则，相同商品可复刻知识草稿。
- AI 客服：商品事实优先，按议价、技术、默认三类专家策略回复；不同账号可选择不同平台和模型。
- AI 训练：在独立对话框中模拟买家咨询，显示实际加载、排除和停用的规则；修正确认后才写入线上配置。
- 关键词回复：账号级关键词回复、默认回复与关键词发货规则。
- 自动发货工作台：商品配置、资源库、发货记录三页；资源支持固定资料、一次一密、图片和幂等 API v1，商品以 `off`/`resource`/`invite` 原子互斥选择，显式资源失效时失败关闭。一次一密支持 TXT、CSV `secret` 列和逐行补货，发货记录提供遮罩查看与确认后的原样重发。
- 订单与卡密：发现并同步近 90 天订单，区分签收、退款中、已退款和关闭状态；历史关键词规则仅对未选择显式商品发货方式的商品作为兼容兜底。普通用户没有自有匹配规则时，关键词发货自动回退管理员的主站规则并消耗主站卡密库存；自有规则永远优先，商品显式选择发货方式（绑定资源/关闭/邀请）不回退。
- 邀请履约桥：对商品级开关启用的订单接收邀请服务的 HMAC 操作；消息按 `mid` 等待闲鱼 WebSocket 对应响应，只有明确成功才写 `succeeded` 并允许后续发货，超时或未知结果失败关闭且不自动重发。
- 权限化设置：普通用户维护自己的商品同步节奏和 AI 平台；管理员额外管理全局基础、SMTP、注册开关和运行状态。
- 经营仪表盘：默认打开“今天”，首屏一次读取当前用户的账号、订单、库存和营收汇总，独立加载订单时段、成交爆品、客户行为和商品表现；今日趋势只画到东八区当前小时，峰谷和涨跌只使用已完成时段，历史单日保留 24 个小时桶，多日按日展示。页面在线且可见时每 15 秒后台刷新，营收、订单数和账号数使用自然滚动；付款核验后的新订单、退款完成和退款撤销会更新金额与订单明细。含管理员在内的所有角色的个人仪表盘都只看自己 `user_id` 归属的数据；另有全站经营合计接口对任何登录用户可见但只含站级总数，管理员独享分代理明细与账号总览，管理员权限另用于注册、用户和系统管理。真实商品流量只有在卖家后台指标适配器完成账号级三次金丝雀后才展示，默认关闭。
- 卖家自动好评：账号级默认关闭，只处理开关开启后新增且平台明确显示可评价的卖出订单；AI 生成事实克制的一句好评，失败时使用固定正向文案。
- 直接注册：管理员开关、图形验证码、邮箱验证码和普通用户容量共同控制注册；支持用户名或邮箱登录、两阶段密码找回、协议页面和普通用户启停。
- 统一公共入口：登录、注册、找回密码、服务条款和隐私说明复用主应用的品牌组件与视觉体系，页面版本由 `frontend/package.json` 在 Vite 构建时注入。

当前卖家自动好评边界：

| 能力 | 状态 |
| --- | --- |
| 账号级开关 | 可用，默认关闭 |
| 卖家评价买家 | 可用，只处理开关开启后的可评价订单 |
| AI 好评文案 | 可用，8-60 字；生成失败使用固定正向文案 |
| 提交节奏 | 严格串行，随机延迟 5-15 分钟 |
| 买家评价卖家 | 等待真实写入与回读闭环验证 |

调度器运行在单进程事件循环中，每 60 秒发现并串行处理任务。提交使用商家评价接口的 `tradeIdList`；只有 `data.module.success` 为真且 `successOrderIds` 包含目标订单时才记成功。明确拒绝记失败，结果不明确记 `needs_reconcile`，不会自动重发可能已经提交的评价。

## AI 上下文优先级

正式回复与训练测试使用同一套事实优先级：

1. 安全限制
2. 当前商品详情与已发布知识档案
3. 当前商品训练规则
4. 议价、技术或默认专家策略
5. 账号通用风格

因此切换商品后，AI 会使用新商品的标题、价格、详情与知识档案；某件商品的训练规则不会直接套到其他商品。

训练窗口优先读取当前商品的未发布草稿，真实买家自动回复只读取已发布版本。回复生成后会逐条审查当前适用规则；发现违反时最多自动重写一次，并把审查结果返回给训练界面。价格、套餐、档位和质保金额类规则是硬保护：重写后仍冲突时会返回规则兜底回复，而不是继续使用模型的违规报价。互相冲突的规则仍需人工整理，模型审查不能消除事实冲突。

## 商品知识工作流

1. 选择真实账号和商品，先用卖家自己的话填写“商品概览”。
2. 点击 AI 生成结构化草稿；AI 会结合概览、商品标题、价格和详情重建完整草稿，旧草稿内容会被整体替换。生成失败时保留原草稿。
3. 人工确认或修正 AI 内容，保存草稿并发布版本。
4. 只有已发布版本会进入真实买家自动回复；草稿可继续用于训练测试。

知识档案可复制到同一账号的其他商品。复制操作会整体覆盖目标商品草稿，保留目标已发布版本和历史版本，且不自动发布；发布前应核对价格、规格和交付差异。

## 账号重登与会话

同一后台用户重新扫码、密码登录或更新 Cookie 时，系统会优先按 Cookie 中的闲鱼 `unb` 找回原账号记录，因此原账号 ID、AI 配置、训练规则和商品知识可以继续复用。不要通过“删除账号”来解决过期登录：删除操作会清理该账号关联数据。

新增账号流程把三条用户路径分开，打开面板本身不会发起登录请求：

- **网页二维码（推荐）**：调用官方接口取得 `codeContent` 并在本地渲染，适合手机扫码，普通扫码不会启动浏览器。若平台要求继续交互风控，可切换到本机 Chrome 或浏览器扩展。
- **本机 Chrome 登录（备选，免安装）**：服务端用 Playwright Chromium 在这台 Mac 上打开闲鱼官方登录页；前端仅在回环与正式域名展示该入口，后端要求有效控制台登录态并把陌生来源记为观测 warning。
- **浏览器扩展导入**：独立使用当前 Chrome/Edge Profile，适合远程访问或需要扩展续期的设备；扩展检测只发生在这一入口。

扩展只通过一次性设备证明提交登录态；Cookie、Token、密码和验证码不经过前端页面。服务端仍会真实验证平台 Token、`unb` 身份和账号落库。只有前端再次确认账号出现在账号列表后，本机 Chrome 窗口或扩展标签页才会关闭。原“本机助手”客户端已彻底移除；历史上以助手登录的账号数据仍按原样保留和展示。

手动 Cookie 和手动扩展配对仍作为高级人工导入方式保留。

扩展的 SMS/密码登录不会在控制台收集手机号、密码或验证码。自动续期当前仅由扩展设备承接：扩展密码登录成功后，用户可以另行填写并明确授权保存续期凭据；凭据使用独立密钥加密，并只绑定一个扩展设备。续期任务通过 5 分钟一次性加密交付发送到该设备，首次领取后服务端立即清空密文，不能重复领取。

系统按真实 `unb` 保存或归并账号，并保留账号规则和商品知识。只有真实平台 Token、`unb`、关键会话 Cookie 和账号落库都通过后才显示成功；关闭添加弹窗会主动结束未完成的登录会话，而不是把它判定为成功。完整能力边界见 [登录策略](docs/login-strategy.md)，请求示例见 [接入指南](docs/integration-guide.md#account-binding-and-refresh)。

## 直接注册与找回

公开注册默认关闭。普通用户上限默认为 20，可由管理员设置为 1–1000；管理员不计入容量，停用的普通用户仍占名额。管理员需要先在“系统与 AI”中保存 SMTP 与独立支持邮箱，发送 6 位 SMTP 收件码并从真实收件箱填回确认，之后才能手动开启注册。SMTP 配置、指纹或授权码发生变化时，验证状态会立即失效并关闭注册。

注册使用一次性图形验证码和用途隔离的邮件验证码；管理员可在注册管理中开启“强制邀请码”，开启后注册必须携带有效邀请码（HMAC 摘要存储、明文仅创建时展示一次、支持有效期与吊销），并在注册事务内一次性消费。首次邮件发送成功后，页面保留“已发送”状态，不会立即请求或刷新新的图形验证码；冷却结束后，只有用户明确选择重发，页面才会获取并要求通过新的图形验证码。验证码和网络标识在数据库中只保存 HMAC 摘要；用户名和邮箱按规范化值检查唯一性。注册事务会重新检查开关、容量、协议版本和验证码，成功后直接创建摘要 Session 并登录。最后一个名额使用后注册开关自动关闭；提高上限后仍需管理员手动重新开放。

登录框接受用户名或邮箱。密码找回先调用 `POST /api/auth/password-reset/verify-code` 校验注册邮箱验证码并取得一次性重置授权，公开页面只在当前组件内存中保存该授权；随后 `POST /api/auth/password-reset` 消费授权并设置新密码。服务端只在现有 `auth_challenges` 表保存授权摘要，因此 v1.7.2 不新增数据库迁移；旧版直接提交 `challenge_id`、`verification_code` 和新密码的负载暂时兼容。重置成功会撤销该用户的全部旧 Session。管理员设置页只启停普通用户，不提供注册页删除入口。`/terms` 与 `/privacy` 记录当前技术处理方式，不声称经过法律审查。

## AI 平台与模型

“系统与 AI”中的平台配置库支持 DeepSeek、OpenAI、通义千问、OpenRouter、硅基流动、Gemini 和自定义 OpenAI 兼容接口。平台 Key 集中加密保存，账号只选择平台与模型。普通用户未配置任何平台时，AI 回复按“账号绑定平台 > 账号自有 Key > 管理员站级默认已验证配置 > 系统全局 Key”回退；使用站级共享时前端显示“主站共享配置”且不下发管理员的平台明文或 profile 归属。

普通用户只读取和修改自己的 AI 平台及个人商品同步设置，不会收到 SMTP、注册管理、全局登录安全或系统运行指标。个人同步设置缺失时继承管理员全局默认值，保存后按后台用户隔离，并应用到该用户拥有的闲鱼实例。

- OpenAI 兼容接口读取标准 `/models`；Gemini 读取 `models.list` 并仅保留支持文本生成的模型。
- 模型列表无法读取时可以手填模型 ID。
- 平台或模型切换必须先生成测试回复，成功后才会写入账号线上配置。
- 测试失败不会修改账号当前使用的平台和模型，也不会静默切换到其他收费平台。
- 入站图片消息会从闲鱼 CDN 校验后转为 OpenAI 兼容视觉请求；中转 URL 需要指向支持视觉输入的模型，纯文本模型仍按原文本路径处理。
- Anthropic 原生 Messages API 暂未接入；Claude 模型可通过 OpenRouter 等兼容网关使用。

## 技术栈

- 后端：Python 3.11+、FastAPI、SQLite、Playwright、WebSocket
- 前端：React 19、TypeScript、Vite、Tailwind CSS
- AI：OpenAI 兼容接口、Google Gemini 原生接口

## 本地运行

```bash
git clone https://github.com/johenking/xianyu-ai-manager.git
cd xianyu-ai-manager

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium

cd frontend
npm ci
npm run build
cd ..

cp .env.example .env
python Start.py
```

打开 `http://127.0.0.1:8091`。

存活探针为 `/health/live`，就绪探针为 `/health/ready`，兼容探针 `/health` 继续保留。当前架构只支持单实例、单 Uvicorn worker；请保持 `WEB_CONCURRENCY=1`。

默认后台用户名为 `admin`。请在 `.env` 中设置强密码 `ADMIN_PASSWORD` 和随机 `JWT_SECRET_KEY`，不要在公网使用默认值。
AI 平台密钥使用 Fernet 加密保存。生产环境请另外设置随机的 `AI_PROVIDER_ENCRYPTION_KEY`；未设置时会在 `data/.ai_provider_key` 生成仅本机可读的密钥文件，请与数据库一起备份且不要提交。
闲鱼账号登录密码使用另一把 Fernet 密钥。生产环境建议设置独立的 `ACCOUNT_CREDENTIAL_ENCRYPTION_KEY`；未设置时会生成权限为 `0600` 的 `data/.account_credential_key`。数据库迁移会在修改前同时备份数据库和本地密钥。
SMTP 授权码、验证码摘要和脱敏网络标识使用第三把系统秘密。生产环境建议设置 `SYSTEM_SECRET_ENCRYPTION_KEY`；未设置时会生成 `data/.system_secret_key`。这三把本地密钥必须与 SQLite 一起备份，缺失任何一把都可能导致相应密文或摘要无法继续使用。

`requirements.txt` 保留旧的安装入口，实际引用 Python 3.11 生成的精确 `requirements.lock`。更新依赖时修改 `requirements.in` 并重新生成锁文件；测试、Ruff 和构建工具使用 `requirements-dev.lock`。

## Docker

```bash
cp .env.example .env
# 编辑 .env，至少设置 ADMIN_PASSWORD 和 JWT_SECRET_KEY
docker compose up --build -d
```

默认映射到 `http://127.0.0.1:8080`。SQLite、日志和上传目录通过本地卷持久化。

## 配置与秘密

- 全局 AI Key 与 SMTP 密码不会通过设置 API 明文返回。
- 平台 API Key 使用 Fernet 加密保存，平台接口只返回配置状态和掩码。
- 后台密码使用 bcrypt cost 12；旧 SHA-256 密码会在一次成功登录后自动升级。
- 新后台 Session 只保存 Token 摘要，旧 Session 在过渡期内继续兼容。
- 闲鱼账号登录密码使用独立密钥加密，接口不返回密码或密文。
- 账号专属 AI Key 使用 `keep / set / clear` 操作，空输入不会误删旧 Key。
- 认证日志不得记录默认管理员密码、邮件一次性验证码、密码重置授权、完整邮箱或任何密码；需要标识邮箱时只使用脱敏值或摘要。
- API 请求日志使用匹配后的路由模板和请求 ID，不记录动态路径里的账号身份；原始 Uvicorn access log 默认关闭。正常 WebSocket 断连只记录脱敏警告，不输出异常堆栈。
- 不要提交 `data/`、数据库、Cookie、日志、浏览器状态、上传文件或 `.env`。
- SMTP 对核心闲鱼自动化仍是可选能力，但直接注册和密码找回依赖它。QQ 邮箱可使用 `smtp.qq.com:465`、SSL 开启、STARTTLS 关闭；SMTP 只有在独立支持邮箱收到 6 位验证码并填回确认后才算已验证。

## 测试

```bash
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/python -m py_compile Start.py app_factory.py application_runtime.py api_routers.py auth_email_service.py auth_registration_service.py settings_service.py db_manager.py schema_migrations.py security_utils.py session_registry.py official_login_sessions.py repositories/auth_repository.py repositories/runtime_session_repository.py services/auth_service.py ai_provider_service.py ai_reply_engine.py auto_rate_service.py backfill_auto_rates.py account_session_refresh.py order_sync_service.py item_metric_service.py item_metric_scheduler.py invite_bridge.py invite_bridge_poller.py backfill_order_snapshots.py browser_extension_pairing.py cloudflared_watchdog.py reply_server.py XianyuAutoAsync.py utils/browser_interaction.py utils/xianyu_official_login.py utils/xianyu_session_probe.py utils/qr_login.py utils/qr_verification_browser.py utils/xianyu_l3_memory.py utils/outbound_http.py utils/outbound_smtp.py utils/outbound_dns.py utils/xianyu_message.py utils/xianyu_slider_stealth.py utils/verification_images.py
.venv/bin/python -m pytest -q tests
ruff check .

cd frontend
npm audit --audit-level=high
npm run typecheck
npm test
npm run build
npm run build
npm run verify:build
```

## 来源与许可

本项目以 [zhinianboke/xianyu-auto-reply](https://github.com/zhinianboke/xianyu-auto-reply) 为主要上游进行修改，上游使用 AGPL-3.0。本项目同样使用 [AGPL-3.0](LICENSE)。

历史来源、已退役能力和修改说明见 [NOTICE](NOTICE)。贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题请阅读 [SECURITY.md](SECURITY.md)。

## 免责声明

本项目按现状提供，不保证平台接口、风控策略或页面结构长期稳定。使用者应自行承担账号、数据与合规风险。
