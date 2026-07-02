"""
极验验证码配置

说明：
- captcha_id 和 private_key 需要从极验官网申请
- 当前使用的是示例配置，生产环境请替换为自己的密钥
"""
import os


class GeetestConfig:
    """极验验证码配置类"""

    # 极验分配的 captcha_id，仅从环境变量读取。
    CAPTCHA_ID = os.getenv("GEETEST_CAPTCHA_ID", "")

    # 极验分配的私钥，源码不提供默认值。
    PRIVATE_KEY = os.getenv("GEETEST_PRIVATE_KEY", "")

    # 用户标识（可选）
    USER_ID = os.getenv("GEETEST_USER_ID", "xianyu_system")

    # 客户端类型：web, h5, native, unknown
    CLIENT_TYPE = "web"

    # API地址
    API_URL = "http://api.geetest.com"
    REGISTER_URL = "/register.php"
    VALIDATE_URL = "/validate.php"

    # 请求超时时间（秒）
    TIMEOUT = 5

    # SDK版本
    VERSION = "python-fastapi:1.0.0"
