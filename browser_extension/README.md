# 咸鱼监控台 Chrome 登录态导入扩展

该扩展只在用户点击按钮时读取当前 Chrome Cookie Store 中的闲鱼/淘宝域名 Cookie，
并通过一次性配对码发送到本机 `http://127.0.0.1:8091`。扩展没有后台任务、没有
远程服务器地址，也不使用浏览器存储保存 Cookie 或配对信息。

## 安装

1. 解压 `dist/xianyu-cookie-importer.zip`。
2. 在 Chrome 打开 `chrome://extensions`，开启“开发者模式”。
3. 点击“加载已解压的扩展程序”，选择解压后的目录。
4. 在咸鱼监控台“添加账号 → 从本机 Chrome 导入”创建配对并复制配对信息。
5. 打开并登录闲鱼官网，点击扩展图标，粘贴配对信息后主动导入。

每个配对码五分钟内有效且只能使用一次。扩展导入后，后台仍会调用真实平台接口
确认登录态和账号 `unb`，验证通过后才保存并启动监听。

## 构建与校验

在 `frontend/` 目录运行 `npm run build:extension`，会以固定文件顺序和时间戳生成
`dist/xianyu-cookie-importer.zip`，并复制同一字节流到
`static/downloads/xianyu-cookie-importer.zip`。随后运行 `npm run verify:extension`
校验扩展源码与两个归档的 SHA-256 一致性。
