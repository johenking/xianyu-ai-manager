# Xianyu AI Manager

闲鱼多账号商品管理、自动回复、自动发货与商品级 AI 知识管理平台。

[![CI](https://github.com/johenking/xianyu-ai-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/johenking/xianyu-ai-manager/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)

> 本项目会操作真实闲鱼账号、Cookie 与订单数据。请仅在您有权管理的账号上使用，并遵守平台规则。自动化登录与浏览器操作可能触发平台风控，项目不承诺绕过验证。

## 功能

- 多账号管理：官方账号密码登录、扫码和手动 Cookie 绑定，按真实 `unb` 保留账号数据；持久浏览器档案可续期 Cookie，账号级定时刷新默认关闭。
- 商品管理：按账号筛选和同步真实商品，只有选择“全部账号”时才展示全量商品；每件商品可维护独立知识档案和训练规则，相同商品可复刻知识草稿。
- AI 客服：商品事实优先，按议价、技术、默认三类专家策略回复；不同账号可选择不同平台和模型。
- AI 训练：在独立对话框中模拟买家咨询，显示实际加载、排除和停用的规则；修正确认后才写入线上配置。
- 关键词回复：账号级关键词回复、默认回复与关键词发货规则。
- 订单与卡密：发现并同步近 90 天订单，区分签收、退款中、已退款和关闭状态，并管理卡密库存与自动发货规则。
- 权限化设置：普通用户维护自己的商品同步节奏和 AI 平台；管理员额外管理全局基础、SMTP、注册开关和运行状态。
- 经营仪表盘：首屏一次读取当前用户的账号、订单、库存和营收汇总，图表与订单明细延后加载；管理员查看系统范围。
- 技能中心：手动与定时真实商品监控、AI 商品筛选、结果通知、专家提示词和运行诊断。
- 直接注册：管理员开关、图形验证码、邮箱验证码和普通用户容量共同控制注册；支持用户名或邮箱登录、密码找回、协议页面和普通用户启停。

当前技能中心能力边界：

| 能力 | 状态 |
| --- | --- |
| 手动执行一次真实商品搜索 | 可用 |
| 专家策略用于测试与正式回复 | 可用 |
| 定时监控调度 | 可用，默认关闭，最短 15 分钟 |
| AI 商品过滤 | 可用，需要至少一个账号完成 AI 配置 |
| 监控结果通知发送 | 可用，支持 Webhook、微信、钉钉、飞书、Bark、Telegram |

调度器运行在单进程事件循环中，每 30 秒检查到期任务。结果按任务和商品链接去重，缺少链接时使用商品 ID；通知会尝试全部已启用的受支持渠道，并记录 `sent`、`partial` 或 `failed`，不会把未发送结果伪装成成功。

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
2. 点击 AI 生成结构化草稿；AI 会结合概览、商品标题、价格和详情补全字段。
3. 人工确认或修正 AI 内容，保存草稿并发布版本。
4. 只有已发布版本会进入真实买家自动回复；草稿可继续用于训练测试。

知识档案可复制到同一账号的其他商品。复制操作只写入目标商品草稿，默认跳过已有知识的目标，也不会自动发布；发布前应核对价格、规格和交付差异。

## 账号重登与会话

同一后台用户重新扫码、密码登录或更新 Cookie 时，系统会优先按 Cookie 中的闲鱼 `unb` 找回原账号记录，因此原账号 ID、AI 配置、训练规则和商品知识可以继续复用。不要通过“删除账号”来解决过期登录：删除操作会清理该账号关联数据。

首次账号密码登录会打开闲鱼官方登录页，从默认短信模式切换到密码模式，并在成功后读取真实 `unb`，把临时浏览器档案归档为 `browser_data/user_<unb>`。前端不再要求账号 ID；旧客户端传入的 `account_id` 仅为兼容字段，不参与身份判定。登录密码使用独立密钥加密保存，接口不会返回明文或密文。

Token 失效、手动刷新和账号级定时刷新都复用同一官方浏览器档案。档案仍有效时可直接续期，不需要反复扫码；档案彻底退出时，系统才使用已保存凭据重新登录。定时刷新默认关闭，可设置 1 小时到 7 天的间隔；关闭时 Token 异常只记录状态，不会自动启动 Chrome，手动“立即刷新”仍可使用。

闲鱼要求短信、扫码、人脸或其他安全验证时，系统会保留可见浏览器并最多等待 15 分钟，同时在账号页显示验证截图。验证不能绕过，完成后系统才会继续保存 Cookie 并恢复监听。官方页面结构或风控策略变化时，密码登录仍可能需要维护；不要通过删除账号来重试登录。

## 直接注册与找回

公开注册默认关闭。普通用户上限默认为 20，可由管理员设置为 1–1000；管理员不计入容量，停用的普通用户仍占名额。管理员需要先在“系统与 AI”中保存 SMTP 与独立支持邮箱，发送 6 位 SMTP 收件码并从真实收件箱填回确认，之后才能手动开启注册。SMTP 配置、指纹或授权码发生变化时，验证状态会立即失效并关闭注册。

注册页面不需要邀请码，使用一次性图形验证码和用途隔离的邮件验证码。验证码和网络标识在数据库中只保存 HMAC 摘要；用户名和邮箱按规范化值检查唯一性。注册事务会重新检查开关、容量、协议版本和验证码，成功后直接创建摘要 Session 并登录。最后一个名额使用后注册开关自动关闭；提高上限后仍需管理员手动重新开放。

登录框接受用户名或邮箱。密码找回需要注册邮箱验证码，重置成功会撤销该用户的全部旧 Session。管理员设置页只启停普通用户，不提供注册页删除入口。`/terms` 与 `/privacy` 记录当前技术处理方式，不声称经过法律审查。

## AI 平台与模型

“系统与 AI”中的平台配置库支持 DeepSeek、OpenAI、通义千问、OpenRouter、硅基流动、Gemini 和自定义 OpenAI 兼容接口。平台 Key 集中加密保存，账号只选择平台与模型。

普通用户只读取和修改自己的 AI 平台及个人商品同步设置，不会收到 SMTP、注册管理、全局登录安全或系统运行指标。个人同步设置缺失时继承管理员全局默认值，保存后按后台用户隔离，并应用到该用户拥有的闲鱼实例。

- OpenAI 兼容接口读取标准 `/models`；Gemini 读取 `models.list` 并仅保留支持文本生成的模型。
- 模型列表无法读取时可以手填模型 ID。
- 平台或模型切换必须先生成测试回复，成功后才会写入账号线上配置。
- 测试失败不会修改账号当前使用的平台和模型，也不会静默切换到其他收费平台。
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
- 不要提交 `data/`、数据库、Cookie、日志、浏览器状态、上传文件或 `.env`。
- SMTP 对核心闲鱼自动化仍是可选能力，但直接注册和密码找回依赖它。QQ 邮箱可使用 `smtp.qq.com:465`、SSL 开启、STARTTLS 关闭；SMTP 只有在独立支持邮箱收到 6 位验证码并填回确认后才算已验证。

## 测试

```bash
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/python -m py_compile Start.py app_factory.py application_runtime.py api_routers.py auth_email_service.py auth_registration_service.py settings_service.py db_manager.py schema_migrations.py security_utils.py session_registry.py repositories/auth_repository.py repositories/runtime_session_repository.py services/auth_service.py ai_provider_service.py ai_reply_engine.py account_session_refresh.py order_sync_service.py skill_monitor_scheduler.py reply_server.py XianyuAutoAsync.py utils/xianyu_official_login.py
.venv/bin/python -m unittest discover -s tests -v
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

技能中心是独立安全重写，设计参考：

- [Usagi-org/ai-goofish-monitor](https://github.com/Usagi-org/ai-goofish-monitor)：监控流程
- [shaxiu/XianyuAutoAgent](https://github.com/shaxiu/XianyuAutoAgent)：专家策略
- [GuDong2003/xianyu-auto-reply-fix](https://github.com/GuDong2003/xianyu-auto-reply-fix)：诊断思路

完整归属和修改说明见 [NOTICE](NOTICE)。贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题请阅读 [SECURITY.md](SECURITY.md)。

## 免责声明

本项目按现状提供，不保证平台接口、风控策略或页面结构长期稳定。使用者应自行承担账号、数据与合规风险。
