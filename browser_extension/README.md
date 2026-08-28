# 咸鱼监控台 Chrome 登录态导入扩展

该扩展是独立的浏览器导入与续期路径，支持 Chrome 和 Edge；网页二维码是推荐登录路径，本机访问时可用由服务端直接打开 Chromium 的“本机 Chrome 登录”备选路径（免安装），两者都不依赖扩展检测。用户在自己的浏览器中完成
扫码、手机号、账号密码以及滑块、人脸等官方验证，扩展按闲鱼/淘宝域名读取登录 Cookie
和 User-Agent，交给固定正式地址 `https://xianyu.cxywjx.top`。它也保留一个
高级手动 Cookie 导入入口。扩展不接受配对信息指定其他服务器，不把私钥或 Cookie
写入可导出的浏览器存储。

## 安装

1. 解压 `dist/xianyu-browser-bridge-1.2.2.zip`。
2. 在 Chrome 打开 `chrome://extensions`，或在 Edge 打开 `edge://extensions`，开启开发者模式。
3. 点击“加载已解压的扩展程序”，选择解压后的目录。
4. 打开咸鱼监控台并保持登录，在独立的“浏览器扩展导入”入口启动扩展流程；本机 Chrome 登录和网页二维码入口不会检测本扩展。
5. 如果使用手动配对导入，在“添加账号 → 高级与运维方式 → 你的 Chrome”创建配对，在 Chrome 登录闲鱼后点击扩展图标粘贴配对信息导入；不必把闲鱼保持为当前标签页。

扩展登录会先用一次性 P-256 设备证明创建挑战，再提交 Cookie；服务端真实调用平台接口确认
Token 和账号 `unb`，并在账号落库后等待控制台确认，确认完成后才关闭官方标签页。
高级配对 Token 五分钟内有效且只能使用一次。扩展导入后，服务端同样会确认登录态和账号
`unb`，验证通过后才保存并启动监听。

## 构建与校验

在 `frontend/` 目录运行 `npm run build:extension`，会以固定文件顺序和时间戳生成
`dist/xianyu-browser-bridge-1.2.2.zip`，并复制同一字节流到版本化公开地址和兼容地址
`static/downloads/xianyu-cookie-importer.zip`。随后运行 `npm run verify:extension`
解包校验三个归档的 12 个必需文件、Manifest 版本和逐文件 SHA-256。
