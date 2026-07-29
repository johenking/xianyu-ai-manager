import base64
import json
import subprocess
from functools import partial
import time
import hashlib
import struct
import os
from typing import Any, Dict, List

from loguru import logger

subprocess.Popen = partial(subprocess.Popen, encoding="utf-8")
import execjs

def get_js_path():
    """获取JavaScript文件的路径"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(current_dir)
    js_path = os.path.join(root_dir, 'static', 'xianyu_js_version_2.js')
    return js_path

try:
    # 检查JavaScript运行时是否可用
    available_runtimes = execjs.runtime_names
    logger.info(f"可用的JavaScript运行时: {available_runtimes}")

    # 尝试获取默认运行时
    current_runtime = execjs.get()
    logger.info(f"当前JavaScript运行时: {current_runtime.name}")

    with open(get_js_path(), 'r', encoding='utf-8') as javascript_file:
        xianyu_js = execjs.compile(javascript_file.read())
    logger.info("JavaScript文件加载成功")
except Exception as e:
    error_msg = str(e)
    logger.error(f"JavaScript运行时错误: {error_msg}")

    if "Could not find an available JavaScript runtime" in error_msg:
        logger.error("解决方案:")
        logger.error("1. 确保已安装Node.js: apt-get install nodejs")
        logger.error("2. 或安装其他JS运行时: apt-get install nodejs npm")
        logger.error("3. 检查PATH环境变量是否包含Node.js路径")

        # 尝试检测系统中的JavaScript运行时
        import subprocess
        try:
            result = subprocess.run(['node', '--version'], capture_output=True, text=True)
            if result.returncode == 0:
                logger.info(f"检测到Node.js版本: {result.stdout.strip()}")
            else:
                logger.error("Node.js未正确安装或不在PATH中")
        except FileNotFoundError:
            logger.error("未找到Node.js可执行文件")

    raise RuntimeError(f"无法加载JavaScript文件: {error_msg}")

def trans_cookies(cookies_str: str) -> dict:
    """将cookies字符串转换为字典"""
    if not cookies_str:
        raise ValueError("cookies不能为空")

    cookies = {}
    for cookie in cookies_str.split("; "):
        if "=" in cookie:
            key, value = cookie.split("=", 1)
            cookies[key] = value
    return cookies


def generate_mid() -> str:
    """生成mid"""
    import random
    random_part = int(1000 * random.random())
    timestamp = int(time.time() * 1000)
    return f"{random_part}{timestamp} 0"


def generate_uuid() -> str:
    """生成uuid"""
    timestamp = int(time.time() * 1000)
    return f"-{timestamp}1"


def generate_device_id(user_id: str) -> str:
    """生成设备ID"""
    import random

    # 字符集
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    result = []

    for i in range(36):
        if i in [8, 13, 18, 23]:
            result.append("-")
        elif i == 14:
            result.append("4")
        else:
            if i == 19:
                # 对于位置19，需要特殊处理
                rand_val = int(16 * random.random())
                result.append(chars[(rand_val & 0x3) | 0x8])
            else:
                rand_val = int(16 * random.random())
                result.append(chars[rand_val])

    return ''.join(result) + "-" + user_id


def generate_sign(t: str, token: str, data: str) -> str:
    """生成签名"""
    app_key = "34839810"
    msg = f"{token}&{t}&{app_key}&{data}"

    # 使用MD5生成签名
    md5_hash = hashlib.md5()
    md5_hash.update(msg.encode('utf-8'))
    return md5_hash.hexdigest()


class MessagePackDecoder:
    """MessagePack解码器的纯Python实现"""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.length = len(data)

    def read_byte(self) -> int:
        if self.pos >= self.length:
            raise ValueError("Unexpected end of data")
        byte = self.data[self.pos]
        self.pos += 1
        return byte

    def read_bytes(self, count: int) -> bytes:
        if self.pos + count > self.length:
            raise ValueError("Unexpected end of data")
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        return result

    def read_uint8(self) -> int:
        return self.read_byte()

    def read_uint16(self) -> int:
        return struct.unpack('>H', self.read_bytes(2))[0]

    def read_uint32(self) -> int:
        return struct.unpack('>I', self.read_bytes(4))[0]

    def read_uint64(self) -> int:
        return struct.unpack('>Q', self.read_bytes(8))[0]

    def read_int8(self) -> int:
        return struct.unpack('>b', self.read_bytes(1))[0]

    def read_int16(self) -> int:
        return struct.unpack('>h', self.read_bytes(2))[0]

    def read_int32(self) -> int:
        return struct.unpack('>i', self.read_bytes(4))[0]

    def read_int64(self) -> int:
        return struct.unpack('>q', self.read_bytes(8))[0]

    def read_float32(self) -> float:
        return struct.unpack('>f', self.read_bytes(4))[0]

    def read_float64(self) -> float:
        return struct.unpack('>d', self.read_bytes(8))[0]

    def read_string(self, length: int) -> str:
        return self.read_bytes(length).decode('utf-8')

    def decode_value(self) -> Any:
        """解码单个MessagePack值"""
        if self.pos >= self.length:
            raise ValueError("Unexpected end of data")

        format_byte = self.read_byte()

        # Positive fixint (0xxxxxxx)
        if format_byte <= 0x7f:
            return format_byte

        # Fixmap (1000xxxx)
        elif 0x80 <= format_byte <= 0x8f:
            size = format_byte & 0x0f
            return self.decode_map(size)

        # Fixarray (1001xxxx)
        elif 0x90 <= format_byte <= 0x9f:
            size = format_byte & 0x0f
            return self.decode_array(size)

        # Fixstr (101xxxxx)
        elif 0xa0 <= format_byte <= 0xbf:
            size = format_byte & 0x1f
            return self.read_string(size)

        # nil
        elif format_byte == 0xc0:
            return None

        # false
        elif format_byte == 0xc2:
            return False

        # true
        elif format_byte == 0xc3:
            return True

        # bin 8
        elif format_byte == 0xc4:
            size = self.read_uint8()
            return self.read_bytes(size)

        # bin 16
        elif format_byte == 0xc5:
            size = self.read_uint16()
            return self.read_bytes(size)

        # bin 32
        elif format_byte == 0xc6:
            size = self.read_uint32()
            return self.read_bytes(size)

        # float 32
        elif format_byte == 0xca:
            return self.read_float32()

        # float 64
        elif format_byte == 0xcb:
            return self.read_float64()

        # uint 8
        elif format_byte == 0xcc:
            return self.read_uint8()

        # uint 16
        elif format_byte == 0xcd:
            return self.read_uint16()

        # uint 32
        elif format_byte == 0xce:
            return self.read_uint32()

        # uint 64
        elif format_byte == 0xcf:
            return self.read_uint64()

        # int 8
        elif format_byte == 0xd0:
            return self.read_int8()

        # int 16
        elif format_byte == 0xd1:
            return self.read_int16()

        # int 32
        elif format_byte == 0xd2:
            return self.read_int32()

        # int 64
        elif format_byte == 0xd3:
            return self.read_int64()

        # str 8
        elif format_byte == 0xd9:
            size = self.read_uint8()
            return self.read_string(size)

        # str 16
        elif format_byte == 0xda:
            size = self.read_uint16()
            return self.read_string(size)

        # str 32
        elif format_byte == 0xdb:
            size = self.read_uint32()
            return self.read_string(size)

        # array 16
        elif format_byte == 0xdc:
            size = self.read_uint16()
            return self.decode_array(size)

        # array 32
        elif format_byte == 0xdd:
            size = self.read_uint32()
            return self.decode_array(size)

        # map 16
        elif format_byte == 0xde:
            size = self.read_uint16()
            return self.decode_map(size)

        # map 32
        elif format_byte == 0xdf:
            size = self.read_uint32()
            return self.decode_map(size)

        # Negative fixint (111xxxxx)
        elif format_byte >= 0xe0:
            return format_byte - 0x100

        raise ValueError(f"Unknown format byte: {format_byte:02x}")

    def decode_array(self, size: int) -> List[Any]:
        """解码数组"""
        return [self.decode_value() for _ in range(size)]

    def decode_map(self, size: int) -> Dict[Any, Any]:
        """解码字典"""
        result = {}
        for _ in range(size):
            key = self.decode_value()
            value = self.decode_value()
            result[key] = value
        return result

    def decode(self) -> Any:
        """解码整个MessagePack数据"""
        return self.decode_value()


def decrypt(data: str) -> str:
    """解密消息数据"""
    import json as json_module  # 使用别名避免作用域冲突

    try:
        # 确保输入数据是字符串类型
        if not isinstance(data, str):
            data = str(data)

        # 清理数据，移除可能的非ASCII字符
        try:
            # 尝试编码为ASCII，如果失败则使用UTF-8编码后再解码
            data.encode('ascii')
        except UnicodeEncodeError:
            # 如果包含非ASCII字符，先编码为UTF-8字节，再解码为ASCII兼容的字符串
            data = data.encode('utf-8', errors='ignore').decode('ascii', errors='ignore')

        # Base64解码
        try:
            decoded_data = base64.b64decode(data)
        except Exception as decode_error:
            # 如果base64解码失败，尝试添加填充
            missing_padding = len(data) % 4
            if missing_padding:
                data += '=' * (4 - missing_padding)
            decoded_data = base64.b64decode(data)

        # 使用MessagePack解码器解码数据
        decoder = MessagePackDecoder(decoded_data)
        decoded_value = decoder.decode()

        # 如果解码后的值是字典，转换为JSON字符串
        if isinstance(decoded_value, dict):
            def json_serializer(obj):
                if isinstance(obj, bytes):
                    return obj.decode('utf-8', errors='ignore')
                raise TypeError(f"Type {type(obj)} not serializable")

            return json_module.dumps(decoded_value, default=json_serializer, ensure_ascii=False)

        # 如果是其他类型，尝试转换为字符串
        return str(decoded_value)

    except Exception as e:
        raise Exception(f"解密失败: {str(e)}")
