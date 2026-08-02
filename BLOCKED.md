# 外部门禁

- macOS 当前只有开发证书，没有 Developer ID Application 与公证凭据；候选 `.app` 可完成本机验证，但大众下载需发行签名和公证。
- Windows `.exe` 已由原生 Windows runner 构建并完成结构校验；Windows Authenticode 证书尚未配置。
- 真实平台验收需要普通用户在自己的电脑启动助手并完成扫码、短信、密码、滑块、人脸等实际验证；完成前不把消息 Token、身份匹配、账号落库和成功后关页记为真人金丝雀通过。
