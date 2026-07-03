# Xianyu AI Manager

闲鱼多账号商品管理、自动回复、自动发货与商品级 AI 知识管理平台。

[![CI](https://github.com/johenking/xianyu-ai-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/johenking/xianyu-ai-manager/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)

> 本项目会操作真实闲鱼账号、Cookie 与订单数据。请仅在您有权管理的账号上使用，并遵守平台规则。自动化登录与浏览器操作可能触发平台风控，项目不承诺绕过验证。

## 功能

- 多账号管理：扫码、账号密码、手动 Cookie，监听与自动确认状态诊断。
- 商品管理：同步真实商品，为每件商品维护独立知识档案和训练规则。
- AI 客服：商品事实优先，按议价、技术、默认三类专家策略回复；不同账号可选择不同平台和模型。
- AI 训练：在独立对话框中模拟买家咨询，修正规则确认后才写入线上配置。
- 关键词回复：账号级关键词回复、默认回复与关键词发货规则。
- 订单与卡密：订单同步、状态管理、卡密库存与自动发货规则。
- 系统设置：基础、AI、SMTP 三个独立配置区，保存复读确认和真实连接检测。
- 技能中心：手动真实商品监控、专家提示词、运行诊断。

当前技能中心能力边界：

| 能力 | 状态 |
| --- | --- |
| 手动执行一次真实商品搜索 | 可用 |
| 专家策略用于测试与正式回复 | 可用 |
| 定时监控调度 | 暂不可用 |
| AI 商品过滤 | 暂不可用 |
| 监控结果通知发送 | 暂不可用 |

未实现能力会在界面和 API 中明确返回“暂不可用”，不会伪装成已排队或已发送。

## AI 上下文优先级

正式回复与训练测试使用同一套事实优先级：

1. 安全限制
2. 当前商品详情与已发布知识档案
3. 当前商品训练规则
4. 议价、技术或默认专家策略
5. 账号通用风格

因此切换商品后，AI 会使用新商品的标题、价格、详情与知识档案；某件商品的训练规则不会直接套到其他商品。

## AI 平台与模型

“系统与 AI”中的平台配置库支持 DeepSeek、OpenAI、通义千问、OpenRouter、硅基流动、Gemini 和自定义 OpenAI 兼容接口。平台 Key 集中加密保存，账号只选择平台与模型。

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

默认后台用户名为 `admin`。请在 `.env` 中设置强密码 `ADMIN_PASSWORD` 和随机 `JWT_SECRET_KEY`，不要在公网使用默认值。
AI 平台密钥使用 Fernet 加密保存。生产环境请另外设置随机的 `AI_PROVIDER_ENCRYPTION_KEY`；未设置时会在 `data/.ai_provider_key` 生成仅本机可读的密钥文件，请与数据库一起备份且不要提交。

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
- 账号专属 AI Key 使用 `keep / set / clear` 操作，空输入不会误删旧 Key。
- 不要提交 `data/`、数据库、Cookie、日志、浏览器状态、上传文件或 `.env`。
- SMTP 是可选能力，未配置不代表系统故障；连接检测只做连接与认证，不发送邮件。

## 测试

```bash
.venv/bin/python -m py_compile settings_service.py db_manager.py ai_reply_engine.py reply_server.py XianyuAutoAsync.py
.venv/bin/python -m unittest discover -s tests -v

cd frontend
npm audit --audit-level=high
npm exec tsc -- --noEmit
npm test
npm run build
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
