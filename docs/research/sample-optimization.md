# SAMPLE 优化结论

日期：2026-08-10。对象：`SAMPLE=/Users/mac/Downloads/AgisoIdleClient_x64_1.2.4.exe`，原件 SHA-256=`52bc5a4711cb5d0ddab4e19146d123dad50a5101ccf891d7b88717699f41ef54`。原件只复制和 hash，未执行生产实例、未写入账号数据。

## 三条可复现结论

1. **外壳不是业务模块。** `file SAMPLE` 输出 `PE32 executable (GUI) Intel 80386, for MS Windows`；既有 `docs/research/sample-feasibility.md` 的 PE/Rizin 记录确认 Inno Setup 6.4.3、11 个 section、入口 stub 的局部控制流。它证明的是 Windows 安装包装层，不能作为 Mac 业务代码来源。复核命令：`file SAMPLE`、`rz-bin -F pe -ej/-Ij/-ij/-Uj SAMPLE`、`rizin -2 -q -F pe -c 's 0x4acfe0; af; afi; afbj; q' SAMPLE`。

2. **overlay 是独立的高熵数据层。** PE section raw end=`1,020,928`，从该偏移切出的 overlay 为 `93,961,052` bytes，SHA-256=`d29234c926455377eb646915ccf8f743903e14b7ded0c70315bee49e8733e372`，熵=`7.99999824/8`；这些值只证明包装层之后存在高熵数据，不把它当作已解析业务代码。复核记录：`/private/tmp/sample-analysis-20260810-161325/logs/overlay-meta.log` 与 `entropy.log`；原始命令及 literal 输出在 `docs/research/sample-verification.txt`。

3. **payload 未形成可复用业务合同，唯一落地价值是 helper 登录前置诊断。** 既有固定 parser、`bsdtar`、7-Zip 的原始 stderr 均在 `docs/research/sample-verification.txt` 指向的日志中，提取文件数为 `0`；`TARGET_WINDOWS_RUNNER=unset`，进程、注册表、端口、DNS、HTTP/WebSocket 行为标记 `unknown`，密码尝试为 `0`。因此本次只在既有 loopback `/health` -> `/v1/device` -> 设备证明/登录状态机接缝增加无秘密 `platform/arch/protocolVersion/installed/startupRegistered/running` 诊断，AccountList 形成 `missing | malformed | outdated | platform/arch/startup/not-running | ready` 状态；Origin、loopback、设备证明和数据库/业务 API 不变。

## 能力表

| 能力 | SAMPLE 证据 | 输入/状态/输出 | 现有接缝 | 置信度/下一步 |
|---|---|---|---|---|
| helper 登录 | 无 payload；仓库 helper 契约可复现 | health -> preflight state -> device/session | `/health`、`/v1/device`、AccountList | 高（仓库合同）；用真人设备 canary 复核 |
| 消息 WebSocket | SAMPLE 无字节/端口证据 | 现有 listener 状态未知 | `XianyuAutoAsync.py` listener | unknown；需 Windows runner/payload |
| 订单同步 | SAMPLE 无业务字段 | 现有结构化订单同步 | `order_sync_service.py` | unknown；不从安装器推断 |
| 指标门禁 | SAMPLE 无卖家后台证据 | 现有默认关闭 canary | `item_metric_service.py` | unknown；等待真人 canary |
