# 咸鱼监控台 Chrome 登录态导入扩展

该扩展是当前设备浏览器登录的桥接层，支持 Chrome 和 Edge。用户在自己的浏览器中完成
扫码、手机号、账号密码以及滑块、人脸等官方验证，扩展只把设备公钥、当前标签页的
登录 Cookie 和 User-Agent 交给固定正式地址 `https://xianyu.cxywjx.top`。它也保留一个
高级手动 Cookie 导入入口。扩展不接受配对信息指定其他服务器，不把私钥或 Cookie
写入可导出的浏览器存储。

## 安装

1. 解压 `dist/xianyu-cookie-importer.zip`。
2. 在 Chrome 打开 `chrome://extensions`，或在 Edge 打开 `edge://extensions`，开启开发者模式。
3. 点击“加载已解压的扩展程序”，选择解压后的目录。
4. 打开咸鱼监控台并保持登录；普通 QR、手机号和密码流程会自动通过扩展打开当前设备的官方标签页。
5. 如果使用高级导入，在“添加账号 → 高级与运维方式 → 你的 Chrome”创建配对，打开并登录闲鱼官网，点击扩展图标，粘贴配对信息后主动导入。

当前设备登录会先用一次性 P-256 设备证明创建挑战，再提交 Cookie；服务端真实调用平台接口确认
Token 和账号 `unb`，并在账号落库后等待控制台确认，确认完成后才关闭官方标签页。
高级配对 Token 五分钟内有效且只能使用一次。扩展导入后，服务端同样会确认登录态和账号
`unb`，验证通过后才保存并启动监听。

## 构建与校验

在 `frontend/` 目录运行 `npm run build:extension`，会以固定文件顺序和时间戳生成
`dist/xianyu-cookie-importer.zip`，并复制同一字节流到
`static/downloads/xianyu-cookie-importer.zip`。随后运行 `npm run verify:extension`
校验扩展源码与两个归档的 SHA-256 一致性。
