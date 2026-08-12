# 闲鱼监控台当前进度

> 仅记录闲鱼监控台拥有的运行状态：账号与消息监听、AI 回复、订单发现、商品开关和桥接操作。邀请确认页、账号池、兑换码、兑换流程与资格动作属于独立邀请项目 `/Users/mac/Documents/Codex/2026-08-04/wo-f`。

## 生产运行态（2026-08-11）

- 唯一生产实例由 `com.cxywjx.xianyu-manager` LaunchAgent 启动，运行目录为 `/Users/mac/Library/Application Support/XianyuManager`；`8091` 只有一个监听进程。
- `GET /health/ready` 返回 `ready`，数据库与 Cookie 管理器正常，迁移版本为 `2026080902`。Shadowrocket VPN 保持连接。
- 邀请订单事件通过 `http://127.0.0.1:8081` 发送给同机邀请服务；买家公网链接不经过该服务间地址。
- 邀请商品范围只读取 `(cookie_id, item_id)` 对应的 `item_info.invite_auto_fulfillment`。新同步商品默认关闭，标题与环境变量商品列表不参与路由。

## 订单仪表盘

- 所有角色的仪表盘都固定按当前登录用户隔离；管理员只额外拥有注册、用户和系统管理能力，不再存在 `system` 仪表盘分支。
- 邀请订单发现现在会把平台金额和订单时间规范化写入 `paid_amount_fen` 与 `ordered_at_utc`。新待发货订单首次落库即写入；近 7 天已发货/已完成订单重复发现时只补空字段，保留状态棘轮和已有可信值。
- 2026-08-11 部署前修复批次为 8 笔、5 已发货、3 已完成，可信金额/时间覆盖率均为 0/8；部署后该批次为 8/8、营收合计 57.24、状态变化 0。浏览器验收期间新增 1 笔待发货订单，最终实时汇总为 9 笔、61.04，金额与时间覆盖率均为 9/9。
- 无账号所有者的 21 笔历史订单原样保留且不进入任何用户仪表盘。真实商品流量仍由独立卖家后台指标适配器提供，当前默认关闭。

## AI 回复

- 消息 Token 探测已恢复，主 WebSocket 保持连接。Provider 通过本机可信 DNS 解析路径验证，不需要关闭 VPN。
- AI 入站故障根因是 `handle_message()` 内重复局部导入让模块级 `db_manager` 变为未赋值局部变量；买家消息在进入 AI 调度前触发 `UnboundLocalError`。修复只删除重复局部导入，继续使用模块级实例。
- 唯一真实回归 canary 已闭环：1 条买家入站、1 次 Provider 调用、1 条 assistant 记录、1 次回复提交。关键词与默认回复优先级未改变，也没有第二次 canary。

## 邀请桥接

- 发送消息复用账号唯一主 WebSocket，按 `mid` 关联响应；启动 push 优先 ACK，不再创建竞争连接。操作键持久化在 `invite_bridge_operations`，`submitted` 不等于平台已发货。
- 11.64 元 quantity=`3` 拼单当前为 `completed`、`system_shipped=1`；确认消息、唯一键发货消息和 `mark_fulfilled` 各成功一次。
- 8.60 元 quantity=`2` 拼单当前为 `shipped`、`system_shipped=0`；`mark_fulfilled` 成功一次。该旗标为命中“平台已发货”幂等分支后的既有状态，不要手工修改。
- 8.60 元订单保留一条历史发货消息 `needs_review` 和随后一次人工唯一键补发记录。两者都是审计事实，禁止重试历史操作或再次发送卡密。
- 两笔订单的资格动作修复未新增监控侧消息或发货操作。消息 `submitted` 仍只证明 WebSocket 写入，不等于买家收件。

## 验证与恢复

- 2026-08-11 已保存的完整监控台门禁为 `812 passed`、`164 subtests`、0 fail；本次邀请桥定向门禁为 `39 passed`，Provider 定向门禁、Ruff、编译与 `git diff --check` 均通过。
- 仪表盘修复定向门禁为后端 `54 passed`、`4 subtests passed`，订单覆盖率测试 `3 passed`，Dashboard 前端 `4 passed`，Vite 构建完成；生产 readiness、单监听、SQLite integrity、真实浏览器 `scope=user` 和回滚 `--check` 均通过。证据与回滚位于 `/Users/mac/Desktop/xianyu-dashboard-repair-20260811-210849/`。
- AI 入站修复回滚单元：`/Users/mac/Library/Application Support/XianyuManager Rollbacks/ai-inbound-scope-20260811-1112`。
- 订单桥与历史会话恢复回滚单元保存在 `/Users/mac/Library/Application Support/XianyuManager Rollbacks/`；执行任何回滚前先运行对应 `rollback.sh --check`，不得覆盖运行后新增订单。

## 下一步

1. 保持单实例和现有 poller，继续观察新付款订单；已完成订单不重放。
2. 保留历史 `needs_review` 操作作为审计记录，只有出现新的外部证据时才按稳定 operation key 对账。
3. 继续区分 `submitted`、平台 `shipped` 与买家实际收件，不从其中一个状态推导另两个状态。
