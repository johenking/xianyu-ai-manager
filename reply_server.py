from fastapi import FastAPI, HTTPException, Depends, status, UploadFile, File, Form, Body, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from typing import List, Tuple, Optional, Dict, Any, Literal
from pathlib import Path
from urllib.parse import quote, unquote
import hashlib
import secrets
import time
import json
import os
import re
import uvicorn
import pandas as pd
import io
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
import requests

import cookie_manager
from db_manager import db_manager
from file_log_collector import setup_file_logging, get_file_log_collector
from ai_reply_engine import ai_reply_engine
from ai_provider_service import (
    PROVIDER_PRESETS,
    discover_provider_models,
    provider_test_tokens,
    test_provider_reply,
)
from settings_service import (
    SETTINGS_SECTION_KEYS,
    apply_secret_action,
    normalize_system_settings,
    resolve_user_basic_settings,
    validate_skill_monitor_features,
)
from account_session_refresh import (
    active_refresh_registry,
    is_runtime_event_active,
    is_valid_account_login_username,
    remove_verification_image,
)
from utils.qr_login import qr_login_manager
from utils.xianyu_utils import trans_cookies
from utils.image_utils import image_manager
from order_sync_service import OrderSyncCoordinator, XianyuOrderListClient, normalize_order_status
from api_routers import (
    accounts_router,
    admin_router,
    ai_router,
    auth_router,
    content_router,
    frontend_router,
    include_domain_routers,
    orders_router,
    settings_router,
    skills_router,
    system_router,
)
from session_registry import get_session_registry
from auth_registration_service import (
    RegistrationError,
    mask_email_for_log,
    normalize_email,
    resolve_client_ip,
)
from auth_email_service import (
    SMTP_CONFIGURATION_KEYS,
    SMTPConfigurationError,
    SMTPDeliveryError,
    SMTPEmailSender,
    registration_readiness,
    smtp_configuration_fingerprint,
    smtp_configuration_status,
)

from loguru import logger

# 刮刮乐远程控制路由
try:
    from api_captcha_remote import router as captcha_router
    CAPTCHA_ROUTER_AVAILABLE = True
except ImportError:
    logger.warning("⚠️ api_captcha_remote 未找到，刮刮乐远程控制功能不可用")
    CAPTCHA_ROUTER_AVAILABLE = False

# 关键字文件路径
KEYWORDS_FILE = Path(__file__).parent / "回复关键字.txt"

# 简单的用户认证配置
ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"  # 系统初始化时的默认密码
SESSION_TOKENS = {}  # 存储会话token: {token: {'user_id': int, 'username': str, 'timestamp': float, 'expires_at': float}}
TOKEN_EXPIRE_TIME = 30 * 24 * 60 * 60  # token过期时间：30天

# HTTP Bearer认证
security = HTTPBearer(auto_error=False)

# 扫码登录检查锁 - 防止并发处理同一个session
qr_check_locks = defaultdict(lambda: asyncio.Lock())
qr_check_processed = {}  # 记录已处理的session: {session_id: {'processed': bool, 'timestamp': float}}

# 账号密码登录会话管理。明文密码只存在于后台任务参数中，不写入会话表。
password_login_sessions = {}
password_login_locks = defaultdict(lambda: asyncio.Lock())
ai_reply_lab_sessions = {}  # {session_id: {'cookie_id': str, 'user_id': int, 'history': list, 'timestamp': float}}

# 不再需要单独的密码初始化，由数据库初始化时处理


def cleanup_qr_check_records():
    """清理过期的扫码检查记录"""
    current_time = time.time()
    expired_sessions = []

    for session_id, record in qr_check_processed.items():
        # 清理超过1小时的记录
        if current_time - record['timestamp'] > 3600:
            expired_sessions.append(session_id)

    for session_id in expired_sessions:
        if session_id in qr_check_processed:
            del qr_check_processed[session_id]
        if session_id in qr_check_locks:
            del qr_check_locks[session_id]


def load_keywords() -> List[Tuple[str, str]]:
    """读取关键字→回复映射表

    文件格式支持：
        关键字<空格/制表符/冒号>回复内容
    忽略空行和以 # 开头的注释行
    """
    mapping: List[Tuple[str, str]] = []
    if not KEYWORDS_FILE.exists():
        return mapping

    with KEYWORDS_FILE.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # 尝试用\t、空格、冒号分隔
            if '\t' in line:
                key, reply = line.split('\t', 1)
            elif ' ' in line:
                key, reply = line.split(' ', 1)
            elif ':' in line:
                key, reply = line.split(':', 1)
            else:
                # 无法解析的行，跳过
                continue
            mapping.append((key.strip(), reply.strip()))
    return mapping


KEYWORDS_MAPPING = load_keywords()


# 认证相关模型
class LoginRequest(BaseModel):
    identifier: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    email: Optional[str] = None
    verification_code: Optional[str] = None


class LoginResponse(BaseModel):
    success: bool
    token: Optional[str] = None
    message: str
    user_id: Optional[int] = None
    username: Optional[str] = None
    is_admin: Optional[bool] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class OrderSyncRequest(BaseModel):
    cookie_id: Optional[str] = None
    days: int = Field(90, ge=1, le=365)


class RegisterRequest(BaseModel):
    invite_code: str = ""
    username: str
    email: str
    password: str
    challenge_id: str
    verification_code: str
    terms_version: str
    terms_accepted: bool


class RegisterResponse(BaseModel):
    success: bool
    message: str
    token: Optional[str] = None
    user_id: Optional[int] = None
    username: Optional[str] = None
    is_admin: Optional[bool] = None


class SendCodeRequest(BaseModel):
    email: str
    session_id: Optional[str] = None
    type: Optional[str] = 'register'  # 'register' 或 'login'


class SendCodeResponse(BaseModel):
    success: bool
    message: str


class EmailCodeRequest(BaseModel):
    purpose: Literal["register", "password_reset"]
    email: str
    invite_code: str = ""
    captcha_challenge_id: str
    captcha_code: str


class PasswordResetRequest(BaseModel):
    email: str
    challenge_id: str
    verification_code: str
    new_password: str


class UserActiveUpdate(BaseModel):
    is_active: bool


class RegistrationSettingUpdate(BaseModel):
    enabled: bool


class RegistrationLimitUpdate(BaseModel):
    limit: int


class SMTPVerificationConfirmRequest(BaseModel):
    challenge_id: str
    verification_code: str


class CaptchaRequest(BaseModel):
    session_id: str


class CaptchaResponse(BaseModel):
    success: bool
    captcha_image: str
    session_id: str
    message: str


class VerifyCaptchaRequest(BaseModel):
    session_id: str
    captcha_code: str


class VerifyCaptchaResponse(BaseModel):
    success: bool
    message: str


def generate_token() -> str:
    """生成随机token"""
    return secrets.token_urlsafe(32)


def create_login_session(user: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """创建并持久化后台登录会话"""
    token = generate_token()
    is_admin = user.get('is_admin', False) or user['username'] == ADMIN_USERNAME
    expires_at = time.time() + TOKEN_EXPIRE_TIME
    token_data = {
        'user_id': user['id'],
        'username': user['username'],
        'is_admin': is_admin,
        'timestamp': time.time(),
        'expires_at': expires_at
    }

    SESSION_TOKENS[token] = token_data
    db_manager.save_auth_session(
        token=token,
        user_id=user['id'],
        username=user['username'],
        is_admin=is_admin,
        expires_at=expires_at
    )
    db_manager.cleanup_expired_auth_sessions()
    return token, token_data


def _drop_user_sessions_from_memory(user_id: int) -> None:
    for token, data in list(SESSION_TOKENS.items()):
        if int(data.get('user_id') or 0) == int(user_id):
            SESSION_TOKENS.pop(token, None)


def _masked_identifier(identifier: str) -> str:
    value = str(identifier or '').strip()
    if '@' in value:
        return mask_email_for_log(value)
    return f"{value[:2]}***" if value else "[空账号]"


def _client_ip(request: Request) -> str:
    peer_ip = request.client.host if request.client else "0.0.0.0"
    trusted = db_manager.get_system_setting('auth_trusted_proxies') or ''
    return resolve_client_ip(peer_ip, request.headers, trusted)


def _registration_state() -> Dict[str, Any]:
    settings = db_manager.get_all_system_settings()
    capacity = db_manager.registration_service.registration_capacity()
    return registration_readiness(
        settings,
        db_path=db_manager.db_path,
        user_count=capacity['user_count'],
    )


def _require_registration_enabled() -> Dict[str, Any]:
    try:
        state = _registration_state()
    except Exception as exc:
        raise RegistrationError(
            "REGISTRATION_UNAVAILABLE",
            "注册服务暂不可用",
            http_status=503,
        ) from exc
    if not state['enabled']:
        raise RegistrationError(
            "REGISTRATION_CLOSED",
            "注册暂未开放",
            http_status=403,
        )
    return state


def _require_verified_smtp(settings: Dict[str, Any]) -> None:
    status = smtp_configuration_status(settings, db_path=db_manager.db_path)
    if not status['smtp_verified']:
        raise RegistrationError(
            "SMTP_NOT_READY",
            "邮件服务暂不可用",
            http_status=503,
        )


def verify_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Optional[Dict[str, Any]]:
    """验证token并返回用户信息"""
    if not credentials:
        return None

    token = credentials.credentials
    token_data = SESSION_TOKENS.get(token)
    if not token_data:
        token_data = db_manager.get_auth_session(token)
        if not token_data:
            return None
        SESSION_TOKENS[token] = token_data

    # 检查token是否过期
    expires_at = token_data.get('expires_at', token_data.get('timestamp', 0) + TOKEN_EXPIRE_TIME)
    if time.time() > expires_at:
        SESSION_TOKENS.pop(token, None)
        db_manager.delete_auth_session(token)
        return None

    current_user = db_manager.get_user_by_id(token_data.get('user_id'))
    if not current_user or not current_user.get('is_active'):
        SESSION_TOKENS.pop(token, None)
        db_manager.delete_auth_session(token)
        return None

    return token_data


def verify_admin_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Dict[str, Any]:
    """验证管理员token"""
    user_info = verify_token(credentials)
    if not user_info:
        raise HTTPException(status_code=401, detail="未授权访问")

    # 检查是否是管理员
    if user_info['username'] != ADMIN_USERNAME:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    return user_info


def require_auth(user_info: Optional[Dict[str, Any]] = Depends(verify_token)):
    """需要认证的依赖，返回用户信息"""
    if not user_info:
        raise HTTPException(status_code=401, detail="未授权访问")
    return user_info


def get_current_user(user_info: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    """获取当前登录用户信息"""
    return user_info


def get_current_user_optional(user_info: Optional[Dict[str, Any]] = Depends(verify_token)) -> Optional[Dict[str, Any]]:
    """获取当前用户信息（可选，不强制要求登录）"""
    return user_info


def get_user_log_prefix(user_info: Dict[str, Any] = None) -> str:
    """获取用户日志前缀"""
    if user_info:
        return f"【{user_info['username']}#{user_info['user_id']}】"
    return "【系统】"


def require_admin(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """要求管理员权限"""
    if current_user['username'] != 'admin':
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_user


def log_with_user(level: str, message: str, user_info: Dict[str, Any] = None):
    """带用户信息的日志记录"""
    prefix = get_user_log_prefix(user_info)
    full_message = f"{prefix} {message}"

    if level.lower() == 'info':
        logger.info(full_message)
    elif level.lower() == 'error':
        logger.error(full_message)
    elif level.lower() == 'warning':
        logger.warning(full_message)
    elif level.lower() == 'debug':
        logger.debug(full_message)
    else:
        logger.info(full_message)


def match_reply(cookie_id: str, message: str) -> Optional[str]:
    """根据 cookie_id 及消息内容匹配回复
    只有启用的账号才会匹配关键字回复
    """
    mgr = cookie_manager.manager
    if mgr is None:
        return None

    # 检查账号是否启用
    if not mgr.get_cookie_status(cookie_id):
        return None  # 禁用的账号不参与自动回复

    # 优先账号级关键字
    if mgr.get_keywords(cookie_id):
        for k, r in mgr.get_keywords(cookie_id):
            if k in message:
                return r

    # 全局关键字
    for k, r in KEYWORDS_MAPPING:
        if k in message:
            return r
    return None


class RequestModel(BaseModel):
    cookie_id: str
    msg_time: str
    user_url: str
    send_user_id: str
    send_user_name: str
    item_id: str
    send_message: str
    chat_id: str


class ResponseData(BaseModel):
    send_msg: str


class ResponseModel(BaseModel):
    code: int
    data: ResponseData


app = FastAPI(
    title="Xianyu Auto Reply API",
    version="1.4.0",
    description="闲鱼自动回复系统API",
    docs_url="/docs",
    redoc_url="/redoc"
)

# 添加 CORS 中间件支持前端跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3002",
    ],  # 允许的前端开发服务器地址
    allow_credentials=True,  # 允许携带凭证
    allow_methods=["*"],  # 允许所有HTTP方法
    allow_headers=["*"],  # 允许所有请求头
)

# 注册刮刮乐远程控制路由
if CAPTCHA_ROUTER_AVAILABLE:
    app.include_router(captcha_router)
    logger.info("✅ 已注册刮刮乐远程控制路由: /api/captcha")
else:
    logger.warning("⚠️ 刮刮乐远程控制路由未注册")

# 初始化文件日志收集器
setup_file_logging()

# 添加一条测试日志
from loguru import logger
logger.info("Web服务器启动，文件日志收集器已初始化")

# 添加请求日志中间件
@app.middleware("http")
async def log_requests(request, call_next):
    start_time = time.time()
    supplied_request_id = request.headers.get("X-Request-ID", "")
    request_id = supplied_request_id if re.fullmatch(r"[A-Za-z0-9._-]{8,80}", supplied_request_id) else secrets.token_hex(8)
    request.state.request_id = request_id

    logger.info(f"🌐 API请求: {request.method} {request.url.path} request_id={request_id}")

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id

    process_time = time.time() - start_time
    logger.info(f"✅ API响应: {request.method} {request.url.path} - {response.status_code} ({process_time:.3f}s)")

    return response


@app.exception_handler(RegistrationError)
async def registration_error_with_request_id(request: Request, exc: RegistrationError):
    headers = {}
    if exc.retry_after is not None:
        headers['Retry-After'] = str(exc.retry_after)
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "success": False,
            "code": exc.code,
            "message": exc.message,
            "retry_after": exc.retry_after,
            "request_id": getattr(request.state, "request_id", ""),
        },
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_without_input(request: Request, exc: RequestValidationError):
    errors = [
        {
            "location": [str(part) for part in error.get("loc", ())],
            "message": error.get("msg", "输入无效"),
            "type": error.get("type", "validation_error"),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "code": "REQUEST_VALIDATION_FAILED",
            "message": "请求参数无效",
            "errors": errors,
            "request_id": getattr(request.state, "request_id", ""),
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_with_request_id(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "request_id": getattr(request.state, "request_id", "")},
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_with_request_id(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "")
    logger.exception(f"未处理请求异常 request_id={request_id}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "request_id": request_id},
    )

# 提供前端静态文件
import os
static_dir = os.path.join(os.path.dirname(__file__), 'static')
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)

# 挂载静态文件目录
app.mount('/static', StaticFiles(directory=static_dir), name='static')

# 挂载 /assets 路径，指向 static/assets 目录
# 这样访问 /assets/xxx.js 时会正确映射到 static_dir/assets/xxx.js
assets_dir = os.path.join(static_dir, 'assets')
os.makedirs(assets_dir, exist_ok=True)
app.mount('/assets', StaticFiles(directory=assets_dir), name='assets')

# 确保图片上传目录存在
uploads_dir = os.path.join(static_dir, 'uploads', 'images')
if not os.path.exists(uploads_dir):
    os.makedirs(uploads_dir, exist_ok=True)
    logger.info(f"创建图片上传目录: {uploads_dir}")

# 健康检查端点
@system_router.get('/health')
async def health_check():
    """健康检查端点，用于Docker健康检查和负载均衡器"""
    try:
        # 检查Cookie管理器状态
        manager_status = "ok" if cookie_manager.manager is not None else "error"

        # 检查数据库连接
        from db_manager import db_manager
        try:
            db_manager.get_all_cookies()
            db_status = "ok"
        except Exception:
            db_status = "error"

        # 获取系统状态
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1)
        memory_info = psutil.virtual_memory()

        status = {
            "status": "healthy" if manager_status == "ok" and db_status == "ok" else "unhealthy",
            "timestamp": time.time(),
            "services": {
                "cookie_manager": manager_status,
                "database": db_status
            },
            "system": {
                "cpu_percent": cpu_percent,
                "memory_percent": memory_info.percent,
                "memory_available": memory_info.available
            },
            "migration_version": getattr(db_manager, "schema_version", "legacy"),
            "runtime_sessions": get_session_registry().summary(),
        }
        if status["status"] == "unhealthy":
            raise HTTPException(status_code=503, detail=status)

        return status

    except Exception as e:
        return {
            "status": "unhealthy",
            "timestamp": time.time(),
            "error": str(e)
        }


@system_router.get('/health/live')
async def health_live():
    return {"status": "alive", "timestamp": time.time()}


@system_router.get('/health/ready')
async def health_ready():
    try:
        db_manager.conn.execute("SELECT 1").fetchone()
        database_ready = True
    except Exception:
        database_ready = False
    manager_ready = cookie_manager.manager is not None
    payload = {
        "status": "ready" if database_ready and manager_ready else "not_ready",
        "timestamp": time.time(),
        "services": {
            "database": "ok" if database_ready else "error",
            "cookie_manager": "ok" if manager_ready else "error",
        },
        "migration_version": getattr(db_manager, "schema_version", "legacy"),
        "runtime_sessions": get_session_registry().summary(),
    }
    return JSONResponse(status_code=200 if payload["status"] == "ready" else 503, content=payload)


# 服务 React 前端 SPA - 所有前端路由都返回 index.html
async def serve_frontend():
    """服务 React 前端 SPA"""
    index_path = os.path.join(static_dir, 'index.html')
    if os.path.exists(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            return HTMLResponse(f.read())
    else:
        return HTMLResponse('<h3>Frontend not found. Please build the frontend first.</h3>')

@frontend_router.get('/', response_class=HTMLResponse)
async def root():
    return await serve_frontend()


# 登录页面路由 - 重定向到 React 前端
@frontend_router.get('/login.html', response_class=HTMLResponse)
async def login_page():
    return await serve_frontend()

@frontend_router.get('/login', response_class=HTMLResponse)
async def login_route():
    return await serve_frontend()


# 注册页面路由
@frontend_router.get('/register.html', response_class=HTMLResponse)
async def register_page():
    return await serve_frontend()

@frontend_router.get('/register', response_class=HTMLResponse)
async def register_route():
    return await serve_frontend()


# 注意：不要在这里定义 /admin 或 /admin/{path} 路由
# 因为后端有 /admin/users, /admin/logs 等 API 路由
# 前端 SPA 通过根路由 / 加载，由 React Router 处理客户端路由
# 文件末尾的 catch-all 路由会处理前端页面的直接访问



# 登录接口
@auth_router.post('/login')
async def login(request: LoginRequest, http_request: Request):
    identifier = str(
        request.identifier or request.username or request.email or ''
    ).strip()
    if not identifier or not request.password:
        raise RegistrationError(
            "LOGIN_INPUT_REQUIRED",
            "请输入用户名或邮箱及密码",
        )

    client_ip = _client_ip(http_request)
    db_manager.auth_rate_limiter.check_login_limit(
        ip=client_ip,
        account=identifier,
    )
    verified = db_manager.verify_user_password(identifier, request.password)
    if not verified:
        logger.warning(f"登录失败 account={_masked_identifier(identifier)}")
        db_manager.auth_rate_limiter.record_login_result(
            ip=client_ip,
            account=identifier,
            success=False,
        )
        raise RegistrationError(
            "INVALID_CREDENTIALS",
            "账号或密码错误",
            http_status=401,
        )

    user = db_manager.user_repository.get_by_identifier(identifier)
    if not user or not user.get('is_active'):
        db_manager.auth_rate_limiter.record_login_result(
            ip=client_ip,
            account=identifier,
            success=False,
        )
        raise RegistrationError(
            "INVALID_CREDENTIALS",
            "账号或密码错误",
            http_status=401,
        )

    db_manager.auth_rate_limiter.record_login_result(
        ip=client_ip,
        account=identifier,
        success=True,
    )
    token, _ = create_login_session(user)
    logger.info(f"用户登录成功 user_id={user['id']}")
    return LoginResponse(
        success=True,
        token=token,
        message="登录成功",
        user_id=user['id'],
        username=user['username'],
        is_admin=(user['username'] == ADMIN_USERNAME),
    )


# 验证token接口
@auth_router.get('/verify')
async def verify(user_info: Optional[Dict[str, Any]] = Depends(verify_token)):
    if user_info:
        return {
            "authenticated": True,
            "user_id": user_info['user_id'],
            "username": user_info['username'],
            "is_admin": user_info['username'] == ADMIN_USERNAME
        }
    return {"authenticated": False}


# 登出接口
@auth_router.post('/logout')
async def logout(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    if credentials:
        SESSION_TOKENS.pop(credentials.credentials, None)
        db_manager.delete_auth_session(credentials.credentials)
    return {"message": "已登出"}


# 修改管理员密码接口
@auth_router.post('/change-admin-password')
async def change_admin_password(request: ChangePasswordRequest, admin_user: Dict[str, Any] = Depends(verify_admin_token)):
    from db_manager import db_manager

    try:
        # 验证当前密码（使用用户表验证）
        if not db_manager.verify_user_password('admin', request.current_password):
            return {"success": False, "message": "当前密码错误"}

        # 更新密码（使用用户表更新）
        success = db_manager.update_user_password('admin', request.new_password)

        if success:
            logger.info(f"【admin#{admin_user['user_id']}】管理员密码修改成功")
            return {"success": True, "message": "密码修改成功"}
        else:
            return {"success": False, "message": "密码修改失败"}

    except Exception as e:
        logger.error(f"修改管理员密码异常: {e}")
        return {"success": False, "message": "系统错误"}


# 普通用户修改密码接口
@auth_router.post('/change-password')
async def change_user_password(request: ChangePasswordRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    from db_manager import db_manager

    try:
        username = current_user.get('username')
        user_id = current_user.get('user_id')

        if not username:
            return {"success": False, "message": "无法获取用户信息"}

        # 验证当前密码
        if not db_manager.verify_user_password(username, request.current_password):
            return {"success": False, "message": "当前密码错误"}

        # 更新密码
        success = db_manager.update_user_password(username, request.new_password)

        if success:
            logger.info(f"【{username}#{user_id}】用户密码修改成功")
            return {"success": True, "message": "密码修改成功"}
        else:
            return {"success": False, "message": "密码修改失败"}

    except Exception as e:
        logger.error(f"修改用户密码异常: {e}")
        return {"success": False, "message": "系统错误"}


# 检查是否使用默认密码
@auth_router.get('/api/check-default-password')
async def check_default_password(current_user: Dict[str, Any] = Depends(get_current_user)):
    from db_manager import db_manager

    try:
        username = current_user.get('username')
        is_admin = current_user.get('is_admin', False)

        logger.info(f"检查默认密码: username={username}, is_admin={is_admin}")

        # 只检查admin用户
        if not is_admin or username != 'admin':
            logger.info(f"非admin用户，跳过检查")
            return {"using_default": False}

        # 检查是否使用默认密码
        using_default = db_manager.verify_user_password('admin', DEFAULT_ADMIN_PASSWORD)
        logger.info(f"默认密码检查结果: {using_default}, DEFAULT_ADMIN_PASSWORD={DEFAULT_ADMIN_PASSWORD}")

        return {"using_default": using_default}

    except Exception as e:
        logger.error(f"检查默认密码异常: {e}")
        return {"using_default": False}


# 生成图形验证码接口
@auth_router.post('/generate-captcha')
async def generate_captcha(request: CaptchaRequest):
    from db_manager import db_manager

    try:
        # 生成图形验证码
        captcha_text, captcha_image = db_manager.generate_captcha()

        if not captcha_image:
            return CaptchaResponse(
                success=False,
                captcha_image="",
                session_id=request.session_id,
                message="图形验证码生成失败"
            )

        # 保存验证码到数据库
        if db_manager.save_captcha(request.session_id, captcha_text):
            return CaptchaResponse(
                success=True,
                captcha_image=captcha_image,
                session_id=request.session_id,
                message="图形验证码生成成功"
            )
        else:
            return CaptchaResponse(
                success=False,
                captcha_image="",
                session_id=request.session_id,
                message="图形验证码保存失败"
            )

    except Exception as e:
        logger.error(f"生成图形验证码失败: {e}")
        return CaptchaResponse(
            success=False,
            captcha_image="",
            session_id=request.session_id,
            message="图形验证码生成失败"
        )


# 验证图形验证码接口
@auth_router.post('/verify-captcha')
async def verify_captcha(request: VerifyCaptchaRequest):
    from db_manager import db_manager

    try:
        if db_manager.verify_captcha(request.session_id, request.captcha_code):
            return VerifyCaptchaResponse(
                success=True,
                message="图形验证码验证成功"
            )
        else:
            return VerifyCaptchaResponse(
                success=False,
                message="图形验证码错误或已过期"
            )

    except Exception as e:
        logger.error(f"验证图形验证码失败: {e}")
        return VerifyCaptchaResponse(
            success=False,
            message="图形验证码验证失败"
        )


# ==================== 极验滑动验证码 ====================

# 极验验证状态存储: {challenge: {"status": int, "expires_at": float}}
geetest_status_store: dict = {}


def cleanup_expired_geetest_status():
    """清理过期的极验验证状态"""
    current_time = time.time()
    expired_keys = [k for k, v in geetest_status_store.items() if v["expires_at"] < current_time]
    for k in expired_keys:
        del geetest_status_store[k]


def set_geetest_status(challenge: str, status: int):
    """设置极验验证状态"""
    cleanup_expired_geetest_status()
    geetest_status_store[challenge] = {
        "status": status,
        "expires_at": time.time() + 300  # 5分钟有效
    }


def get_geetest_status(challenge: str) -> int:
    """获取极验验证状态，返回0表示未验证或已过期"""
    cleanup_expired_geetest_status()
    stored = geetest_status_store.get(challenge)
    if stored and stored["expires_at"] > time.time():
        return stored["status"]
    return 0


class GeetestRegisterResponse(BaseModel):
    """极验验证码初始化响应"""
    success: bool
    code: int = 200
    message: str = ""
    data: Optional[dict] = None


class GeetestValidateRequest(BaseModel):
    """极验二次验证请求"""
    challenge: str
    validate_str: str = Field(..., alias='validate')
    seccode: str

    model_config = {'populate_by_name': True}


class GeetestValidateResponse(BaseModel):
    """极验二次验证响应"""
    success: bool
    code: int = 200
    message: str = ""


@auth_router.get('/geetest/register', response_model=GeetestRegisterResponse)
async def geetest_register():
    """
    获取极验验证码初始化参数

    前端调用此接口获取gt、challenge等参数，用于初始化验证码组件
    """
    try:
        from utils.geetest import GeetestLib

        gt_lib = GeetestLib()
        result = await gt_lib.register()

        data = result.to_dict()
        logger.info(f"极验初始化结果: status={result.status}, data={data}")

        # 记录初始状态
        challenge = data.get("challenge", "")
        if challenge:
            set_geetest_status(challenge, 0)

        return GeetestRegisterResponse(
            success=True,
            code=200,
            message="获取成功" if result.status == 1 else "宕机模式",
            data=data
        )

    except Exception as e:
        logger.error(f"极验初始化失败: {e}")
        # 返回本地初始化结果
        try:
            from utils.geetest import GeetestLib
            gt_lib = GeetestLib()
            result = gt_lib.local_init()
            data = result.to_dict()

            # 记录初始状态
            challenge = data.get("challenge", "")
            if challenge:
                set_geetest_status(challenge, 0)

            return GeetestRegisterResponse(
                success=True,
                code=200,
                message="本地初始化",
                data=data
            )
        except Exception as e2:
            logger.error(f"极验本地初始化也失败: {e2}")
            return GeetestRegisterResponse(
                success=False,
                code=500,
                message="验证码服务异常"
            )


@auth_router.post('/geetest/validate', response_model=GeetestValidateResponse)
async def geetest_validate(request: GeetestValidateRequest):
    """
    极验二次验证

    用户完成滑动验证后，前端调用此接口进行二次验证
    """
    try:
        # 检查是否已经验证过
        if get_geetest_status(request.challenge) == 1:
            return GeetestValidateResponse(
                success=True,
                code=200,
                message="验证通过"
            )

        from utils.geetest import GeetestLib

        gt_lib = GeetestLib()

        # 判断是正常模式还是宕机模式
        # 通过challenge长度判断：正常模式challenge是32位MD5，宕机模式是UUID
        is_normal_mode = len(request.challenge) == 32

        if is_normal_mode:
            result = await gt_lib.success_validate(
                request.challenge,
                request.validate_str,
                request.seccode
            )
        else:
            result = gt_lib.fail_validate(
                request.challenge,
                request.validate_str,
                request.seccode
            )

        if result.status == 1:
            # 记录验证通过状态
            set_geetest_status(request.challenge, 1)

            return GeetestValidateResponse(
                success=True,
                code=200,
                message="验证通过"
            )
        else:
            return GeetestValidateResponse(
                success=False,
                code=400,
                message=result.msg or "验证失败"
            )

    except Exception as e:
        logger.error(f"极验二次验证失败: {e}")
        return GeetestValidateResponse(
            success=False,
            code=500,
            message="验证服务异常"
        )


@auth_router.get('/api/auth/registration-config')
def get_registration_config():
    try:
        settings = db_manager.get_all_system_settings()
        state = _registration_state()
        support_email = str(settings.get('support_email') or '').strip()
        if '\r' in support_email or '\n' in support_email:
            support_email = ''
        return {
            "enabled": state['enabled'],
            "ready": state['ready'],
            "invite_required": False,
            "terms_version": state['terms_version'] or 'v2',
            "terms_url": "/terms",
            "privacy_url": "/privacy",
            "support_email": support_email,
            "message": "注册已开放" if state['enabled'] else "注册暂未开放",
        }
    except Exception:
        logger.warning("读取公开注册状态失败")
        return {
            "enabled": False,
            "ready": False,
            "invite_required": False,
            "terms_version": "v2",
            "terms_url": "/terms",
            "privacy_url": "/privacy",
            "support_email": "",
            "message": "注册暂未开放",
        }


@auth_router.post('/api/auth/captcha')
def create_auth_captcha(http_request: Request):
    client_ip = _client_ip(http_request)
    db_manager.auth_rate_limiter.enforce_captcha(client_ip)
    captcha_text, captcha_image = db_manager.generate_captcha()
    if not captcha_text or not captcha_image:
        raise RegistrationError(
            "CAPTCHA_UNAVAILABLE",
            "图形验证码暂不可用",
            http_status=503,
        )
    challenge = db_manager.registration_service.create_challenge(
        purpose="captcha",
        subject=client_ip,
        secret=captcha_text.upper(),
    )
    return {
        "success": True,
        "challenge_id": challenge['challenge_id'],
        "captcha_image": captcha_image,
        "expires_in": 600,
    }


@auth_router.post('/api/auth/email-code')
async def send_auth_email_code(request: EmailCodeRequest, http_request: Request):
    email = normalize_email(request.email).normalized
    client_ip = _client_ip(http_request)
    settings = db_manager.get_all_system_settings()
    if request.purpose == 'register':
        _require_registration_enabled()
        challenge_purpose = 'register_email'
        challenge_context = ''
        subject = "闲鱼监控台注册验证码"
    else:
        _require_verified_smtp(settings)
        challenge_purpose = 'password_reset_email'
        challenge_context = ''
        subject = "闲鱼监控台密码重置验证码"

    db_manager.registration_service.consume_challenge(
        challenge_id=request.captcha_challenge_id,
        purpose="captcha",
        subject=client_ip,
        secret=request.captcha_code.upper(),
    )
    db_manager.auth_rate_limiter.enforce_email_send(client_ip, email)
    _require_verified_smtp(settings)

    verification_code = f"{secrets.randbelow(1_000_000):06d}"
    user = db_manager.get_user_by_email_for_public_auth(email)
    if request.purpose == 'register':
        actionable_target = user is None
    else:
        actionable_target = bool(user and user.get('is_active'))
    decoy_secret = secrets.token_urlsafe(32)
    challenge_secret = (
        verification_code
        if actionable_target
        else decoy_secret
    )
    text_content = (
        f"您的验证码是 {verification_code}\n\n"
        "验证码在 10 分钟内有效，最多可尝试 5 次。请勿向任何人泄露。\n"
        "如非本人操作，请忽略此邮件。"
    )
    try:
        await asyncio.to_thread(
            SMTPEmailSender().send,
            settings,
            recipient=email,
            subject=subject,
            text=text_content,
        )
    except (SMTPConfigurationError, SMTPDeliveryError) as exc:
        logger.warning(
            f"认证邮件发送失败 type={type(exc).__name__} "
            f"email={mask_email_for_log(email)}"
        )
        raise RegistrationError(
            "EMAIL_SEND_FAILED",
            "验证码邮件发送失败，请稍后重试",
            http_status=502,
        ) from exc

    challenge = db_manager.registration_service.create_challenge(
        purpose=challenge_purpose,
        subject=email,
        context=challenge_context,
        secret=challenge_secret,
    )
    logger.info(f"认证验证码请求已处理 purpose={request.purpose}")
    return {
        "success": True,
        "challenge_id": challenge['challenge_id'],
        "expires_in": 600,
        "cooldown_seconds": 60,
        "message": "验证码已发送，请查收邮件",
    }


@auth_router.post('/send-verification-code')
async def send_verification_code(_request: SendCodeRequest):
    raise RegistrationError(
        "LEGACY_AUTH_ENDPOINT_REMOVED",
        "此接口已停用，请改用 /api/auth/captcha 和 /api/auth/email-code",
        http_status=410,
    )


@auth_router.post('/register')
async def register(request: RegisterRequest, http_request: Request):
    if not request.terms_accepted:
        raise RegistrationError("TERMS_NOT_ACCEPTED", "请先同意服务条款和隐私说明")
    _require_registration_enabled()
    client_ip = _client_ip(http_request)
    db_manager.auth_rate_limiter.check_registration_limit(client_ip)
    try:
        user = db_manager.registration_service.register_user(
            username=request.username,
            email=request.email,
            password=request.password,
            challenge_id=request.challenge_id,
            verification_code=request.verification_code,
            terms_version=request.terms_version,
            invite_code=request.invite_code,
        )
    except RegistrationError:
        db_manager.auth_rate_limiter.record_registration_failure(client_ip)
        raise
    except Exception as exc:
        logger.error(f"注册事务失败 type={type(exc).__name__}")
        raise RegistrationError(
            "REGISTRATION_FAILED",
            "注册失败，请稍后重试",
            http_status=503,
        ) from exc

    token, _ = create_login_session(user)
    logger.info(f"注册成功 user_id={user['id']}")
    return RegisterResponse(
        success=True,
        token=token,
        message="注册成功",
        user_id=user['id'],
        username=user['username'],
        is_admin=False,
    )


@auth_router.post('/api/auth/password-reset')
async def reset_user_password(request: PasswordResetRequest):
    settings = db_manager.get_all_system_settings()
    _require_verified_smtp(settings)
    try:
        user_id = db_manager.registration_service.reset_password(
            email=request.email,
            new_password=request.new_password,
            challenge_id=request.challenge_id,
            verification_code=request.verification_code,
        )
    except RegistrationError:
        raise
    except Exception as exc:
        logger.error(f"密码重置失败 type={type(exc).__name__}")
        raise RegistrationError(
            "PASSWORD_RESET_FAILED",
            "密码重置失败，请稍后重试",
            http_status=503,
        ) from exc
    _drop_user_sessions_from_memory(user_id)
    logger.info(f"用户密码重置成功 user_id={user_id}")
    return {
        "success": True,
        "message": "密码已重置，请重新登录",
    }


# ------------------------- 发送消息接口 -------------------------

# 兼容旧接口的后备秘钥，仅允许通过环境变量注入。
API_SECRET_KEY = os.getenv("XIANYU_REPLY_API_SECRET", "")

class SendMessageRequest(BaseModel):
    api_key: str
    cookie_id: str
    chat_id: str
    to_user_id: str
    message: str


class SendMessageResponse(BaseModel):
    success: bool
    message: str


def verify_api_key(api_key: str) -> bool:
    """验证API秘钥"""
    try:
        # 从系统设置中获取QQ回复消息秘钥
        from db_manager import db_manager
        qq_secret_key = db_manager.get_system_setting('qq_reply_secret_key')

        # 如果系统设置中没有配置，使用默认值
        if not qq_secret_key:
            qq_secret_key = API_SECRET_KEY

        return api_key == qq_secret_key
    except Exception as e:
        logger.error(f"验证API秘钥时发生异常: {e}")
        # 异常情况下使用默认秘钥验证
        return api_key == API_SECRET_KEY


@system_router.post('/send-message', response_model=SendMessageResponse)
async def send_message_api(request: SendMessageRequest):
    """发送消息API接口（使用秘钥验证）"""
    try:
        # 清理所有参数中的换行符
        def clean_param(param_str):
            """清理参数中的换行符"""
            if isinstance(param_str, str):
                return param_str.replace('\\n', '').replace('\n', '')
            return param_str

        # 清理所有参数
        cleaned_api_key = clean_param(request.api_key)
        cleaned_cookie_id = clean_param(request.cookie_id)
        cleaned_chat_id = clean_param(request.chat_id)
        cleaned_to_user_id = clean_param(request.to_user_id)
        cleaned_message = clean_param(request.message)

        # 验证API秘钥不能为空
        if not cleaned_api_key:
            logger.warning("API秘钥为空")
            return SendMessageResponse(
                success=False,
                message="API秘钥不能为空"
            )

        # 特殊测试秘钥处理
        if cleaned_api_key == "zhinina_test_key":
            logger.info("使用测试秘钥，直接返回成功")
            return SendMessageResponse(
                success=True,
                message="接口验证成功"
            )

        # 验证API秘钥
        if not verify_api_key(cleaned_api_key):
            logger.warning("API秘钥验证失败")
            return SendMessageResponse(
                success=False,
                message="API秘钥验证失败"
            )

        # 验证必需参数不能为空
        required_params = {
            'cookie_id': cleaned_cookie_id,
            'chat_id': cleaned_chat_id,
            'to_user_id': cleaned_to_user_id,
            'message': cleaned_message
        }

        for param_name, param_value in required_params.items():
            if not param_value:
                logger.warning(f"必需参数 {param_name} 为空")
                return SendMessageResponse(
                    success=False,
                    message=f"参数 {param_name} 不能为空"
                )

        # 直接获取XianyuLive实例，跳过cookie_manager检查
        from XianyuAutoAsync import XianyuLive
        live_instance = XianyuLive.get_instance(cleaned_cookie_id)

        if not live_instance:
            logger.warning(f"账号实例不存在或未连接: {cleaned_cookie_id}")
            return SendMessageResponse(
                success=False,
                message="账号实例不存在或未连接，请检查账号状态"
            )

        # 检查WebSocket连接状态
        if not live_instance.ws or live_instance.ws.closed:
            logger.warning(f"账号WebSocket连接已断开: {cleaned_cookie_id}")
            return SendMessageResponse(
                success=False,
                message="账号WebSocket连接已断开，请等待重连"
            )

        # 发送消息（使用清理后的所有参数）
        await live_instance.send_msg(
            live_instance.ws,
            cleaned_chat_id,
            cleaned_to_user_id,
            cleaned_message
        )

        logger.info(f"API成功发送消息: {cleaned_cookie_id} -> {cleaned_to_user_id}, 内容: {cleaned_message[:50]}{'...' if len(cleaned_message) > 50 else ''}")

        return SendMessageResponse(
            success=True,
            message="消息发送成功"
        )

    except Exception as e:
        # 使用清理后的参数记录日志
        cookie_id_for_log = clean_param(request.cookie_id) if 'clean_param' in locals() else request.cookie_id
        to_user_id_for_log = clean_param(request.to_user_id) if 'clean_param' in locals() else request.to_user_id
        logger.error(f"API发送消息异常: {cookie_id_for_log} -> {to_user_id_for_log}, 错误: {str(e)}")
        return SendMessageResponse(
            success=False,
            message=f"发送消息失败: {str(e)}"
        )


@system_router.post("/xianyu/reply", response_model=ResponseModel)
async def xianyu_reply(req: RequestModel):
    msg_template = match_reply(req.cookie_id, req.send_message)
    is_default_reply = False

    if not msg_template:
        # 从数据库获取默认回复
        from db_manager import db_manager
        default_reply_settings = db_manager.get_default_reply(req.cookie_id)

        if default_reply_settings and default_reply_settings.get('enabled', False):
            # 检查是否开启了"只回复一次"功能
            if default_reply_settings.get('reply_once', False):
                # 检查是否已经回复过这个chat_id
                if db_manager.has_default_reply_record(req.cookie_id, req.chat_id):
                    raise HTTPException(status_code=404, detail="该对话已使用默认回复，不再重复回复")

            msg_template = default_reply_settings.get('reply_content', '')
            is_default_reply = True

        # 如果数据库中没有设置或为空，返回错误
        if not msg_template:
            raise HTTPException(status_code=404, detail="未找到匹配的回复规则且未设置默认回复")

    # 按占位符格式化
    try:
        send_msg = msg_template.format(
            send_user_id=req.send_user_id,
            send_user_name=req.send_user_name,
            send_message=req.send_message,
        )
    except Exception:
        # 如果格式化失败，返回原始内容
        send_msg = msg_template

    # 如果是默认回复且开启了"只回复一次"，记录回复记录
    if is_default_reply:
        from db_manager import db_manager
        default_reply_settings = db_manager.get_default_reply(req.cookie_id)
        if default_reply_settings and default_reply_settings.get('reply_once', False):
            db_manager.add_default_reply_record(req.cookie_id, req.chat_id)

    return {"code": 200, "data": {"send_msg": send_msg}}

# ------------------------- 账号 / 关键字管理接口 -------------------------


class CookieIn(BaseModel):
    id: str
    value: str


class CookieStatusIn(BaseModel):
    enabled: bool


class DefaultReplyIn(BaseModel):
    enabled: bool
    reply_content: Optional[str] = None
    reply_image_url: Optional[str] = None
    reply_once: bool = False


class NotificationChannelIn(BaseModel):
    name: str
    type: str = "qq"
    config: str


class NotificationChannelUpdate(BaseModel):
    name: str
    config: str
    enabled: bool = True


class MessageNotificationIn(BaseModel):
    channel_id: int
    enabled: bool = True


class SystemSettingIn(BaseModel):
    value: str
    description: Optional[str] = None


class SystemSettingsSectionIn(BaseModel):
    settings: Dict[str, Any] = Field(default_factory=dict)
    secret_actions: Dict[str, str] = Field(default_factory=dict)


class SystemSettingsVerifyIn(BaseModel):
    settings: Dict[str, Any] = Field(default_factory=dict)
    secret_actions: Dict[str, str] = Field(default_factory=dict)


class UserBasicSettingsIn(BaseModel):
    item_sync_enabled: Optional[bool] = None
    item_sync_interval: Optional[int] = Field(None, ge=60, le=86400)
    item_sync_max_pages: Optional[int] = Field(None, ge=1, le=50)


class SystemSettingCreateIn(BaseModel):
    key: str
    value: str
    description: Optional[str] = None





@accounts_router.get("/cookies")
def list_cookies(current_user: Dict[str, Any] = Depends(get_current_user)):
    if cookie_manager.manager is None:
        return []

    # 获取当前用户的cookies
    user_id = current_user['user_id']
    from db_manager import db_manager
    user_cookies = db_manager.get_all_cookies(user_id)
    return list(user_cookies.keys())


@accounts_router.get("/cookies/details")
def get_cookies_details(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取所有Cookie的详细信息（包括值和状态）"""
    if cookie_manager.manager is None:
        return []

    # 获取当前用户的cookies
    user_id = current_user['user_id']
    from db_manager import db_manager
    user_cookies = db_manager.get_all_cookies(user_id)

    result = []
    for cookie_id, cookie_value in user_cookies.items():
        cookie_enabled = cookie_manager.manager.get_cookie_status(cookie_id)
        auto_confirm = db_manager.get_auto_confirm(cookie_id)
        # 获取备注信息
        cookie_details = db_manager.get_cookie_details(cookie_id)
        remark = cookie_details.get('remark', '') if cookie_details else ''

        result.append({
            'id': cookie_id,
            'value': cookie_value,
            'enabled': cookie_enabled,
            'auto_confirm': auto_confirm,
            'remark': remark,
            'pause_duration': cookie_details.get('pause_duration', 10) if cookie_details else 10,
            'username': cookie_details.get('username', '') if cookie_details else '',
            'has_login_password': bool(cookie_details.get('password')) if cookie_details else False,
            'login_credentials_valid': bool(
                cookie_details
                and cookie_details.get('password')
                and is_valid_account_login_username(cookie_details.get('username'))
            ),
            'show_browser': bool(cookie_details.get('show_browser')) if cookie_details else False,
            'cookie_refresh_enabled': bool(cookie_details.get('cookie_refresh_enabled')) if cookie_details else False,
            'cookie_refresh_interval_minutes': (
                cookie_details.get('cookie_refresh_interval_minutes', 1440) if cookie_details else 1440
            ),
        })
    return result


@accounts_router.post("/cookies")
def add_cookie(item: CookieIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")
    try:
        # 添加cookie时绑定到当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager

        cookie_unb = db_manager._extract_cookie_unb(item.value)
        canonical_id = db_manager.find_cookie_id_by_unb(user_id, cookie_unb) if cookie_unb else None
        target_id = canonical_id or item.id

        log_with_user('info', f"尝试添加Cookie: {target_id}, 当前用户ID: {user_id}, 用户名: {current_user.get('username', 'unknown')}", current_user)

        # 检查cookie是否已存在且属于其他用户
        existing_cookies = db_manager.get_all_cookies()
        if target_id in existing_cookies:
            # 检查是否属于当前用户
            user_cookies = db_manager.get_all_cookies(user_id)
            if target_id not in user_cookies:
                log_with_user('warning', f"Cookie ID冲突: {target_id} 已被其他用户使用", current_user)
                raise HTTPException(status_code=400, detail="该Cookie ID已被其他用户使用")

        # 保存到数据库时指定用户ID
        db_manager.save_cookie(target_id, item.value, user_id)

        # 添加到CookieManager，同时指定用户ID
        if target_id in cookie_manager.manager.cookies:
            cookie_manager.manager.update_cookie(target_id, item.value, save_to_db=False)
        else:
            cookie_manager.manager.add_cookie(target_id, item.value, user_id=user_id)
        log_with_user('info', f"Cookie添加成功: {target_id}", current_user)
        return {"msg": "success", "account_id": target_id, "matched_existing": bool(canonical_id)}
    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"添加Cookie失败: {item.id} - {str(e)}", current_user)
        raise HTTPException(status_code=400, detail=str(e))


# ============ 带子路径的 /cookies/{cid}/xxx 路由必须在 /cookies/{cid} 之前定义 ============

class AccountLoginInfoUpdate(BaseModel):
    username: Optional[str] = None
    login_password: Optional[str] = None
    show_browser: Optional[bool] = None


class CookieRefreshSettingsUpdate(BaseModel):
    cookie_refresh_enabled: bool
    cookie_refresh_interval_minutes: int


@accounts_router.put("/cookies/{cid}/login-info")
def update_cookie_login_info(cid: str, update_data: AccountLoginInfoUpdate, current_user: Dict[str, Any] = Depends(get_current_user)):
    """更新账号登录信息（用户名、密码、是否显示浏览器）"""
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")
        if update_data.username is not None and update_data.username and not is_valid_account_login_username(update_data.username):
            raise HTTPException(status_code=400, detail="闲鱼登录账号不能填写 API 地址，请填写手机号、邮箱或闲鱼登录名")

        # 使用现有的update_cookie_account_info方法更新登录信息
        success = db_manager.update_cookie_account_info(
            cid,
            username=update_data.username,
            password=update_data.login_password if update_data.login_password else None,
            show_browser=update_data.show_browser
        )

        if success:
            return {"success": True, "message": "登录信息已更新"}
        else:
            raise HTTPException(status_code=500, detail="更新登录信息失败")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@accounts_router.put("/cookies/{cid}/cookie-refresh-settings")
def update_cookie_refresh_settings(
    cid: str,
    update_data: CookieRefreshSettingsUpdate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """更新账号定时Cookie刷新设置"""
    try:
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        success = db_manager.update_cookie_refresh_settings(
            cid,
            enabled=update_data.cookie_refresh_enabled,
            interval_minutes=update_data.cookie_refresh_interval_minutes,
        )
        if not success:
            raise HTTPException(status_code=500, detail="更新Cookie定时刷新设置失败")

        try:
            from XianyuAutoAsync import XianyuLive
            live_instance = XianyuLive.get_instance(cid)
            if live_instance:
                live_instance.configure_cookie_refresh(
                    update_data.cookie_refresh_enabled,
                    update_data.cookie_refresh_interval_minutes,
                )
        except Exception as sync_error:
            logger.warning(f"同步运行中Cookie刷新设置失败（数据库已保存）: {cid}, {sync_error}")

        return {"success": True, "message": "Cookie定时刷新设置已更新"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 通用的 /cookies/{cid} 路由 ============

@accounts_router.put('/cookies/{cid}')
def update_cookie(cid: str, item: CookieIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail='CookieManager 未就绪')
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        # 获取旧的 cookie 值，用于判断是否需要重启任务
        old_cookie_details = db_manager.get_cookie_details(cid)
        old_cookie_value = old_cookie_details.get('value') if old_cookie_details else None

        # 使用 update_cookie_account_info 更新（只更新cookie值，不覆盖其他字段）
        success = db_manager.update_cookie_account_info(cid, cookie_value=item.value)

        if not success:
            raise HTTPException(status_code=400, detail="更新Cookie失败")

        # 只有当 cookie 值真的发生变化时才重启任务
        if item.value != old_cookie_value:
            logger.info(f"Cookie值已变化，重启任务: {cid}")
            cookie_manager.manager.update_cookie(cid, item.value, save_to_db=False)
        else:
            logger.info(f"Cookie值未变化，无需重启任务: {cid}")

        return {'msg': 'updated'}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class CookieAccountInfo(BaseModel):
    """账号信息更新模型"""
    value: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    show_browser: Optional[bool] = None


@accounts_router.post("/cookie/{cid}/account-info")
def update_cookie_account_info(cid: str, info: CookieAccountInfo, current_user: Dict[str, Any] = Depends(get_current_user)):
    """更新账号信息（Cookie、用户名、密码、显示浏览器设置）"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail='CookieManager 未就绪')
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        # 获取旧的 cookie 值，用于判断是否需要重启任务
        old_cookie_details = db_manager.get_cookie_details(cid)
        old_cookie_value = old_cookie_details.get('value') if old_cookie_details else None

        # 更新数据库
        success = db_manager.update_cookie_account_info(
            cid,
            cookie_value=info.value,
            username=info.username,
            password=info.password,
            show_browser=info.show_browser
        )

        if not success:
            raise HTTPException(status_code=400, detail="更新账号信息失败")

        # 只有当 cookie 值真的发生变化时才重启任务
        if info.value is not None and info.value != old_cookie_value:
            logger.info(f"Cookie值已变化，重启任务: {cid}")
            cookie_manager.manager.update_cookie(cid, info.value, save_to_db=False)
        else:
            logger.info(f"Cookie值未变化，无需重启任务: {cid}")

        return {'msg': 'updated', 'success': True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新账号信息失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@accounts_router.get("/cookie/{cid}/details")
def get_cookie_account_details(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取账号详细信息（包括用户名、密码、显示浏览器设置）"""
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        # 获取详细信息
        details = db_manager.get_cookie_details(cid)

        if not details:
            raise HTTPException(status_code=404, detail="账号不存在")

        safe_details = dict(details)
        safe_details['has_login_password'] = bool(safe_details.pop('password', ''))
        safe_details.pop('password_encrypted', None)
        return safe_details
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取账号详情失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ========================= 账号密码登录相关接口 =========================

async def _update_cookie_manager_after_official_login(
    account_id: str,
    cookies_str: str,
    user_id: int,
    *,
    is_new_account: bool,
) -> None:
    """Apply one CookieManager mutation after the database transaction succeeds."""
    manager = cookie_manager.manager
    if manager is None:
        logger.warning(f"CookieManager 未初始化，账号 {account_id} 将在服务重启后启动监听")
        return

    if is_new_account:
        operation = manager.add_cookie(account_id, cookies_str, user_id=user_id)
    else:
        operation = manager.update_cookie(account_id, cookies_str, save_to_db=False)
    if asyncio.isfuture(operation) or asyncio.iscoroutine(operation):
        await operation


async def _execute_password_login(
    session_id: str,
    account: str,
    password: str,
    show_browser: bool,
    user_id: int,
    current_user: Dict[str, Any],
):
    """Run the official password login and persist the account by its real unb."""
    from utils.xianyu_official_login import OfficialLoginWorker, XianyuOfficialLoginService

    session = password_login_sessions.get(session_id)
    if session is None:
        return

    worker = OfficialLoginWorker()
    session['worker'] = worker
    login_key = f"{user_id}:{account.strip().lower()}"
    log_with_user('info', f"开始闲鱼官方账号密码登录任务: {session_id}", current_user)

    def on_status(result):
        active_session = password_login_sessions.get(session_id)
        if active_session is None or result.status != 'verification_required':
            return
        active_session['status'] = 'verification_required'
        active_session['screenshot_path'] = result.verification_image_path or None
        active_session['error_code'] = result.error_code
        get_session_registry().update(
            session_id,
            status='verification_required',
            error_code=result.error_code,
            error_message=result.message,
        )

    try:
        async with password_login_locks[login_key]:
            service = XianyuOfficialLoginService()
            result = await asyncio.to_thread(
                service.login_with_password,
                account=account,
                password=password,
                show_browser=show_browser,
                worker=worker,
                on_status=on_status,
            )

        if not result.succeeded:
            session['status'] = result.status if result.status in {'timeout', 'cancelled'} else 'failed'
            session['error'] = result.message or '闲鱼官方登录失败'
            session['error_code'] = result.error_code
            session['screenshot_path'] = result.verification_image_path or session.get('screenshot_path')
            return

        canonical_account_id = db_manager.find_cookie_id_by_unb(user_id, result.unb)
        account_id = canonical_account_id or result.unb
        existing_cookies = db_manager.get_all_cookies(user_id)
        is_new_account = account_id not in existing_cookies
        cookies_str = service.cookies_to_string(result.cookies)

        update_success = db_manager.update_cookie_account_info(
            account_id,
            cookie_value=cookies_str,
            username=account,
            password=password,
            show_browser=show_browser,
            user_id=user_id,
        )
        if not update_success:
            session['status'] = 'failed'
            session['error'] = '官方登录成功，但保存账号信息失败'
            session['error_code'] = 'account_save_failed'
            return

        await _update_cookie_manager_after_official_login(
            account_id,
            cookies_str,
            user_id,
            is_new_account=is_new_account,
        )
        session.update({
            'status': 'success',
            'account_id': account_id,
            'is_new_account': is_new_account,
            'cookie_count': len(result.cookies),
            'error_code': '',
        })
        log_with_user(
            'info',
            f"闲鱼官方登录成功，已按真实 unb 保存并更新监听: {account_id}",
            current_user,
        )
    except Exception as exc:
        session['status'] = 'failed'
        session['error'] = str(exc)
        session['error_code'] = 'login_exception'
        log_with_user('error', f"闲鱼官方账号密码登录异常: {exc}", current_user)
        logger.exception("闲鱼官方账号密码登录任务异常")
    finally:
        session['worker'] = None


@accounts_router.post("/password-login")
async def password_login(
    request: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """账号密码登录接口（异步，支持人脸认证）"""
    try:
        account = request.get('account')
        password = request.get('password')
        show_browser = request.get('show_browser', False)

        if not account or not password:
            return {'success': False, 'message': '登录账号和密码不能为空'}

        log_with_user('info', f"开始闲鱼官方账号密码登录: {account}", current_user)

        # 生成会话ID
        import secrets
        session_id = secrets.token_urlsafe(16)

        user_id = current_user['user_id']

        # 创建登录会话
        password_login_sessions[session_id] = {
            'account': account,
            'show_browser': show_browser,
            'status': 'processing',
            'screenshot_path': None,
            'worker': None,
            'task': None,
            'timestamp': time.time(),
            'user_id': user_id,
            'error_code': '',
        }
        get_session_registry().register(
            session_id,
            "password_login",
            user_id,
            status="processing",
            ttl_seconds=3600,
            transient=password_login_sessions[session_id],
        )

        # 启动后台登录任务
        task = asyncio.create_task(_execute_password_login(
            session_id, account, password, show_browser, user_id, current_user
        ))
        password_login_sessions[session_id]['task'] = task

        return {
            'success': True,
            'session_id': session_id,
            'status': 'processing',
            'message': '登录任务已启动，请等待...'
        }

    except Exception as e:
        log_with_user('error', f"账号密码登录异常: {str(e)}", current_user)
        import traceback
        logger.error(traceback.format_exc())
        return {'success': False, 'message': f'登录失败: {str(e)}'}


@accounts_router.get("/password-login/check/{session_id}")
async def check_password_login_status(
    session_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """检查账号密码登录状态"""
    try:
        registry = get_session_registry()
        registry.cleanup()
        # 清理过期会话（超过1小时）
        current_time = time.time()
        expired_sessions = [
            sid for sid, session in password_login_sessions.items()
            if current_time - session['timestamp'] > 3600
        ]
        for sid in expired_sessions:
            expired_session = password_login_sessions.pop(sid, None)
            worker = expired_session.get('worker') if expired_session else None
            if worker:
                worker.close_browser()

        if session_id not in password_login_sessions:
            persisted = registry.get(session_id)
            if persisted and persisted.get('owner_user_id') != current_user['user_id']:
                raise HTTPException(status_code=403, detail='无权限访问该会话')
            if persisted and persisted.get('status') == 'interrupted':
                return {'status': 'interrupted', 'message': persisted.get('error_message') or '服务已重启，请重新发起登录'}
            return {'status': 'not_found', 'message': '会话不存在或已过期'}

        session = password_login_sessions[session_id]

        # 检查用户权限
        if session['user_id'] != current_user['user_id']:
            raise HTTPException(status_code=403, detail='无权限访问该会话')

        status = session['status']
        registry.update(
            session_id,
            status=status,
            error_code=session.get('error_code', ''),
            error_message=session.get('error', '') if status in {'failed', 'timeout', 'cancelled'} else '',
        )

        if status == 'verification_required':
            screenshot_path = session.get('screenshot_path')
            return {
                'status': 'verification_required',
                'screenshot_path': screenshot_path,
                'message': '需要身份验证，请查看验证截图' if screenshot_path else '需要身份验证，请在可见浏览器中完成'
            }
        elif status == 'success':
            screenshot_path = session.get('screenshot_path')
            if screenshot_path:
                remove_verification_image(screenshot_path)

            result = {
                'status': 'success',
                'message': f'账号 {session["account_id"]} 登录成功',
                'account_id': session['account_id'],
                'is_new_account': session.get('is_new_account', False),
                'cookie_count': session.get('cookie_count', 0)
            }
            # 清理会话
            del password_login_sessions[session_id]
            return result
        elif status in {'failed', 'timeout', 'cancelled'}:
            # 删除截图（如果存在）
            screenshot_path = session.get('screenshot_path')
            if screenshot_path:
                remove_verification_image(screenshot_path)

            error_msg = session.get('error', '登录失败')
            log_with_user('info', f"返回登录终态: {session_id}, 状态: {status}, 消息: {error_msg}", current_user)
            result = {
                'status': status,
                'message': error_msg,
                'error': error_msg,
                'error_code': session.get('error_code', ''),
            }
            # 清理会话
            del password_login_sessions[session_id]
            return result
        else:
            # 处理中
            return {
                'status': 'processing',
                'message': '登录处理中，请稍候...'
            }

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"检查账号密码登录状态异常: {str(e)}", current_user)
        return {'status': 'error', 'message': str(e)}


# ========================= 人脸验证截图相关接口 =========================

@accounts_router.get("/face-verification/screenshot/{account_id}")
async def get_account_face_verification_screenshot(
    account_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """获取指定账号的人脸验证截图"""
    try:
        import glob
        from datetime import datetime

        # 检查账号是否属于当前用户
        user_id = current_user['user_id']
        username = current_user['username']

        # 如果是管理员，允许访问所有账号
        is_admin = username == 'admin'

        if not is_admin:
            cookie_info = db_manager.get_cookie_details(account_id)
            if not cookie_info:
                log_with_user('warning', f"账号 {account_id} 不存在", current_user)
                return {
                    'success': False,
                    'message': '账号不存在'
                }

            cookie_user_id = cookie_info.get('user_id')
            if cookie_user_id != user_id:
                log_with_user('warning', f"用户 {user_id} 尝试访问账号 {account_id}（归属用户: {cookie_user_id}）", current_user)
                return {
                    'success': False,
                    'message': '无权访问该账号'
                }

        # 获取该账号的验证截图
        screenshots_dir = os.path.join(static_dir, 'uploads', 'images')
        pattern = os.path.join(screenshots_dir, f'face_verify_{account_id}_*.jpg')
        screenshot_files = glob.glob(pattern)

        log_with_user('debug', f"查找截图: {pattern}, 找到 {len(screenshot_files)} 个文件", current_user)

        if not screenshot_files:
            log_with_user('warning', f"账号 {account_id} 没有找到验证截图", current_user)
            return {
                'success': False,
                'message': '未找到验证截图'
            }

        # 获取最新的截图
        latest_file = max(screenshot_files, key=os.path.getmtime)
        filename = os.path.basename(latest_file)
        stat = os.stat(latest_file)

        screenshot_info = {
            'filename': filename,
            'account_id': account_id,
            'path': f'/static/uploads/images/{filename}',
            'size': stat.st_size,
            'created_time': stat.st_ctime,
            'created_time_str': datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S')
        }

        log_with_user('info', f"获取账号 {account_id} 的验证截图", current_user)

        return {
            'success': True,
            'screenshot': screenshot_info
        }

    except Exception as e:
        log_with_user('error', f"获取验证截图失败: {str(e)}", current_user)
        return {
            'success': False,
            'message': str(e)
        }


@accounts_router.delete("/face-verification/screenshot/{account_id}")
async def delete_account_face_verification_screenshot(
    account_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """删除指定账号的人脸验证截图"""
    try:
        import glob

        # 检查账号是否属于当前用户
        user_id = current_user['user_id']
        cookie_info = db_manager.get_cookie_details(account_id)
        if not cookie_info or cookie_info.get('user_id') != user_id:
            return {
                'success': False,
                'message': '无权访问该账号'
            }

        # 删除该账号的所有验证截图
        screenshots_dir = os.path.join(static_dir, 'uploads', 'images')
        pattern = os.path.join(screenshots_dir, f'face_verify_{account_id}_*.jpg')
        screenshot_files = glob.glob(pattern)

        deleted_count = 0
        for file_path in screenshot_files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    deleted_count += 1
                    log_with_user('info', f"删除账号 {account_id} 的验证截图: {os.path.basename(file_path)}", current_user)
            except Exception as e:
                log_with_user('error', f"删除截图失败 {file_path}: {str(e)}", current_user)

        return {
            'success': True,
            'message': f'已删除 {deleted_count} 个验证截图',
            'deleted_count': deleted_count
        }

    except Exception as e:
        log_with_user('error', f"删除验证截图失败: {str(e)}", current_user)
        return {
            'success': False,
            'message': str(e)
        }


# ========================= 扫码登录相关接口 =========================

@accounts_router.post("/qr-login/generate")
async def generate_qr_code(current_user: Dict[str, Any] = Depends(get_current_user)):
    """生成扫码登录二维码"""
    try:
        log_with_user('info', "请求生成扫码登录二维码", current_user)

        result = await qr_login_manager.generate_qr_code()

        if result['success']:
            session_id = result['session_id']
            get_session_registry().register(
                session_id,
                "qr_login",
                current_user['user_id'],
                status="processing",
                ttl_seconds=900,
                transient=qr_login_manager.sessions.get(session_id),
            )
            log_with_user('info', f"扫码登录二维码生成成功: {result['session_id']}", current_user)
        else:
            log_with_user('warning', f"扫码登录二维码生成失败: {result.get('message', '未知错误')}", current_user)

        return result

    except Exception as e:
        log_with_user('error', f"生成扫码登录二维码异常: {str(e)}", current_user)
        return {'success': False, 'message': f'生成二维码失败: {str(e)}'}


@accounts_router.get("/qr-login/check/{session_id}")
async def check_qr_code_status(session_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """检查扫码登录状态"""
    try:
        registry = get_session_registry()
        registry.cleanup()
        persisted = registry.get(session_id)
        if persisted and persisted.get('owner_user_id') != current_user['user_id']:
            raise HTTPException(status_code=403, detail='无权限访问该扫码会话')
        if persisted and persisted.get('status') == 'interrupted':
            return {'status': 'interrupted', 'message': persisted.get('error_message') or '服务已重启，请重新生成二维码'}
        # 清理过期记录
        cleanup_qr_check_records()

        # 检查是否已经处理过
        if session_id in qr_check_processed:
            record = qr_check_processed[session_id]
            if record['processed']:
                log_with_user('debug', f"扫码登录session {session_id} 已处理过，直接返回", current_user)
                # 返回简单的成功状态，避免重复处理
                return {'status': 'already_processed', 'message': '该会话已处理完成'}

        # 获取该session的锁
        session_lock = qr_check_locks[session_id]

        # 使用非阻塞方式尝试获取锁
        if session_lock.locked():
            log_with_user('debug', f"扫码登录session {session_id} 正在被其他请求处理，跳过", current_user)
            return {'status': 'processing', 'message': '正在处理中，请稍候...'}

        async with session_lock:
            # 再次检查是否已处理（双重检查）
            if session_id in qr_check_processed and qr_check_processed[session_id]['processed']:
                log_with_user('debug', f"扫码登录session {session_id} 在获取锁后发现已处理，直接返回", current_user)
                return {'status': 'already_processed', 'message': '该会话已处理完成'}

            # 清理过期会话
            qr_login_manager.cleanup_expired_sessions()

            # 获取会话状态
            status_info = qr_login_manager.get_session_status(session_id)
            registry.update(
                session_id,
                status=status_info.get('status') or 'processing',
                error_code='qr_login_error' if status_info.get('status') in {'failed', 'error'} else '',
                error_message=status_info.get('message', '') if status_info.get('status') in {'failed', 'error'} else '',
            )
            safe_status_info = {
                'status': status_info.get('status'),
                'session_id': status_info.get('session_id'),
                'has_verification_screenshot': bool(status_info.get('verification_screenshot_path')),
                'verification_browser_status': status_info.get('verification_browser_status'),
                'has_cookies': bool(status_info.get('cookies')),
            }
            log_with_user('info', f"获取扫码登录会话状态: {safe_status_info}", current_user)
            if status_info['status'] == 'success':
                log_with_user('info', f"扫码登录会话成功，准备处理Cookie: {session_id}", current_user)
                # 登录成功，处理Cookie（现在包含获取真实cookie的逻辑）
                cookies_info = qr_login_manager.get_session_cookies(session_id)
                log_with_user(
                    'info',
                    f"获取扫码登录Cookie摘要: session={session_id}, "
                    f"has_cookies={bool(cookies_info and cookies_info.get('cookies'))}, "
                    f"has_unb={bool(cookies_info and cookies_info.get('unb'))}",
                    current_user
                )
                if cookies_info:
                    account_info = await process_qr_login_cookies(
                        cookies_info['cookies'],
                        cookies_info['unb'],
                        current_user
                    )
                    status_info['account_info'] = account_info

                    log_with_user('info', f"扫码登录处理完成: {session_id}, 账号: {account_info.get('account_id', 'unknown')}", current_user)

                    # 标记该session已处理
                    qr_check_processed[session_id] = {
                        'processed': True,
                        'timestamp': time.time()
                    }

            status_info.pop('cookies', None)
            status_info.pop('unb', None)
            return status_info

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"检查扫码登录状态异常: {str(e)}", current_user)
        return {'status': 'error', 'message': str(e)}


@accounts_router.post("/qr-login/continue/{session_id}")
async def continue_qr_code_after_verification(session_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """用户完成安全验证后，恢复扫码登录状态检查"""
    try:
        log_with_user('info', f"请求继续检查扫码安全验证结果: {session_id}", current_user)
        qr_login_manager.continue_after_verification(session_id)
        return await check_qr_code_status(session_id, current_user)
    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"继续检查扫码安全验证结果异常: {str(e)}", current_user)
        return {'status': 'error', 'message': str(e)}


async def process_qr_login_cookies(cookies: str, unb: str, current_user: Dict[str, Any]) -> Dict[str, Any]:
    """处理扫码登录获取的Cookie - 先获取真实cookie再保存到数据库"""
    try:
        user_id = current_user['user_id']

        # 检查是否已存在相同unb的账号
        existing_cookies = db_manager.get_all_cookies(user_id)
        existing_account_id = db_manager.find_cookie_id_by_unb(user_id, unb)

        # 确定账号ID
        if existing_account_id:
            account_id = existing_account_id
            is_new_account = False
            log_with_user('info', f"扫码登录找到现有账号: {account_id}, UNB: {unb}", current_user)
        else:
            # 创建新账号，使用unb作为账号ID
            account_id = unb

            # 确保账号ID唯一
            counter = 1
            original_account_id = account_id
            while account_id in existing_cookies:
                account_id = f"{original_account_id}_{counter}"
                counter += 1

            is_new_account = True
            log_with_user('info', f"扫码登录准备创建新账号: {account_id}, UNB: {unb}", current_user)

        # 第一步：使用扫码cookie获取真实cookie
        log_with_user('info', f"开始使用扫码cookie获取真实cookie: {account_id}", current_user)

        try:
            # 创建一个临时的XianyuLive实例来执行cookie刷新
            from XianyuAutoAsync import XianyuLive

            # 使用扫码登录的cookie创建临时实例
            temp_instance = XianyuLive(
                cookies_str=cookies,
                cookie_id=account_id,
                user_id=user_id
            )

            # 执行cookie刷新获取真实cookie
            refresh_success = await temp_instance.refresh_cookies_from_qr_login(
                qr_cookies_str=cookies,
                cookie_id=account_id,
                user_id=user_id
            )

            if refresh_success:
                log_with_user('info', f"扫码登录真实cookie获取成功: {account_id}", current_user)

                # 从数据库获取刚刚保存的真实cookie
                updated_cookie_info = db_manager.get_cookie_by_id(account_id)
                if updated_cookie_info:
                    real_cookies = updated_cookie_info['cookies_str']
                    log_with_user('info', f"已获取真实cookie，长度: {len(real_cookies)}", current_user)

                    # 第二步：将真实cookie添加到cookie_manager（如果是新账号）或更新现有账号
                    if cookie_manager.manager:
                        if is_new_account:
                            cookie_manager.manager.add_cookie(account_id, real_cookies)
                            log_with_user('info', f"已将真实cookie添加到cookie_manager: {account_id}", current_user)
                        else:
                            # refresh_cookies_from_qr_login 已经保存到数据库了，这里不需要再保存
                            cookie_manager.manager.update_cookie(account_id, real_cookies, save_to_db=False)
                            log_with_user('info', f"已更新cookie_manager中的真实cookie: {account_id}", current_user)

                    return {
                        'account_id': account_id,
                        'is_new_account': is_new_account,
                        'real_cookie_refreshed': True,
                        'cookie_length': len(real_cookies)
                    }
                else:
                    log_with_user('error', f"无法从数据库获取真实cookie: {account_id}", current_user)
                    # 降级处理：使用原始扫码cookie
                    return await _fallback_save_qr_cookie(account_id, cookies, user_id, is_new_account, current_user, "无法从数据库获取真实cookie")
            else:
                log_with_user('warning', f"扫码登录真实cookie获取失败: {account_id}", current_user)
                # 降级处理：使用原始扫码cookie
                return await _fallback_save_qr_cookie(account_id, cookies, user_id, is_new_account, current_user, "真实cookie获取失败")

        except Exception as refresh_e:
            log_with_user('error', f"扫码登录真实cookie获取异常: {str(refresh_e)}", current_user)
            # 降级处理：使用原始扫码cookie
            return await _fallback_save_qr_cookie(account_id, cookies, user_id, is_new_account, current_user, f"获取真实cookie异常: {str(refresh_e)}")

    except Exception as e:
        log_with_user('error', f"处理扫码登录Cookie失败: {str(e)}", current_user)
        raise e


async def _fallback_save_qr_cookie(account_id: str, cookies: str, user_id: int, is_new_account: bool, current_user: Dict[str, Any], error_reason: str) -> Dict[str, Any]:
    """降级处理：当无法获取真实cookie时，保存原始扫码cookie"""
    try:
        log_with_user('warning', f"降级处理 - 保存原始扫码cookie: {account_id}, 原因: {error_reason}", current_user)

        # 保存原始扫码cookie到数据库
        if is_new_account:
            db_manager.save_cookie(account_id, cookies, user_id)
            log_with_user('info', f"降级处理 - 新账号原始cookie已保存: {account_id}", current_user)
        else:
            # 现有账号使用 update_cookie_account_info 避免覆盖其他字段
            db_manager.update_cookie_account_info(account_id, cookie_value=cookies)
            log_with_user('info', f"降级处理 - 现有账号原始cookie已更新: {account_id}", current_user)

        # 添加到或更新cookie_manager
        if cookie_manager.manager:
            if is_new_account:
                cookie_manager.manager.add_cookie(account_id, cookies)
                log_with_user('info', f"降级处理 - 已将原始cookie添加到cookie_manager: {account_id}", current_user)
            else:
                # update_cookie_account_info 已经保存到数据库了，这里不需要再保存
                cookie_manager.manager.update_cookie(account_id, cookies, save_to_db=False)
                log_with_user('info', f"降级处理 - 已更新cookie_manager中的原始cookie: {account_id}", current_user)

        return {
            'account_id': account_id,
            'is_new_account': is_new_account,
            'real_cookie_refreshed': False,
            'fallback_reason': error_reason,
            'cookie_length': len(cookies)
        }

    except Exception as fallback_e:
        log_with_user('error', f"降级处理失败: {str(fallback_e)}", current_user)
        raise fallback_e


@accounts_router.post("/qr-login/refresh-cookies")
async def refresh_cookies_from_qr_login(
    request: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """使用扫码登录获取的cookie访问指定界面获取真实cookie并存入数据库"""
    try:
        qr_cookies = request.get('qr_cookies')
        cookie_id = request.get('cookie_id')

        if not qr_cookies:
            return {'success': False, 'message': '缺少扫码登录cookie'}

        if not cookie_id:
            return {'success': False, 'message': '缺少cookie_id'}

        log_with_user('info', f"开始使用扫码cookie刷新真实cookie: {cookie_id}", current_user)

        # 创建一个临时的XianyuLive实例来执行cookie刷新
        from XianyuAutoAsync import XianyuLive

        # 使用扫码登录的cookie创建临时实例
        temp_instance = XianyuLive(
            cookies_str=qr_cookies,
            cookie_id=cookie_id,
            user_id=current_user['user_id']
        )

        # 执行cookie刷新
        success = await temp_instance.refresh_cookies_from_qr_login(
            qr_cookies_str=qr_cookies,
            cookie_id=cookie_id,
            user_id=current_user['user_id']
        )

        if success:
            log_with_user('info', f"扫码cookie刷新成功: {cookie_id}", current_user)

            # 如果cookie_manager存在，更新其中的cookie
            if cookie_manager.manager:
                # 从数据库获取更新后的cookie
                updated_cookie_info = db_manager.get_cookie_by_id(cookie_id)
                if updated_cookie_info:
                    # refresh_cookies_from_qr_login 已经保存到数据库了，这里不需要再保存
                    cookie_manager.manager.update_cookie(cookie_id, updated_cookie_info['cookies_str'], save_to_db=False)
                    log_with_user('info', f"已更新cookie_manager中的cookie: {cookie_id}", current_user)

            return {
                'success': True,
                'message': '真实cookie获取并保存成功',
                'cookie_id': cookie_id
            }
        else:
            log_with_user('error', f"扫码cookie刷新失败: {cookie_id}", current_user)
            return {'success': False, 'message': '获取真实cookie失败'}

    except Exception as e:
        log_with_user('error', f"扫码cookie刷新异常: {str(e)}", current_user)
        return {'success': False, 'message': f'刷新cookie失败: {str(e)}'}


@accounts_router.post("/qr-login/reset-cooldown/{cookie_id}")
async def reset_qr_cookie_refresh_cooldown(
    cookie_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """重置指定账号的扫码登录Cookie刷新冷却时间"""
    try:
        log_with_user('info', f"重置扫码登录Cookie刷新冷却时间: {cookie_id}", current_user)

        # 检查cookie是否存在
        cookie_info = db_manager.get_cookie_by_id(cookie_id)
        if not cookie_info:
            return {'success': False, 'message': '账号不存在'}

        # 如果cookie_manager中有对应的实例，直接重置
        if cookie_manager.manager and cookie_id in cookie_manager.manager.instances:
            instance = cookie_manager.manager.instances[cookie_id]
            remaining_time_before = instance.get_qr_cookie_refresh_remaining_time()
            instance.reset_qr_cookie_refresh_flag()

            log_with_user('info', f"已重置账号 {cookie_id} 的扫码登录冷却时间，原剩余时间: {remaining_time_before}秒", current_user)

            return {
                'success': True,
                'message': '扫码登录Cookie刷新冷却时间已重置',
                'cookie_id': cookie_id,
                'previous_remaining_time': remaining_time_before
            }
        else:
            # 如果没有活跃实例，返回成功（因为没有冷却时间需要重置）
            log_with_user('info', f"账号 {cookie_id} 没有活跃实例，无需重置冷却时间", current_user)
            return {
                'success': True,
                'message': '账号没有活跃实例，无需重置冷却时间',
                'cookie_id': cookie_id
            }

    except Exception as e:
        log_with_user('error', f"重置扫码登录冷却时间异常: {str(e)}", current_user)
        return {'success': False, 'message': f'重置冷却时间失败: {str(e)}'}


@accounts_router.get("/qr-login/cooldown-status/{cookie_id}")
async def get_qr_cookie_refresh_cooldown_status(
    cookie_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """获取指定账号的扫码登录Cookie刷新冷却状态"""
    try:
        # 检查cookie是否存在
        cookie_info = db_manager.get_cookie_by_id(cookie_id)
        if not cookie_info:
            return {'success': False, 'message': '账号不存在'}

        # 如果cookie_manager中有对应的实例，获取冷却状态
        if cookie_manager.manager and cookie_id in cookie_manager.manager.instances:
            instance = cookie_manager.manager.instances[cookie_id]
            remaining_time = instance.get_qr_cookie_refresh_remaining_time()
            cooldown_duration = instance.qr_cookie_refresh_cooldown
            last_refresh_time = instance.last_qr_cookie_refresh_time

            return {
                'success': True,
                'cookie_id': cookie_id,
                'remaining_time': remaining_time,
                'cooldown_duration': cooldown_duration,
                'last_refresh_time': last_refresh_time,
                'is_in_cooldown': remaining_time > 0,
                'remaining_minutes': remaining_time // 60,
                'remaining_seconds': remaining_time % 60
            }
        else:
            return {
                'success': True,
                'cookie_id': cookie_id,
                'remaining_time': 0,
                'cooldown_duration': 600,  # 默认10分钟
                'last_refresh_time': 0,
                'is_in_cooldown': False,
                'message': '账号没有活跃实例'
            }

    except Exception as e:
        log_with_user('error', f"获取扫码登录冷却状态异常: {str(e)}", current_user)
        return {'success': False, 'message': f'获取冷却状态失败: {str(e)}'}


@accounts_router.put('/cookies/{cid}/status')
def update_cookie_status(cid: str, status_data: CookieStatusIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    """更新账号的启用/禁用状态"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail='CookieManager 未就绪')
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        cookie_manager.manager.update_cookie_status(cid, status_data.enabled)
        return {'msg': 'status updated', 'enabled': status_data.enabled}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------- 默认回复管理接口 -------------------------

@content_router.get('/default-replies/{cid}')
def get_default_reply(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取指定账号的默认回复设置"""
    from db_manager import db_manager
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限访问该Cookie")

        result = db_manager.get_default_reply(cid)
        if result is None:
            # 如果没有设置，返回默认值
            return {'enabled': False, 'reply_content': '', 'reply_once': False}
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.put('/default-replies/{cid}')
def update_default_reply(cid: str, reply_data: DefaultReplyIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    """更新指定账号的默认回复设置"""
    from db_manager import db_manager
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        db_manager.save_default_reply(cid, reply_data.enabled, reply_data.reply_content, reply_data.reply_once, reply_data.reply_image_url)
        return {'msg': 'default reply updated', 'enabled': reply_data.enabled, 'reply_once': reply_data.reply_once, 'reply_image_url': reply_data.reply_image_url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.get('/default-replies')
def get_all_default_replies(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取当前用户所有账号的默认回复设置"""
    from db_manager import db_manager
    try:
        # 只返回当前用户的默认回复设置
        user_id = current_user['user_id']
        user_cookies = db_manager.get_all_cookies(user_id)

        all_replies = db_manager.get_all_default_replies()
        # 过滤只属于当前用户的回复设置
        user_replies = {cid: reply for cid, reply in all_replies.items() if cid in user_cookies}
        return user_replies
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.delete('/default-replies/{cid}')
def delete_default_reply(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """删除指定账号的默认回复设置"""
    from db_manager import db_manager
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        success = db_manager.delete_default_reply(cid)
        if success:
            return {'msg': 'default reply deleted'}
        else:
            raise HTTPException(status_code=400, detail='删除失败')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.post('/default-replies/{cid}/clear-records')
def clear_default_reply_records(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """清空指定账号的默认回复记录"""
    from db_manager import db_manager
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        db_manager.clear_default_reply_records(cid)
        return {'msg': 'default reply records cleared'}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------- 默认回复管理接口（单数形式兼容路由） -------------------------
# 兼容前端使用 /api/default-reply/ 的请求

@content_router.get('/api/default-reply/{cid}', deprecated=True)
def get_default_reply_compat(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取指定账号的默认回复设置（兼容路由）"""
    return get_default_reply(cid, current_user)


@content_router.put('/api/default-reply/{cid}', deprecated=True)
def update_default_reply_compat(cid: str, reply_data: DefaultReplyIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    """更新指定账号的默认回复设置（兼容路由）"""
    return update_default_reply(cid, reply_data, current_user)


@content_router.delete('/api/default-reply/{cid}', deprecated=True)
def delete_default_reply_compat(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """删除指定账号的默认回复设置（兼容路由）"""
    return delete_default_reply(cid, current_user)


@content_router.post('/api/default-reply/{cid}/clear-records', deprecated=True)
def clear_default_reply_records_compat(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """清空指定账号的默认回复记录（兼容路由）"""
    return clear_default_reply_records(cid, current_user)


# ------------------------- 通知渠道管理接口 -------------------------

@content_router.get('/notification-channels')
def get_notification_channels(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取所有通知渠道"""
    from db_manager import db_manager
    try:
        user_id = current_user['user_id']
        return db_manager.get_notification_channels(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.post('/notification-channels')
def create_notification_channel(channel_data: NotificationChannelIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    """创建通知渠道"""
    from db_manager import db_manager
    try:
        user_id = current_user['user_id']
        channel_id = db_manager.create_notification_channel(
            channel_data.name,
            channel_data.type,
            channel_data.config,
            user_id
        )
        return {'msg': 'notification channel created', 'id': channel_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@content_router.get('/notification-channels/{channel_id}')
def get_notification_channel(channel_id: int, _: None = Depends(require_auth)):
    """获取指定通知渠道"""
    from db_manager import db_manager
    try:
        channel = db_manager.get_notification_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail='通知渠道不存在')
        return channel
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.put('/notification-channels/{channel_id}')
def update_notification_channel(channel_id: int, channel_data: NotificationChannelUpdate, _: None = Depends(require_auth)):
    """更新通知渠道"""
    from db_manager import db_manager
    try:
        success = db_manager.update_notification_channel(
            channel_id,
            channel_data.name,
            channel_data.config,
            channel_data.enabled
        )
        if success:
            return {'msg': 'notification channel updated'}
        else:
            raise HTTPException(status_code=404, detail='通知渠道不存在')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@content_router.delete('/notification-channels/{channel_id}')
def delete_notification_channel(channel_id: int, _: None = Depends(require_auth)):
    """删除通知渠道"""
    from db_manager import db_manager
    try:
        success = db_manager.delete_notification_channel(channel_id)
        if success:
            return {'msg': 'notification channel deleted'}
        else:
            raise HTTPException(status_code=404, detail='通知渠道不存在')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------- 消息通知配置接口 -------------------------

@content_router.get('/message-notifications')
def get_all_message_notifications(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取当前用户所有账号的消息通知配置"""
    from db_manager import db_manager
    try:
        # 只返回当前用户的消息通知配置
        user_id = current_user['user_id']
        user_cookies = db_manager.get_all_cookies(user_id)

        all_notifications = db_manager.get_all_message_notifications()
        # 过滤只属于当前用户的通知配置
        user_notifications = {cid: notifications for cid, notifications in all_notifications.items() if cid in user_cookies}
        return user_notifications
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.get('/message-notifications/{cid}')
def get_account_notifications(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取指定账号的消息通知配置"""
    from db_manager import db_manager
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限访问该Cookie")

        return db_manager.get_account_notifications(cid)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.post('/message-notifications/{cid}')
def set_message_notification(cid: str, notification_data: MessageNotificationIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    """设置账号的消息通知"""
    from db_manager import db_manager
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        # 检查通知渠道是否存在
        channel = db_manager.get_notification_channel(notification_data.channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail='通知渠道不存在')

        success = db_manager.set_message_notification(cid, notification_data.channel_id, notification_data.enabled)
        if success:
            return {'msg': 'message notification set'}
        else:
            raise HTTPException(status_code=400, detail='设置失败')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.delete('/message-notifications/account/{cid}')
def delete_account_notifications(cid: str, _: None = Depends(require_auth)):
    """删除账号的所有消息通知配置"""
    from db_manager import db_manager
    try:
        success = db_manager.delete_account_notifications(cid)
        if success:
            return {'msg': 'account notifications deleted'}
        else:
            raise HTTPException(status_code=404, detail='账号通知配置不存在')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.delete('/message-notifications/{notification_id}')
def delete_message_notification(notification_id: int, _: None = Depends(require_auth)):
    """删除消息通知配置"""
    from db_manager import db_manager
    try:
        success = db_manager.delete_message_notification(notification_id)
        if success:
            return {'msg': 'message notification deleted'}
        else:
            raise HTTPException(status_code=404, detail='通知配置不存在')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------- 系统设置接口 -------------------------

@settings_router.get('/system-settings/public')
def get_public_system_settings():
    """获取公开的系统设置（无需认证）"""
    try:
        all_settings = db_manager.get_all_system_settings()
        state = _registration_state()
        return {
            "registration_enabled": "true" if state['enabled'] else "false",
            "show_default_login_info": all_settings.get(
                "show_default_login_info", "false"
            ),
            "login_captcha_enabled": all_settings.get(
                "login_captcha_enabled", "true"
            ),
        }
    except Exception:
        logger.warning("获取公开系统设置失败")
        return {
            "registration_enabled": "false",
            "show_default_login_info": "false",
            "login_captcha_enabled": "true",
        }


@settings_router.get('/system-settings')
def get_system_settings(_: Dict[str, Any] = Depends(require_admin)):
    """获取类型化系统设置，不返回明文密钥。"""
    from db_manager import db_manager
    try:
        return normalize_system_settings(db_manager.get_all_system_settings())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _prepare_settings_section(section: str, request: SystemSettingsSectionIn) -> Dict[str, Any]:
    if section not in SETTINGS_SECTION_KEYS:
        raise HTTPException(status_code=404, detail='配置分区不存在')
    unknown = set(request.settings) - SETTINGS_SECTION_KEYS[section]
    if unknown:
        raise HTTPException(status_code=400, detail=f"不支持的配置项: {', '.join(sorted(unknown))}")

    values = dict(request.settings)
    raw = db_manager.get_all_system_settings()
    secret_for_section = {'ai': 'ai_api_key', 'smtp': 'smtp_password'}.get(section)
    if secret_for_section:
        action = request.secret_actions.get(secret_for_section, 'keep')
        try:
            values[secret_for_section] = apply_secret_action(
                raw.get(secret_for_section, ''), action, str(values.get(secret_for_section, ''))
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    if section == 'basic':
        interval = int(values.get('item_sync_interval', raw.get('item_sync_interval', 600)) or 600)
        pages = int(values.get('item_sync_max_pages', raw.get('item_sync_max_pages', 5)) or 5)
        if not 60 <= interval <= 86400:
            raise HTTPException(status_code=400, detail='商品同步间隔必须在1分钟到24小时之间')
        if not 1 <= pages <= 50:
            raise HTTPException(status_code=400, detail='同步页数必须在1到50之间')
    elif section == 'ai':
        api_url = str(values.get('ai_api_url', raw.get('ai_api_url', '')) or '').strip()
        model = str(values.get('ai_model', raw.get('ai_model', '')) or '').strip()
        if not api_url.startswith(('http://', 'https://')):
            raise HTTPException(status_code=400, detail='AI API地址必须以 http:// 或 https:// 开头')
        if not model:
            raise HTTPException(status_code=400, detail='AI模型不能为空')
    elif section == 'smtp':
        port = int(values.get('smtp_port', raw.get('smtp_port', 587)) or 587)
        if not 1 <= port <= 65535:
            raise HTTPException(status_code=400, detail='SMTP端口无效')
    return values


def _settings_summary() -> Dict[str, Any]:
    raw = db_manager.get_all_system_settings()
    settings = normalize_system_settings(raw)
    ai_configured = bool(raw.get('ai_api_url') and raw.get('ai_model') and raw.get('ai_api_key'))
    smtp_values = [raw.get('smtp_server'), raw.get('smtp_user'), raw.get('smtp_password')]
    smtp_status = smtp_configuration_status(raw, db_path=db_manager.db_path)
    smtp_configured = smtp_status['smtp_configured']
    smtp_verified = smtp_status['smtp_verified']
    smtp_partial = any(smtp_values) and not smtp_configured
    try:
        registration = _registration_state()
    except Exception:
        registration = {
            'enabled': False,
            'ready': False,
            'requested': False,
            'smtp_verified': False,
            'user_limit': 0,
            'user_count': 0,
            'remaining_slots': 0,
        }
    settings['smtp_verified'] = smtp_verified
    return {
        'settings': settings,
        'sections': {
            'basic': {'state': 'saved', 'label': '已保存', 'configured': True},
            'ai': {
                'state': 'ready' if ai_configured else 'missing',
                'label': '已配置' if ai_configured else '未配置',
                'configured': ai_configured,
                'model': settings.get('ai_model') or '',
            },
            'smtp': {
                'state': 'ready' if smtp_verified else ('warning' if smtp_configured or smtp_partial else 'missing'),
                'label': '已验证' if smtp_verified else ('待验证' if smtp_configured else ('配置不完整' if smtp_partial else '未配置')),
                'configured': smtp_configured,
                'verified': smtp_verified,
            },
        },
        'registration': registration,
        'runtime': {
            'cookie_manager': cookie_manager.manager is not None,
            'account_count': len(getattr(cookie_manager.manager, 'cookies', {}) or {}),
            'active_tasks': len(getattr(cookie_manager.manager, 'tasks', {}) or {}),
        },
    }


@settings_router.get('/api/settings/summary')
def get_settings_summary(_: Dict[str, Any] = Depends(require_admin)):
    return {'success': True, **_settings_summary()}


def _user_basic_settings_summary(user_id: int) -> Dict[str, Any]:
    resolved = resolve_user_basic_settings(
        db_manager.get_all_system_settings(),
        db_manager.get_user_settings(user_id),
    )
    return {"success": True, **resolved}


@settings_router.get('/api/settings/user-summary')
def get_user_settings_summary(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    return _user_basic_settings_summary(current_user['user_id'])


@settings_router.put('/api/settings/user-basic')
def save_user_basic_settings(
    request: UserBasicSettingsIn,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    values = (
        request.model_dump(exclude_none=True)
        if hasattr(request, "model_dump")
        else request.dict(exclude_none=True)
    )
    if not values:
        raise HTTPException(status_code=400, detail='至少提交一项个人设置')
    if not db_manager.set_user_settings(current_user['user_id'], values):
        raise HTTPException(status_code=500, detail='个人设置保存失败')
    return {
        **_user_basic_settings_summary(current_user['user_id']),
        "message": "个人同步设置已保存",
        "saved_at": datetime.now().isoformat(timespec='seconds'),
    }


@settings_router.put('/api/settings/sections/{section}')
def save_settings_section(section: str, request: SystemSettingsSectionIn,
                          _: Dict[str, Any] = Depends(require_admin)):
    values = _prepare_settings_section(section, request)
    if section == 'basic' and str(
        values.get('registration_enabled', '')
    ).strip().lower() in {'1', 'true', 'yes', 'on'}:
        try:
            ready = _registration_state()['ready']
        except Exception:
            ready = False
        if not ready:
            raise RegistrationError(
                "REGISTRATION_NOT_READY",
                "请先确认 SMTP、支持邮箱和注册容量",
                http_status=409,
            )
    if not db_manager.save_system_settings_section(values):
        raise HTTPException(status_code=500, detail='配置保存失败')
    return {
        'success': True,
        'message': '配置已保存',
        'saved_at': datetime.now().isoformat(timespec='seconds'),
        **_settings_summary(),
    }


@settings_router.post('/api/settings/verify/{section}')
def verify_settings_section(section: str, request: SystemSettingsVerifyIn,
                            _: Dict[str, Any] = Depends(require_admin)):
    if section not in {'ai', 'smtp'}:
        raise HTTPException(status_code=400, detail='该配置不需要连接验证')
    values = _prepare_settings_section(
        section,
        SystemSettingsSectionIn(settings=request.settings, secret_actions=request.secret_actions),
    )
    raw = db_manager.get_all_system_settings()
    effective = {**raw, **values}
    try:
        if section == 'ai':
            from openai import OpenAI
            api_key = effective.get('ai_api_key') or ''
            if not api_key:
                raise ValueError('AI API Key未配置')
            client = OpenAI(api_key=api_key, base_url=effective.get('ai_api_url'))
            kwargs = {
                'model': effective.get('ai_model'),
                'messages': [{'role': 'user', 'content': '回复OK'}],
                'max_tokens': 8,
                'temperature': 0,
            }
            if 'deepseek' in str(effective.get('ai_model', '')).lower() or 'deepseek' in str(effective.get('ai_api_url', '')).lower():
                kwargs['extra_body'] = {'thinking': {'type': 'disabled'}}
            client.chat.completions.create(**kwargs)
            return {'success': True, 'state': 'ready', 'message': 'AI连接可用'}
        recipient = normalize_email(
            str(effective.get('support_email') or '')
        ).normalized
        smtp_settings = {
            key: effective.get(key, '') for key in SMTP_CONFIGURATION_KEYS
        }
        if not db_manager.save_unverified_smtp_settings(smtp_settings):
            raise RegistrationError(
                "SMTP_VERIFICATION_SAVE_FAILED",
                "SMTP 待验证配置保存失败，请重试",
                http_status=503,
            )
        current = db_manager.get_all_system_settings()
        fingerprint = smtp_configuration_fingerprint(
            current,
            db_path=db_manager.db_path,
        )
        verification_code = f"{secrets.randbelow(1_000_000):06d}"
        SMTPEmailSender().send(
            current,
            recipient=recipient,
            subject='闲鱼监控台 SMTP 验证码',
            text=(
                f'您的 SMTP 验证码是 {verification_code}\n\n'
                '验证码在 10 分钟内有效，最多可尝试 5 次。'
            ),
        )
        challenge = db_manager.registration_service.create_challenge(
            purpose='smtp_verify_email',
            subject=recipient,
            context=fingerprint,
            secret=verification_code,
        )
        return {
            'success': True,
            'state': 'pending',
            'challenge_id': challenge['challenge_id'],
            'expires_in': 600,
            'masked_recipient': mask_email_for_log(recipient),
            'message': '验证邮件已发送',
        }
    except (SMTPConfigurationError, SMTPDeliveryError) as e:
        logger.warning(f"SMTP配置验证失败: {type(e).__name__}")
        raise RegistrationError(
            "SMTP_VERIFICATION_FAILED",
            "SMTP 验证邮件发送失败，请检查配置",
        ) from e
    except RegistrationError:
        raise
    except Exception as e:
        logger.warning(f"{section.upper()}配置验证失败: {type(e).__name__}")
        raise HTTPException(status_code=400, detail=f"验证失败: {str(e)}")


@settings_router.post('/api/settings/verify/smtp/confirm')
def confirm_smtp_verification(
    request: SMTPVerificationConfirmRequest,
    _: Dict[str, Any] = Depends(require_admin),
):
    verified_at = datetime.now().astimezone().isoformat(timespec='seconds')
    confirmation = db_manager.registration_service.confirm_smtp_verification(
        challenge_id=request.challenge_id,
        verification_code=request.verification_code,
        verified_at=verified_at,
    )
    return {
        'success': True,
        'state': 'ready',
        'verified_at': confirmation['verified_at'],
        'message': 'SMTP 配置已确认',
    }


@settings_router.put('/system-settings/{key}')
def update_system_setting(key: str, setting_data: SystemSettingIn,
                          _: Dict[str, Any] = Depends(require_admin)):
    """更新系统设置"""
    try:
        # 禁止直接修改密码哈希
        if key == 'admin_password_hash':
            raise HTTPException(status_code=400, detail='请使用密码修改接口')
        if key in {'smtp_verified_fingerprint', 'smtp_verified_at'}:
            raise HTTPException(status_code=400, detail='该设置只能由 SMTP 验证流程更新')
        if key == 'registration_enabled' and setting_data.value.strip().lower() == 'true':
            if not _registration_state()['ready']:
                raise RegistrationError(
                    "REGISTRATION_NOT_READY",
                    "请先确认 SMTP、支持邮箱和注册容量",
                    http_status=409,
                )

        success = db_manager.set_system_setting(key, setting_data.value, setting_data.description)
        if success:
            return {'msg': 'system setting updated'}
        else:
            raise HTTPException(status_code=400, detail='更新失败')
    except (HTTPException, RegistrationError):
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------- 注册设置接口 -------------------------


def _update_registration_enabled(
    enabled: bool,
    admin_user: Dict[str, Any],
) -> Dict[str, Any]:
    if enabled:
        try:
            ready = _registration_state()['ready']
        except Exception:
            ready = False
        if not ready:
            raise RegistrationError(
                "REGISTRATION_NOT_READY",
                "请先确认 SMTP、支持邮箱和注册容量",
                http_status=409,
            )
    if not db_manager.set_system_setting(
        'registration_enabled',
        'true' if enabled else 'false',
        '是否开启用户注册',
    ):
        raise RegistrationError(
            "REGISTRATION_SETTING_FAILED",
            "注册开关保存失败",
            http_status=503,
        )
    log_with_user(
        'info',
        f"更新注册设置: {'开启' if enabled else '关闭'}",
        admin_user,
    )
    return {
        'success': True,
        'enabled': enabled,
        'message': f"注册功能已{'开启' if enabled else '关闭'}",
    }


@admin_router.get('/api/admin/registration/status')
def get_registration_admin_status(
    _: Dict[str, Any] = Depends(require_admin),
):
    settings = db_manager.get_all_system_settings()
    state = _registration_state()
    support_email = str(
        settings.get('support_email') or settings.get('smtp_user') or ''
    ).strip()
    return {
        'success': True,
        'user_limit': state['user_limit'],
        'user_count': state['user_count'],
        'remaining_slots': state['remaining_slots'],
        'registration': {
            'enabled': state['enabled'],
            'ready': state['ready'],
            'requested': state['requested'],
            'terms_version': state['terms_version'],
        },
        'smtp': {
            'configured': state['smtp_configured'],
            'verified': state['smtp_verified'],
            'verified_at': settings.get('smtp_verified_at') or '',
            'support_email': mask_email_for_log(support_email)
            if support_email else '',
        },
    }


@admin_router.post('/api/admin/registration/invites')
def create_registration_invites(
    _request: Dict[str, Any] = Body(default_factory=dict),
    _: Dict[str, Any] = Depends(require_admin),
):
    raise RegistrationError(
        "INVITATION_REGISTRATION_REMOVED",
        "邀请注册已移除，请使用直接注册配置",
        http_status=410,
    )


@admin_router.get('/api/admin/registration/invites')
def list_registration_invites(
    _: Dict[str, Any] = Depends(require_admin),
):
    raise RegistrationError(
        "INVITATION_REGISTRATION_REMOVED",
        "邀请注册已移除，请使用直接注册配置",
        http_status=410,
    )


@admin_router.delete('/api/admin/registration/invites/{invite_id}')
def revoke_registration_invite(
    invite_id: int,
    _: Dict[str, Any] = Depends(require_admin),
):
    del invite_id
    raise RegistrationError(
        "INVITATION_REGISTRATION_REMOVED",
        "邀请注册已移除，请使用直接注册配置",
        http_status=410,
    )


@admin_router.put('/api/admin/registration/limit')
def update_registration_limit(
    request: RegistrationLimitUpdate,
    _: Dict[str, Any] = Depends(require_admin),
):
    capacity = db_manager.registration_service.update_registration_limit(
        request.limit
    )
    state = _registration_state()
    return {
        'success': True,
        **capacity,
        'enabled': state['enabled'],
        'requested': state['requested'],
        'message': '注册用户上限已更新',
    }


@admin_router.get('/api/admin/registration/users')
def list_registration_users(
    limit: int = Query(50, ge=1, le=200),
    _: Dict[str, Any] = Depends(require_admin),
):
    users = [
        user
        for user in db_manager.user_repository.list_recent(
            limit=min(200, limit + 1)
        )
        if str(user.get('username') or '').casefold()
        != ADMIN_USERNAME.casefold()
    ][:limit]
    return {
        'success': True,
        'users': [
            {
                key: user.get(key)
                for key in (
                    'id',
                    'username',
                    'email',
                    'is_active',
                    'created_at',
                    'terms_version',
                    'terms_accepted_at',
                )
            }
            for user in users
        ],
    }


@admin_router.put('/api/admin/registration/users/{user_id}')
def update_registration_user(
    user_id: int,
    request: UserActiveUpdate,
    _: Dict[str, Any] = Depends(require_admin),
):
    target = db_manager.get_user_by_id(user_id)
    if target and str(target.get('username') or '').casefold() == ADMIN_USERNAME.casefold():
        raise RegistrationError(
            "ADMIN_DEACTIVATION_FORBIDDEN",
            "管理员账号不能通过注册管理修改",
        )
    user = db_manager.auth_service.set_user_active(user_id, request.is_active)
    if not request.is_active:
        _drop_user_sessions_from_memory(user_id)
    return {'success': True, 'user': user}


@admin_router.put('/api/admin/registration/enabled')
def update_registration_enabled(
    request: RegistrationSettingUpdate,
    admin_user: Dict[str, Any] = Depends(require_admin),
):
    return _update_registration_enabled(request.enabled, admin_user)

@settings_router.get('/registration-status')
def get_registration_status():
    """兼容旧客户端的公开注册状态。"""
    try:
        state = _registration_state()
        return {
            'enabled': state['enabled'],
            'ready': state['ready'],
            'message': '注册功能已开启' if state['enabled'] else '注册暂未开放',
        }
    except Exception:
        logger.warning("获取注册状态失败")
        return {
            'enabled': False,
            'ready': False,
            'message': '注册暂未开放',
        }


@auth_router.get('/login-info-status')
def get_login_info_status():
    """获取默认登录信息显示状态（公开接口，无需认证）"""
    from db_manager import db_manager
    try:
        enabled_str = db_manager.get_system_setting('show_default_login_info')
        logger.debug(f"从数据库获取的登录信息显示设置值: '{enabled_str}'")

        # 如果设置不存在，默认为开启
        if enabled_str is None:
            enabled_bool = True
        else:
            enabled_bool = enabled_str == 'true'

        return {"enabled": enabled_bool}
    except Exception as e:
        logger.error(f"获取登录信息显示状态失败: {e}")
        # 出错时默认为开启
        return {"enabled": True}


class LoginInfoSettingUpdate(BaseModel):
    enabled: bool


@settings_router.put('/registration-settings')
def update_registration_settings(setting_data: RegistrationSettingUpdate, admin_user: Dict[str, Any] = Depends(require_admin)):
    """兼容旧客户端的管理员注册开关。"""
    return _update_registration_enabled(setting_data.enabled, admin_user)

@auth_router.put('/login-info-settings')
def update_login_info_settings(setting_data: LoginInfoSettingUpdate, admin_user: Dict[str, Any] = Depends(require_admin)):
    """更新默认登录信息显示设置（仅管理员）"""
    from db_manager import db_manager
    try:
        enabled = setting_data.enabled
        success = db_manager.set_system_setting(
            'show_default_login_info',
            'true' if enabled else 'false',
            '是否显示默认登录信息'
        )
        if success:
            log_with_user('info', f"更新登录信息显示设置: {'开启' if enabled else '关闭'}", admin_user)
            return {
                'success': True,
                'enabled': enabled,
                'message': f"默认登录信息显示已{'开启' if enabled else '关闭'}"
            }
        else:
            raise HTTPException(status_code=500, detail='更新登录信息显示设置失败')
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新登录信息显示设置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))




@accounts_router.delete("/cookies/{cid}")
def remove_cookie(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        cookie_manager.manager.remove_cookie(cid)
        return {"msg": "removed"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class AutoConfirmUpdate(BaseModel):
    auto_confirm: bool


class RemarkUpdate(BaseModel):
    remark: str


class PauseDurationUpdate(BaseModel):
    pause_duration: int


@accounts_router.put("/cookies/{cid}/auto-confirm")
def update_auto_confirm(cid: str, update_data: AutoConfirmUpdate, current_user: Dict[str, Any] = Depends(get_current_user)):
    """更新账号的自动确认发货设置"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        # 更新数据库中的auto_confirm设置
        success = db_manager.update_auto_confirm(cid, update_data.auto_confirm)
        if not success:
            raise HTTPException(status_code=500, detail="更新自动确认发货设置失败")

        # 通知CookieManager更新设置（如果账号正在运行）
        if hasattr(cookie_manager.manager, 'update_auto_confirm_setting'):
            cookie_manager.manager.update_auto_confirm_setting(cid, update_data.auto_confirm)

        return {
            "msg": "success",
            "auto_confirm": update_data.auto_confirm,
            "message": f"自动确认发货已{'开启' if update_data.auto_confirm else '关闭'}"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@accounts_router.get("/cookies/{cid}/auto-confirm")
def get_auto_confirm(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取账号的自动确认发货设置"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        # 获取auto_confirm设置
        auto_confirm = db_manager.get_auto_confirm(cid)
        return {
            "auto_confirm": auto_confirm,
            "message": f"自动确认发货当前{'开启' if auto_confirm else '关闭'}"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@accounts_router.put("/cookies/{cid}/remark")
def update_cookie_remark(cid: str, update_data: RemarkUpdate, current_user: Dict[str, Any] = Depends(get_current_user)):
    """更新账号备注"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        # 更新备注
        success = db_manager.update_cookie_remark(cid, update_data.remark)
        if success:
            log_with_user('info', f"更新账号备注: {cid} -> {update_data.remark}", current_user)
            return {
                "message": "备注更新成功",
                "remark": update_data.remark
            }
        else:
            raise HTTPException(status_code=500, detail="备注更新失败")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@accounts_router.get("/cookies/{cid}/remark")
def get_cookie_remark(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取账号备注"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        # 获取Cookie详细信息（包含备注）
        cookie_details = db_manager.get_cookie_details(cid)
        if cookie_details:
            return {
                "remark": cookie_details.get('remark', ''),
                "message": "获取备注成功"
            }
        else:
            raise HTTPException(status_code=404, detail="账号不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@accounts_router.put("/cookies/{cid}/pause-duration")
def update_cookie_pause_duration(cid: str, update_data: PauseDurationUpdate, current_user: Dict[str, Any] = Depends(get_current_user)):
    """更新账号自动回复暂停时间"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        # 验证暂停时间范围（0-120分钟，0表示不暂停）
        if not (0 <= update_data.pause_duration <= 120):
            raise HTTPException(status_code=400, detail="暂停时间必须在0-120分钟之间（0表示不暂停）")

        # 更新暂停时间
        success = db_manager.update_cookie_pause_duration(cid, update_data.pause_duration)
        if success:
            log_with_user('info', f"更新账号自动回复暂停时间: {cid} -> {update_data.pause_duration}分钟", current_user)
            return {
                "message": "暂停时间更新成功",
                "pause_duration": update_data.pause_duration
            }
        else:
            raise HTTPException(status_code=500, detail="暂停时间更新失败")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@accounts_router.get("/cookies/{cid}/pause-duration")
def get_cookie_pause_duration(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取账号自动回复暂停时间"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        # 获取暂停时间
        pause_duration = db_manager.get_cookie_pause_duration(cid)
        return {
            "pause_duration": pause_duration,
            "message": "获取暂停时间成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class KeywordIn(BaseModel):
    keywords: Dict[str, str]  # key -> reply

class KeywordWithItemIdIn(BaseModel):
    keywords: List[Dict[str, Any]]  # [{"keyword": str, "reply": str, "item_id": str}]


@content_router.get("/keywords/{cid}")
def get_keywords(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")

    # 检查cookie是否属于当前用户
    user_id = current_user['user_id']
    from db_manager import db_manager
    user_cookies = db_manager.get_all_cookies(user_id)

    if cid not in user_cookies:
        raise HTTPException(status_code=403, detail="无权限访问该Cookie")

    # 直接从数据库获取所有关键词（避免重复计算）
    item_keywords = db_manager.get_keywords_with_item_id(cid)

    # 转换为统一格式
    all_keywords = []
    for keyword, reply, item_id in item_keywords:
        all_keywords.append({
            "keyword": keyword,
            "reply": reply,
            "item_id": item_id,
            "type": "item" if item_id else "normal"
        })

    return all_keywords


@content_router.get("/keywords-with-item-id/{cid}")
def get_keywords_with_item_id(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取包含商品ID的关键词列表"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")

    # 检查cookie是否属于当前用户
    user_id = current_user['user_id']
    from db_manager import db_manager
    user_cookies = db_manager.get_all_cookies(user_id)

    if cid not in user_cookies:
        raise HTTPException(status_code=403, detail="无权限访问该Cookie")

    # 获取包含类型信息的关键词
    keywords = db_manager.get_keywords_with_type(cid)

    # 转换为前端需要的格式
    result = []
    for keyword_data in keywords:
        result.append({
            "keyword": keyword_data['keyword'],
            "reply": keyword_data['reply'],
            "item_id": keyword_data['item_id'] or "",
            "type": keyword_data['type'],
            "image_url": keyword_data['image_url']
        })

    return result


@content_router.post("/keywords/{cid}")
def update_keywords(cid: str, body: KeywordIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")

    # 检查cookie是否属于当前用户
    user_id = current_user['user_id']
    from db_manager import db_manager
    user_cookies = db_manager.get_all_cookies(user_id)

    if cid not in user_cookies:
        log_with_user('warning', f"尝试操作其他用户的Cookie关键字: {cid}", current_user)
        raise HTTPException(status_code=403, detail="无权限操作该Cookie")

    kw_list = [(k, v) for k, v in body.keywords.items()]
    log_with_user('info', f"更新Cookie关键字: {cid}, 数量: {len(kw_list)}", current_user)

    cookie_manager.manager.update_keywords(cid, kw_list)
    log_with_user('info', f"Cookie关键字更新成功: {cid}", current_user)
    return {"msg": "updated", "count": len(kw_list)}


@content_router.post("/keywords-with-item-id/{cid}")
def update_keywords_with_item_id(cid: str, body: KeywordWithItemIdIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    """更新包含商品ID的关键词列表"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")

    # 检查cookie是否属于当前用户
    user_id = current_user['user_id']
    from db_manager import db_manager
    user_cookies = db_manager.get_all_cookies(user_id)

    if cid not in user_cookies:
        log_with_user('warning', f"尝试操作其他用户的Cookie关键字: {cid}", current_user)
        raise HTTPException(status_code=403, detail="无权限操作该Cookie")

    # 验证数据格式
    keywords_to_save = []
    keyword_set = set()  # 用于检查当前提交的关键词中是否有重复

    for kw_data in body.keywords:
        keyword = kw_data.get('keyword', '').strip()
        reply = kw_data.get('reply', '').strip()
        item_id = kw_data.get('item_id', '').strip() or None

        if not keyword:
            raise HTTPException(status_code=400, detail="关键词不能为空")

        # 检查当前提交的关键词中是否有重复
        keyword_key = f"{keyword}|{item_id or ''}"
        if keyword_key in keyword_set:
            item_id_text = f"（商品ID: {item_id}）" if item_id else "（通用关键词）"
            raise HTTPException(status_code=400, detail=f"关键词 '{keyword}' {item_id_text} 在当前提交中重复")
        keyword_set.add(keyword_key)

        keywords_to_save.append((keyword, reply, item_id))

    # 保存关键词（只保存文本关键词，保留图片关键词）
    try:
        success = db_manager.save_text_keywords_only(cid, keywords_to_save)
        if not success:
            raise HTTPException(status_code=500, detail="保存关键词失败")
    except Exception as e:
        error_msg = str(e)

        # 检查是否是图片关键词冲突
        if "已存在（图片关键词）" in error_msg:
            # 直接使用数据库管理器提供的友好错误信息
            raise HTTPException(status_code=400, detail=error_msg)
        elif "UNIQUE constraint failed" in error_msg or "唯一约束冲突" in error_msg:
            # 尝试从错误信息中提取具体的冲突关键词
            conflict_keyword = None
            conflict_type = None

            # 检查是否是数据库管理器抛出的详细错误
            if "关键词唯一约束冲突" in error_msg:
                # 解析详细错误信息：关键词唯一约束冲突: Cookie=xxx, 关键词='xxx', 通用关键词/商品ID: xxx
                import re
                keyword_match = re.search(r"关键词='([^']+)'", error_msg)
                if keyword_match:
                    conflict_keyword = keyword_match.group(1)

                if "通用关键词" in error_msg:
                    conflict_type = "通用关键词"
                elif "商品ID:" in error_msg:
                    item_match = re.search(r"商品ID: ([^\s,]+)", error_msg)
                    if item_match:
                        conflict_type = f"商品关键词（商品ID: {item_match.group(1)}）"

            # 构造用户友好的错误信息
            if conflict_keyword and conflict_type:
                detail_msg = f'关键词 "{conflict_keyword}" （{conflict_type}） 已存在，请使用其他关键词或商品ID'
            elif "keywords.cookie_id, keywords.keyword" in error_msg:
                detail_msg = "关键词重复！该关键词已存在（可能是图片关键词或文本关键词），请使用其他关键词"
            else:
                detail_msg = "关键词重复！请使用不同的关键词或商品ID组合"

            raise HTTPException(status_code=400, detail=detail_msg)
        else:
            log_with_user('error', f"保存关键词时发生未知错误: {error_msg}", current_user)
            raise HTTPException(status_code=500, detail="保存关键词失败")

    log_with_user('info', f"更新Cookie关键字(含商品ID): {cid}, 数量: {len(keywords_to_save)}", current_user)
    return {"msg": "updated", "count": len(keywords_to_save)}


@content_router.get("/items/{cid}")
def get_items_list(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取指定账号的商品列表"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")

    # 检查cookie是否属于当前用户
    user_id = current_user['user_id']
    from db_manager import db_manager
    user_cookies = db_manager.get_all_cookies(user_id)

    if cid not in user_cookies:
        raise HTTPException(status_code=403, detail="无权限访问该Cookie")

    try:
        # 获取该账号的所有商品
        with db_manager.lock:
            cursor = db_manager.conn.cursor()
            cursor.execute('''
            SELECT item_id, item_title, item_price, created_at
            FROM item_info
            WHERE cookie_id = ?
            ORDER BY created_at DESC
            ''', (cid,))

            items = []
            for row in cursor.fetchall():
                items.append({
                    'item_id': row[0],
                    'item_title': row[1] or '未知商品',
                    'item_price': row[2] or '价格未知',
                    'created_at': row[3]
                })

            return {"items": items, "count": len(items)}

    except Exception as e:
        logger.error(f"获取商品列表失败: {e}")
        raise HTTPException(status_code=500, detail="获取商品列表失败")


@content_router.get("/keywords-export/{cid}")
def export_keywords(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """导出指定账号的关键词为Excel文件"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")

    # 检查cookie是否属于当前用户
    user_id = current_user['user_id']
    from db_manager import db_manager
    user_cookies = db_manager.get_all_cookies(user_id)

    if cid not in user_cookies:
        raise HTTPException(status_code=403, detail="无权限访问该Cookie")

    try:
        # 获取关键词数据（包含类型信息）
        keywords = db_manager.get_keywords_with_type(cid)

        # 创建DataFrame，只导出文本类型的关键词
        data = []
        for keyword_data in keywords:
            # 只导出文本类型的关键词
            if keyword_data.get('type', 'text') == 'text':
                data.append({
                    '关键词': keyword_data['keyword'],
                    '商品ID': keyword_data['item_id'] or '',
                    '关键词内容': keyword_data['reply']
                })

        # 如果没有数据，创建空的DataFrame但保留列名（作为模板）
        if not data:
            df = pd.DataFrame(columns=['关键词', '商品ID', '关键词内容'])
        else:
            df = pd.DataFrame(data)

        # 创建Excel文件
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='关键词数据', index=False)

            # 如果是空模板，添加一些示例说明
            if data == []:
                worksheet = writer.sheets['关键词数据']
                # 添加示例数据作为注释（从第2行开始）
                worksheet['A2'] = '你好'
                worksheet['B2'] = ''
                worksheet['C2'] = '您好！欢迎咨询，有什么可以帮助您的吗？'

                worksheet['A3'] = '价格'
                worksheet['B3'] = '123456'
                worksheet['C3'] = '这个商品的价格是99元，现在有优惠活动哦！'

                worksheet['A4'] = '发货'
                worksheet['B4'] = ''
                worksheet['C4'] = '我们会在24小时内发货，请耐心等待。'

                # 设置示例行的样式（浅灰色背景）
                from openpyxl.styles import PatternFill
                gray_fill = PatternFill(start_color='F0F0F0', end_color='F0F0F0', fill_type='solid')
                for row in range(2, 5):
                    for col in range(1, 4):
                        worksheet.cell(row=row, column=col).fill = gray_fill

        output.seek(0)

        # 生成文件名（使用URL编码处理中文）
        from urllib.parse import quote
        if not data:
            filename = f"keywords_template_{cid}_{int(time.time())}.xlsx"
        else:
            filename = f"keywords_{cid}_{int(time.time())}.xlsx"
        encoded_filename = quote(filename.encode('utf-8'))

        # 返回文件
        return StreamingResponse(
            io.BytesIO(output.read()),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
            }
        )

    except Exception as e:
        logger.error(f"导出关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"导出关键词失败: {str(e)}")


@content_router.post("/keywords-import/{cid}")
async def import_keywords(cid: str, file: UploadFile = File(...), current_user: Dict[str, Any] = Depends(get_current_user)):
    """导入Excel文件中的关键词到指定账号"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")

    # 检查cookie是否属于当前用户
    user_id = current_user['user_id']
    from db_manager import db_manager
    user_cookies = db_manager.get_all_cookies(user_id)

    if cid not in user_cookies:
        raise HTTPException(status_code=403, detail="无权限访问该Cookie")

    # 检查文件类型
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="请上传Excel文件(.xlsx或.xls)")

    try:
        # 读取Excel文件
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))

        # 检查必要的列
        required_columns = ['关键词', '商品ID', '关键词内容']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise HTTPException(status_code=400, detail=f"Excel文件缺少必要的列: {', '.join(missing_columns)}")

        # 获取现有的文本类型关键词（用于比较更新/新增）
        existing_keywords = db_manager.get_keywords_with_type(cid)
        existing_dict = {}
        for keyword_data in existing_keywords:
            # 只考虑文本类型的关键词
            if keyword_data.get('type', 'text') == 'text':
                keyword = keyword_data['keyword']
                reply = keyword_data['reply']
                item_id = keyword_data['item_id']
                key = f"{keyword}|{item_id or ''}"
                existing_dict[key] = (keyword, reply, item_id)

        # 处理导入数据
        import_data = []
        update_count = 0
        add_count = 0

        def clean_cell_value(value):
            """清理单元格值，处理数字转字符串时的 .0 后缀问题"""
            if pd.isna(value):
                return ''
            # 如果是数字类型，先转为整数（如果是整数值）再转字符串
            if isinstance(value, float) and value == int(value):
                return str(int(value)).strip()
            return str(value).strip()

        for index, row in df.iterrows():
            keyword = clean_cell_value(row['关键词'])
            item_id = clean_cell_value(row['商品ID']) or None
            reply = clean_cell_value(row['关键词内容'])

            if not keyword:
                continue  # 跳过没有关键词的行

            # 检查是否重复
            key = f"{keyword}|{item_id or ''}"
            if key in existing_dict:
                # 更新现有关键词
                update_count += 1
            else:
                # 新增关键词
                add_count += 1

            import_data.append((keyword, reply, item_id))

        if not import_data:
            raise HTTPException(status_code=400, detail="Excel文件中没有有效的关键词数据")

        # 保存到数据库（只影响文本关键词，保留图片关键词）
        success = db_manager.save_text_keywords_only(cid, import_data)
        if not success:
            raise HTTPException(status_code=500, detail="保存关键词到数据库失败")

        log_with_user('info', f"导入关键词成功: {cid}, 新增: {add_count}, 更新: {update_count}", current_user)

        return {
            "msg": "导入成功",
            "total": len(import_data),
            "added": add_count,
            "updated": update_count
        }

    except pd.errors.EmptyDataError:
        raise HTTPException(status_code=400, detail="Excel文件为空")
    except pd.errors.ParserError:
        raise HTTPException(status_code=400, detail="Excel文件格式错误")
    except Exception as e:
        logger.error(f"导入关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"导入关键词失败: {str(e)}")


@content_router.post("/keywords/{cid}/image")
async def add_image_keyword(
    cid: str,
    keyword: str = Form(...),
    item_id: str = Form(default=""),
    image: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """添加图片关键词"""
    logger.info(f"接收到图片关键词添加请求: cid={cid}, keyword={keyword}, item_id={item_id}")

    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")

    # 检查参数
    if not keyword or not keyword.strip():
        raise HTTPException(status_code=400, detail="关键词不能为空")

    if not image or not image.filename:
        raise HTTPException(status_code=400, detail="请选择图片文件")

    # 检查cookie是否属于当前用户
    cookie_details = db_manager.get_cookie_details(cid)
    if not cookie_details or cookie_details['user_id'] != current_user['user_id']:
        raise HTTPException(status_code=404, detail="账号不存在或无权限")

    try:
        logger.info(f"接收到图片关键词添加请求: cid={cid}, keyword={keyword}, item_id={item_id}, filename={image.filename}")

        # 验证图片文件
        if not image.content_type or not image.content_type.startswith('image/'):
            logger.warning(f"无效的图片文件类型: {image.content_type}")
            raise HTTPException(status_code=400, detail="请上传图片文件")

        # 读取图片数据
        image_data = await image.read()
        logger.info(f"读取图片数据成功，大小: {len(image_data)} bytes")

        # 保存图片
        image_url = image_manager.save_image(image_data, image.filename)
        if not image_url:
            logger.error("图片保存失败")
            raise HTTPException(status_code=400, detail="图片保存失败")

        logger.info(f"图片保存成功: {image_url}")

        # 先检查关键词是否已存在
        normalized_item_id = item_id if item_id and item_id.strip() else None
        if db_manager.check_keyword_duplicate(cid, keyword, normalized_item_id):
            # 删除已保存的图片
            image_manager.delete_image(image_url)
            if normalized_item_id:
                raise HTTPException(status_code=400, detail=f"关键词 '{keyword}' 在商品 '{normalized_item_id}' 中已存在")
            else:
                raise HTTPException(status_code=400, detail=f"通用关键词 '{keyword}' 已存在")

        # 保存图片关键词到数据库
        success = db_manager.save_image_keyword(cid, keyword, image_url, item_id or None)
        if not success:
            # 如果数据库保存失败，删除已保存的图片
            logger.error("数据库保存失败，删除已保存的图片")
            image_manager.delete_image(image_url)
            raise HTTPException(status_code=400, detail="图片关键词保存失败，请稍后重试")

        log_with_user('info', f"添加图片关键词成功: {cid}, 关键词: {keyword}", current_user)

        return {
            "msg": "图片关键词添加成功",
            "keyword": keyword,
            "image_url": image_url,
            "item_id": item_id or None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"添加图片关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"添加图片关键词失败: {str(e)}")


@content_router.post("/upload-image")
async def upload_image(
    image: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """上传图片（用于卡券等功能）"""
    try:
        logger.info(f"接收到图片上传请求: filename={image.filename}")

        # 验证图片文件
        if not image.content_type or not image.content_type.startswith('image/'):
            logger.warning(f"无效的图片文件类型: {image.content_type}")
            raise HTTPException(status_code=400, detail="请上传图片文件")

        # 读取图片数据
        image_data = await image.read()
        logger.info(f"读取图片数据成功，大小: {len(image_data)} bytes")

        # 保存图片
        image_url = image_manager.save_image(image_data, image.filename)
        if not image_url:
            logger.error("图片保存失败")
            raise HTTPException(status_code=400, detail="图片保存失败")

        logger.info(f"图片上传成功: {image_url}")

        return {
            "message": "图片上传成功",
            "image_url": image_url
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"图片上传失败: {e}")
        raise HTTPException(status_code=500, detail=f"图片上传失败: {str(e)}")


@content_router.get("/keywords-with-type/{cid}")
def get_keywords_with_type(cid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取包含类型信息的关键词列表"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")

    # 检查cookie是否属于当前用户
    cookie_details = db_manager.get_cookie_details(cid)
    if not cookie_details or cookie_details['user_id'] != current_user['user_id']:
        raise HTTPException(status_code=404, detail="账号不存在或无权限")

    try:
        keywords = db_manager.get_keywords_with_type(cid)
        return keywords
    except Exception as e:
        logger.error(f"获取关键词列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取关键词列表失败: {str(e)}")


@content_router.delete("/keywords/{cid}/{index}")
def delete_keyword_by_index(cid: str, index: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    """根据索引删除关键词"""
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail="CookieManager 未就绪")

    # 检查cookie是否属于当前用户
    cookie_details = db_manager.get_cookie_details(cid)
    if not cookie_details or cookie_details['user_id'] != current_user['user_id']:
        raise HTTPException(status_code=404, detail="账号不存在或无权限")

    try:
        # 先获取要删除的关键词信息（用于删除图片文件）
        keywords = db_manager.get_keywords_with_type(cid)
        if 0 <= index < len(keywords):
            keyword_data = keywords[index]

            # 删除关键词
            success = db_manager.delete_keyword_by_index(cid, index)
            if not success:
                raise HTTPException(status_code=400, detail="删除关键词失败")

            # 如果是图片关键词，删除对应的图片文件
            if keyword_data.get('type') == 'image' and keyword_data.get('image_url'):
                image_manager.delete_image(keyword_data['image_url'])

            log_with_user('info', f"删除关键词成功: {cid}, 索引: {index}, 关键词: {keyword_data.get('keyword')}", current_user)

            return {"msg": "删除成功"}
        else:
            raise HTTPException(status_code=400, detail="关键词索引无效")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除关键词失败: {str(e)}")


@content_router.get("/debug/keywords-table-info")
def debug_keywords_table_info(current_user: Dict[str, Any] = Depends(get_current_user)):
    """调试：检查keywords表结构"""
    try:
        import sqlite3
        conn = sqlite3.connect(db_manager.db_path)
        cursor = conn.cursor()

        # 获取表结构信息
        cursor.execute("PRAGMA table_info(keywords)")
        columns = cursor.fetchall()

        # 获取数据库版本
        cursor.execute("SELECT value FROM system_settings WHERE key = 'db_version'")
        version_result = cursor.fetchone()
        db_version = version_result[0] if version_result else "未知"

        conn.close()

        return {
            "db_version": db_version,
            "table_columns": [{"name": col[1], "type": col[2], "default": col[4]} for col in columns]
        }
    except Exception as e:
        logger.error(f"检查表结构失败: {e}")
        raise HTTPException(status_code=500, detail=f"检查表结构失败: {str(e)}")


# 卡券管理API
@content_router.get("/cards")
def get_cards(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取当前用户的卡券列表"""
    try:
        from db_manager import db_manager
        user_id = current_user['user_id']
        cards = db_manager.get_all_cards(user_id)
        return cards
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.post("/cards")
def create_card(card_data: dict, current_user: Dict[str, Any] = Depends(get_current_user)):
    """创建新卡券"""
    try:
        from db_manager import db_manager
        user_id = current_user['user_id']
        card_name = card_data.get('name', '未命名卡券')

        log_with_user('info', f"创建卡券: {card_name}", current_user)

        # 验证多规格字段
        is_multi_spec = card_data.get('is_multi_spec', False)
        if is_multi_spec:
            if not card_data.get('spec_name') or not card_data.get('spec_value'):
                raise HTTPException(status_code=400, detail="多规格卡券必须提供规格名称和规格值")

        card_id = db_manager.create_card(
            name=card_data.get('name'),
            card_type=card_data.get('type'),
            api_config=card_data.get('api_config'),
            text_content=card_data.get('text_content'),
            data_content=card_data.get('data_content'),
            image_url=card_data.get('image_url'),
            description=card_data.get('description'),
            enabled=card_data.get('enabled', True),
            delay_seconds=card_data.get('delay_seconds', 0),
            is_multi_spec=is_multi_spec,
            spec_name=card_data.get('spec_name') if is_multi_spec else None,
            spec_value=card_data.get('spec_value') if is_multi_spec else None,
            user_id=user_id
        )

        log_with_user('info', f"卡券创建成功: {card_name} (ID: {card_id})", current_user)
        return {"id": card_id, "message": "卡券创建成功"}
    except Exception as e:
        log_with_user('error', f"创建卡券失败: {card_data.get('name', '未知')} - {str(e)}", current_user)
        raise HTTPException(status_code=500, detail=str(e))


@content_router.get("/cards/{card_id}")
def get_card(card_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取单个卡券详情"""
    try:
        from db_manager import db_manager
        user_id = current_user['user_id']
        card = db_manager.get_card_by_id(card_id, user_id)
        if card:
            return card
        else:
            raise HTTPException(status_code=404, detail="卡券不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.put("/cards/{card_id}")
def update_card(card_id: int, card_data: dict, _: None = Depends(require_auth)):
    """更新卡券"""
    try:
        from db_manager import db_manager
        # 验证多规格字段
        is_multi_spec = card_data.get('is_multi_spec')
        if is_multi_spec:
            if not card_data.get('spec_name') or not card_data.get('spec_value'):
                raise HTTPException(status_code=400, detail="多规格卡券必须提供规格名称和规格值")

        success = db_manager.update_card(
            card_id=card_id,
            name=card_data.get('name'),
            card_type=card_data.get('type'),
            api_config=card_data.get('api_config'),
            text_content=card_data.get('text_content'),
            data_content=card_data.get('data_content'),
            image_url=card_data.get('image_url'),
            description=card_data.get('description'),
            enabled=card_data.get('enabled', True),
            delay_seconds=card_data.get('delay_seconds'),
            is_multi_spec=is_multi_spec,
            spec_name=card_data.get('spec_name'),
            spec_value=card_data.get('spec_value')
        )
        if success:
            return {"message": "卡券更新成功"}
        else:
            raise HTTPException(status_code=404, detail="卡券不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.put("/cards/{card_id}/image")
async def update_card_with_image(
    card_id: int,
    image: UploadFile = File(...),
    name: str = Form(...),
    type: str = Form(...),
    description: str = Form(default=""),
    delay_seconds: int = Form(default=0),
    enabled: bool = Form(default=True),
    is_multi_spec: bool = Form(default=False),
    spec_name: str = Form(default=""),
    spec_value: str = Form(default=""),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """更新带图片的卡券"""
    try:
        logger.info(f"接收到带图片的卡券更新请求: card_id={card_id}, name={name}, type={type}")

        # 验证图片文件
        if not image.content_type or not image.content_type.startswith('image/'):
            logger.warning(f"无效的图片文件类型: {image.content_type}")
            raise HTTPException(status_code=400, detail="请上传图片文件")

        # 验证多规格字段
        if is_multi_spec:
            if not spec_name or not spec_value:
                raise HTTPException(status_code=400, detail="多规格卡券必须提供规格名称和规格值")

        # 读取图片数据
        image_data = await image.read()
        logger.info(f"读取图片数据成功，大小: {len(image_data)} bytes")

        # 保存图片
        image_url = image_manager.save_image(image_data, image.filename)
        if not image_url:
            logger.error("图片保存失败")
            raise HTTPException(status_code=400, detail="图片保存失败")

        logger.info(f"图片保存成功: {image_url}")

        # 更新卡券
        from db_manager import db_manager
        success = db_manager.update_card(
            card_id=card_id,
            name=name,
            card_type=type,
            image_url=image_url,
            description=description,
            enabled=enabled,
            delay_seconds=delay_seconds,
            is_multi_spec=is_multi_spec,
            spec_name=spec_name if is_multi_spec else None,
            spec_value=spec_value if is_multi_spec else None
        )

        if success:
            logger.info(f"卡券更新成功: {name} (ID: {card_id})")
            return {"message": "卡券更新成功", "image_url": image_url}
        else:
            # 如果数据库更新失败，删除已保存的图片
            image_manager.delete_image(image_url)
            raise HTTPException(status_code=404, detail="卡券不存在")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新带图片的卡券失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# 自动发货规则API
@content_router.get("/delivery-rules")
def get_delivery_rules(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取发货规则列表"""
    try:
        from db_manager import db_manager
        user_id = current_user['user_id']
        rules = db_manager.get_all_delivery_rules(user_id)
        return rules
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.post("/delivery-rules")
def create_delivery_rule(rule_data: dict, current_user: Dict[str, Any] = Depends(get_current_user)):
    """创建新发货规则"""
    try:
        from db_manager import db_manager
        user_id = current_user['user_id']
        rule_id = db_manager.create_delivery_rule(
            keyword=rule_data.get('keyword'),
            card_id=rule_data.get('card_id'),
            delivery_count=rule_data.get('delivery_count', 1),
            enabled=rule_data.get('enabled', True),
            description=rule_data.get('description'),
            user_id=user_id
        )
        return {"id": rule_id, "message": "发货规则创建成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.get("/delivery-rules/{rule_id}")
def get_delivery_rule(rule_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取单个发货规则详情"""
    try:
        from db_manager import db_manager
        user_id = current_user['user_id']
        rule = db_manager.get_delivery_rule_by_id(rule_id, user_id)
        if rule:
            return rule
        else:
            raise HTTPException(status_code=404, detail="发货规则不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.put("/delivery-rules/{rule_id}")
def update_delivery_rule(rule_id: int, rule_data: dict, current_user: Dict[str, Any] = Depends(get_current_user)):
    """更新发货规则"""
    try:
        from db_manager import db_manager
        user_id = current_user['user_id']
        success = db_manager.update_delivery_rule(
            rule_id=rule_id,
            keyword=rule_data.get('keyword'),
            card_id=rule_data.get('card_id'),
            delivery_count=rule_data.get('delivery_count', 1),
            enabled=rule_data.get('enabled', True),
            description=rule_data.get('description'),
            user_id=user_id
        )
        if success:
            return {"message": "发货规则更新成功"}
        else:
            raise HTTPException(status_code=404, detail="发货规则不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.delete("/cards/{card_id}")
def delete_card(card_id: int, _: None = Depends(require_auth)):
    """删除卡券"""
    try:
        from db_manager import db_manager
        success = db_manager.delete_card(card_id)
        if success:
            return {"message": "卡券删除成功"}
        else:
            raise HTTPException(status_code=404, detail="卡券不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@content_router.delete("/delivery-rules/{rule_id}")
def delete_delivery_rule(rule_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    """删除发货规则"""
    try:
        from db_manager import db_manager
        user_id = current_user['user_id']
        success = db_manager.delete_delivery_rule(rule_id, user_id)
        if success:
            return {"message": "发货规则删除成功"}
        else:
            raise HTTPException(status_code=404, detail="发货规则不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 备份和恢复 API ====================

@admin_router.get("/backup/export")
def export_backup(current_user: Dict[str, Any] = Depends(get_current_user)):
    """导出用户备份"""
    try:
        from db_manager import db_manager
        user_id = current_user['user_id']
        username = current_user['username']

        # 导出当前用户的数据
        backup_data = db_manager.export_backup(user_id)

        # 生成文件名
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"xianyu_backup_{username}_{timestamp}.json"

        # 返回JSON响应，设置下载头
        response = JSONResponse(content=backup_data)
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        response.headers["Content-Type"] = "application/json"

        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出备份失败: {str(e)}")


@admin_router.post("/backup/import")
def import_backup(file: UploadFile = File(...), current_user: Dict[str, Any] = Depends(get_current_user)):
    """导入用户备份"""
    try:
        # 验证文件类型
        if not file.filename.endswith('.json'):
            raise HTTPException(status_code=400, detail="只支持JSON格式的备份文件")

        # 读取文件内容
        content = file.file.read()
        backup_data = json.loads(content.decode('utf-8'))

        # 导入备份到当前用户
        from db_manager import db_manager
        user_id = current_user['user_id']
        success = db_manager.import_backup(backup_data, user_id)

        if success:
            # 备份导入成功后，刷新 CookieManager 的内存缓存
            import cookie_manager
            if cookie_manager.manager:
                try:
                    cookie_manager.manager.reload_from_db()
                    logger.info("备份导入后已刷新 CookieManager 缓存")
                except Exception as e:
                    logger.error(f"刷新 CookieManager 缓存失败: {e}")

            return {"message": "备份导入成功"}
        else:
            raise HTTPException(status_code=400, detail="备份导入失败")

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="备份文件格式无效")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导入备份失败: {str(e)}")


@admin_router.post("/system/reload-cache")
def reload_cache(_: None = Depends(require_auth)):
    """重新加载系统缓存（用于手动刷新数据）"""
    try:
        import cookie_manager
        if cookie_manager.manager:
            success = cookie_manager.manager.reload_from_db()
            if success:
                return {"message": "系统缓存已刷新", "success": True}
            else:
                raise HTTPException(status_code=500, detail="缓存刷新失败")
        else:
            raise HTTPException(status_code=500, detail="CookieManager 未初始化")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新缓存失败: {str(e)}")


# ==================== 商品管理 API ====================

@content_router.get("/items")
def get_all_items(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取当前用户的所有商品信息"""
    try:
        # 只返回当前用户的商品信息
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        all_items = []
        for cookie_id in user_cookies.keys():
            items = db_manager.get_items_by_cookie(cookie_id)
            all_items.extend(items)

        return {"items": all_items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取商品信息失败: {str(e)}")


# ==================== 商品搜索 API ====================

class ItemSearchRequest(BaseModel):
    keyword: str
    page: int = 1
    page_size: int = 20

class ItemSearchMultipleRequest(BaseModel):
    keyword: str
    total_pages: int = 1

@content_router.post("/items/search")
async def search_items(
    search_request: ItemSearchRequest,
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)
):
    """搜索闲鱼商品"""
    user_info = f"【{current_user.get('username', 'unknown')}#{current_user.get('user_id', 'unknown')}】" if current_user else "【未登录】"

    try:
        logger.info(f"{user_info} 开始单页搜索: 关键词='{search_request.keyword}', 页码={search_request.page}, 每页={search_request.page_size}")

        from utils.item_search import search_xianyu_items

        # 执行搜索
        result = await search_xianyu_items(
            keyword=search_request.keyword,
            page=search_request.page,
            page_size=search_request.page_size
        )

        # 检查是否有错误
        has_error = result.get("error")
        items_count = len(result.get("items", []))

        logger.info(f"{user_info} 单页搜索完成: 获取到 {items_count} 条数据" +
                   (f", 错误: {has_error}" if has_error else ""))

        response_data = {
            "success": True,
            "data": result.get("items", []),
            "total": result.get("total", 0),
            "page": search_request.page,
            "page_size": search_request.page_size,
            "keyword": search_request.keyword,
            "is_real_data": result.get("is_real_data", False),
            "source": result.get("source", "unknown")
        }

        # 如果有错误信息，也包含在响应中
        if has_error:
            response_data["error"] = has_error

        return response_data

    except Exception as e:
        error_msg = str(e)
        logger.error(f"{user_info} 商品搜索失败: {error_msg}")
        raise HTTPException(status_code=500, detail=f"商品搜索失败: {error_msg}")


@accounts_router.get("/cookies/check")
async def check_valid_cookies(
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)
):
    """检查是否有有效的cookies账户（必须是启用状态）"""
    try:
        if cookie_manager.manager is None:
            return {
                "success": True,
                "hasValidCookies": False,
                "validCount": 0,
                "enabledCount": 0,
                "totalCount": 0
            }

        from db_manager import db_manager

        # 获取所有cookies
        all_cookies = db_manager.get_all_cookies()

        # 检查启用状态和有效性
        valid_cookies = []
        enabled_cookies = []

        for cookie_id, cookie_value in all_cookies.items():
            # 检查是否启用
            is_enabled = cookie_manager.manager.get_cookie_status(cookie_id)
            if is_enabled:
                enabled_cookies.append(cookie_id)
                # 检查是否有效（长度大于50）
                if len(cookie_value) > 50:
                    valid_cookies.append(cookie_id)

        return {
            "success": True,
            "hasValidCookies": len(valid_cookies) > 0,
            "validCount": len(valid_cookies),
            "enabledCount": len(enabled_cookies),
            "totalCount": len(all_cookies)
        }

    except Exception as e:
        logger.error(f"检查cookies失败: {str(e)}")
        return {
            "success": False,
            "hasValidCookies": False,
            "error": str(e)
        }

@content_router.post("/items/search_multiple")
async def search_multiple_pages(
    search_request: ItemSearchMultipleRequest,
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)
):
    """搜索多页闲鱼商品"""
    user_info = f"【{current_user.get('username', 'unknown')}#{current_user.get('user_id', 'unknown')}】" if current_user else "【未登录】"

    try:
        logger.info(f"{user_info} 开始多页搜索: 关键词='{search_request.keyword}', 页数={search_request.total_pages}")

        from utils.item_search import search_multiple_pages_xianyu

        # 执行多页搜索
        result = await search_multiple_pages_xianyu(
            keyword=search_request.keyword,
            total_pages=search_request.total_pages
        )

        # 检查是否有错误
        has_error = result.get("error")
        items_count = len(result.get("items", []))

        logger.info(f"{user_info} 多页搜索完成: 获取到 {items_count} 条数据" +
                   (f", 错误: {has_error}" if has_error else ""))

        response_data = {
            "success": True,
            "data": result.get("items", []),
            "total": result.get("total", 0),
            "total_pages": search_request.total_pages,
            "keyword": search_request.keyword,
            "is_real_data": result.get("is_real_data", False),
            "is_fallback": result.get("is_fallback", False),
            "source": result.get("source", "unknown")
        }

        # 如果有错误信息，也包含在响应中
        if has_error:
            response_data["error"] = has_error

        return response_data

    except Exception as e:
        error_msg = str(e)
        logger.error(f"{user_info} 多页商品搜索失败: {error_msg}")
        raise HTTPException(status_code=500, detail=f"多页商品搜索失败: {error_msg}")



@content_router.get("/items/cookie/{cookie_id}")
def get_items_by_cookie(cookie_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取指定Cookie的商品信息"""
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限访问该Cookie")

        items = db_manager.get_items_by_cookie(cookie_id)
        return {"items": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取商品信息失败: {str(e)}")


@content_router.get("/items/{cookie_id}/{item_id}")
def get_item_detail(cookie_id: str, item_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取商品详情"""
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限访问该Cookie")

        item = db_manager.get_item_info(cookie_id, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="商品不存在")
        return {"item": item}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取商品详情失败: {str(e)}")


class ItemDetailUpdate(BaseModel):
    item_detail: str


@content_router.put("/items/{cookie_id}/{item_id}")
def update_item_detail(
    cookie_id: str,
    item_id: str,
    update_data: ItemDetailUpdate,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """更新商品详情"""
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        success = db_manager.update_item_detail(cookie_id, item_id, update_data.item_detail)
        if success:
            return {"message": "商品详情更新成功"}
        else:
            raise HTTPException(status_code=400, detail="更新失败")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新商品详情失败: {str(e)}")


@content_router.delete("/items/{cookie_id}/{item_id}")
def delete_item_info(
    cookie_id: str,
    item_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """删除商品信息"""
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        success = db_manager.delete_item_info(cookie_id, item_id)
        if success:
            return {"message": "商品信息删除成功"}
        else:
            raise HTTPException(status_code=404, detail="商品信息不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除商品信息异常: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


class BatchDeleteRequest(BaseModel):
    items: List[dict]  # [{"cookie_id": "xxx", "item_id": "yyy"}, ...]


class AIReplySettings(BaseModel):
    ai_enabled: bool
    provider_profile_id: Optional[int] = None
    model_name: str = "deepseek-v4-flash"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    max_discount_percent: int = 10
    max_discount_amount: int = 100
    max_bargain_rounds: int = 3
    custom_prompts: str = ""
    api_key_action: str = "keep"
    provider_test_token: str = ""


class AIProviderProfileCreate(BaseModel):
    name: str
    provider_type: str = "openai_compatible"
    preset: str = "custom"
    base_url: str = ""
    api_key: str = ""
    default_model: str = ""
    is_default: bool = False


class AIProviderProfileUpdate(BaseModel):
    name: Optional[str] = None
    provider_type: Optional[str] = None
    preset: Optional[str] = None
    base_url: Optional[str] = None
    api_key: str = ""
    api_key_action: str = "keep"
    default_model: Optional[str] = None
    is_default: Optional[bool] = None


class AIProviderTestRequest(BaseModel):
    model_name: str


class AIReplyLabRequest(BaseModel):
    session_id: Optional[str] = None
    message: str
    item_id: Optional[str] = None
    item_title: str = "测试商品"
    item_price: Any = 100
    item_desc: str = "这是一个测试商品"
    training_rules: List[Any] = Field(default_factory=list)
    prompt_override: str = ""


class AIReplyLabSaveRequest(BaseModel):
    item_id: str = ""
    training_rules: List[Any] = Field(default_factory=list)


class AITrainingRuleStatusRequest(BaseModel):
    enabled: bool


class AIItemKnowledgeDraftRequest(BaseModel):
    profile: Dict[str, Any] = Field(default_factory=dict)


class AIItemKnowledgeGenerateRequest(BaseModel):
    overview: str = ""
    profile: Dict[str, Any] = Field(default_factory=dict)


class AIItemKnowledgeCopyRequest(BaseModel):
    target_item_ids: List[str] = Field(default_factory=list)
    overwrite: bool = False


class SkillMonitorTaskIn(BaseModel):
    name: str = ""
    keyword: str
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    region: str = ""
    published_within_hours: int = 24
    ai_filter: str = ""
    notify_enabled: bool = False
    account_id: str = ""
    enabled: bool = True
    schedule_enabled: bool = False
    schedule_interval_minutes: int = 60
    next_run_at: Optional[str] = None


class SkillMonitorTaskUpdate(BaseModel):
    name: Optional[str] = None
    keyword: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    region: Optional[str] = None
    published_within_hours: Optional[int] = None
    ai_filter: Optional[str] = None
    notify_enabled: Optional[bool] = None
    account_id: Optional[str] = None
    enabled: Optional[bool] = None
    schedule_enabled: Optional[bool] = None
    schedule_interval_minutes: Optional[int] = None
    next_run_at: Optional[str] = None


class SkillAgentPromptIn(BaseModel):
    prompt_type: str
    title: str = ""
    content: str
    enabled: bool = True


class SkillAgentTestIn(BaseModel):
    message: str
    cookie_id: str = ""
    item_id: str = ""


def _mask_secret(value: str) -> str:
    """Return a display-safe secret preview without exposing the stored value."""
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}***{value[-4:]}"


@content_router.delete("/items/batch")
def batch_delete_items(
    request: BatchDeleteRequest,
    _: None = Depends(require_auth)
):
    """批量删除商品信息"""
    try:
        if not request.items:
            raise HTTPException(status_code=400, detail="删除列表不能为空")

        success_count = db_manager.batch_delete_item_info(request.items)
        total_count = len(request.items)

        return {
            "message": f"批量删除完成",
            "success_count": success_count,
            "total_count": total_count,
            "failed_count": total_count - success_count
        }
    except Exception as e:
        logger.error(f"批量删除商品信息异常: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


# ==================== AI回复管理API ====================

AI_TRAINING_SECTION_TITLE = "AI训练修正"
AI_TRAINING_MARKER = f"【{AI_TRAINING_SECTION_TITLE}】"
AI_REPLY_RISK_PHRASES = [
    "登录我发给你的邮箱",
    "登录我发给您的邮箱",
    "登录我发的邮箱",
    "登录卖家的邮箱",
    "买邮箱",
    "买的是邮箱",
    "发密码",
    "验证码发我",
    "我帮你登录",
    "我帮您登录",
    "我帮你查询账号",
    "我帮您查询账号",
]


def _dedupe_rules(rules: List[str]) -> List[str]:
    seen = set()
    cleaned = []
    for rule in rules or []:
        text = str(rule or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _normalize_scoped_rules(rules: List[Any], default_scope: str = 'item') -> List[Dict[str, str]]:
    seen = set()
    cleaned = []
    for rule in rules or []:
        if isinstance(rule, dict):
            scope = str(rule.get('scope') or default_scope).strip().lower()
            text = str(rule.get('text') or '').strip()
        else:
            scope = default_scope
            text = str(rule or '').strip()
        if scope not in {'global', 'item'} or not text:
            continue
        key = (scope, text)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({'scope': scope, 'text': text})
    return cleaned


def _detect_ai_reply_warnings(reply: str) -> List[str]:
    return [phrase for phrase in AI_REPLY_RISK_PHRASES if phrase in (reply or "")]


def _extract_custom_prompt_text(raw: str) -> str:
    raw = (raw or '').strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except Exception:
        return raw

    if isinstance(parsed, str):
        return parsed.strip()
    if isinstance(parsed, dict):
        for key in ('default', 'price', 'tech'):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return raw


def _merge_training_rules_into_prompt(raw_prompt: str, rules: List[str]) -> str:
    rules = _dedupe_rules(rules)
    base_text = _extract_custom_prompt_text(raw_prompt)
    existing_rules: List[str] = []

    if AI_TRAINING_MARKER in base_text:
        before, after = base_text.split(AI_TRAINING_MARKER, 1)
        base_text = before.strip()
        existing_rules = [
            line.strip().lstrip('-').strip()
            for line in after.splitlines()
            if line.strip().lstrip('-').strip()
        ]

    merged_rules = _dedupe_rules(existing_rules + rules)
    training_block = ""
    if merged_rules:
        training_block = f"\n\n{AI_TRAINING_MARKER}\n" + "\n".join([f"- {rule}" for rule in merged_rules])

    merged_text = f"{base_text}{training_block}".strip()
    return json.dumps({
        "default": merged_text,
        "price": merged_text,
        "tech": merged_text,
    }, ensure_ascii=False)


def _ensure_ai_cookie_access(cookie_id: str, current_user: Dict[str, Any]):
    user_id = current_user['user_id']
    user_cookies = db_manager.get_all_cookies(user_id)
    if cookie_id not in user_cookies:
        raise HTTPException(status_code=403, detail="无权限访问该Cookie")
    if cookie_manager.manager is None:
        raise HTTPException(status_code=500, detail='CookieManager 未就绪')
    if cookie_id not in cookie_manager.manager.cookies:
        raise HTTPException(status_code=404, detail='账号不存在')


def _get_ai_knowledge_item(cookie_id: str, item_id: str, current_user: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_ai_cookie_access(cookie_id, current_user)
    item = db_manager.get_item_info(cookie_id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail='当前账号中找不到这个商品，请先同步商品')
    return item


def _item_knowledge_source_hash(item: Dict[str, Any]) -> str:
    source = json.dumps({
        'title': item.get('item_title') or '',
        'price': item.get('item_price') or '',
        'detail': item.get('item_detail') or item.get('item_description') or '',
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def _item_knowledge_payload(cookie_id: str, item_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
    profile = db_manager.get_ai_item_knowledge_profile(cookie_id, item_id)
    current_hash = _item_knowledge_source_hash(item)
    return {
        **profile,
        'item': {
            'item_id': item_id,
            'title': item.get('item_title') or '',
            'price': item.get('item_price') or '',
            'detail': item.get('item_detail') or item.get('item_description') or '',
            'updated_at': item.get('updated_at'),
        },
        'current_source_hash': current_hash,
        'source_changed': bool(profile.get('source_detail_hash') and profile.get('source_detail_hash') != current_hash),
    }


def _normalize_provider_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    preset = str(data.get('preset') or 'custom').strip().lower()
    preset_data = PROVIDER_PRESETS.get(preset, PROVIDER_PRESETS['custom'])
    provider_type = str(data.get('provider_type') or preset_data['provider_type']).strip()
    if provider_type not in {'openai_compatible', 'gemini'}:
        raise HTTPException(status_code=400, detail='平台类型仅支持 OpenAI 兼容接口或 Gemini')
    name = str(data.get('name') or preset_data['label']).strip()
    base_url = str(data.get('base_url') or preset_data['base_url']).strip().rstrip('/')
    default_model = str(data.get('default_model') or preset_data['default_model']).strip()
    if not name:
        raise HTTPException(status_code=400, detail='平台名称不能为空')
    if not base_url:
        raise HTTPException(status_code=400, detail='API 地址不能为空')
    if not re.match(r'^https?://', base_url, re.IGNORECASE):
        raise HTTPException(status_code=400, detail='API 地址必须以 http:// 或 https:// 开头')
    return {**data, 'name': name, 'preset': preset, 'provider_type': provider_type,
            'base_url': base_url, 'default_model': default_model}


def _provider_public_payload(profile: Dict[str, Any]) -> Dict[str, Any]:
    cached_at = profile.get('models_cached_at')
    return {
        **profile,
        'models_cache_fresh': bool(cached_at and time.time() - float(cached_at) < 86400),
    }


@ai_router.get('/api/ai/providers')
def list_ai_providers(current_user: Dict[str, Any] = Depends(get_current_user)):
    user_id = current_user['user_id']
    db_manager.ensure_legacy_ai_provider_profiles(user_id)
    return {
        'providers': [_provider_public_payload(item) for item in db_manager.list_ai_provider_profiles(user_id)],
        'presets': PROVIDER_PRESETS,
    }


@ai_router.post('/api/ai/providers')
def create_ai_provider(payload: AIProviderProfileCreate, current_user: Dict[str, Any] = Depends(get_current_user)):
    user_id = current_user['user_id']
    data = _normalize_provider_payload(payload.dict())
    if not data.get('api_key'):
        raise HTTPException(status_code=400, detail='新平台必须填写 API Key')
    if not db_manager.list_ai_provider_profiles(user_id):
        data['is_default'] = True
    try:
        profile_id = db_manager.create_ai_provider_profile(user_id, data)
    except Exception as e:
        if 'UNIQUE constraint failed' in str(e):
            raise HTTPException(status_code=409, detail='平台名称已存在')
        raise HTTPException(status_code=400, detail='平台配置创建失败')
    return _provider_public_payload(db_manager.get_ai_provider_profile(profile_id, user_id))


@ai_router.put('/api/ai/providers/{profile_id}')
def update_ai_provider(profile_id: int, payload: AIProviderProfileUpdate,
                       current_user: Dict[str, Any] = Depends(get_current_user)):
    user_id = current_user['user_id']
    current = db_manager.get_ai_provider_profile(profile_id, user_id)
    if not current:
        raise HTTPException(status_code=404, detail='平台配置不存在')
    merged = _normalize_provider_payload({**current, **payload.dict(exclude_none=True)})
    try:
        return _provider_public_payload(db_manager.update_ai_provider_profile(profile_id, user_id, merged))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@ai_router.delete('/api/ai/providers/{profile_id}')
def delete_ai_provider(profile_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    try:
        if not db_manager.delete_ai_provider_profile(profile_id, current_user['user_id']):
            raise HTTPException(status_code=404, detail='平台配置不存在')
        return {'message': '平台配置已删除'}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@ai_router.post('/api/ai/providers/{profile_id}/models/refresh')
def refresh_ai_provider_models(profile_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    user_id = current_user['user_id']
    profile = db_manager.get_ai_provider_profile(profile_id, user_id, include_secret=True)
    if not profile:
        raise HTTPException(status_code=404, detail='平台配置不存在')
    try:
        models = discover_provider_models(profile)
        db_manager.update_ai_provider_models(profile_id, user_id, models)
        return {'models': models, 'cached_at': time.time()}
    except Exception as e:
        logger.warning(f'平台模型列表刷新失败 profile={profile_id}: {type(e).__name__}')
        raise HTTPException(status_code=400, detail='模型列表读取失败，可手动填写模型 ID 后测试')


@ai_router.post('/api/ai/providers/{profile_id}/test')
def test_ai_provider(profile_id: int, payload: AIProviderTestRequest,
                     current_user: Dict[str, Any] = Depends(get_current_user)):
    user_id = current_user['user_id']
    profile = db_manager.get_ai_provider_profile(profile_id, user_id, include_secret=True)
    if not profile:
        raise HTTPException(status_code=404, detail='平台配置不存在')
    model_name = payload.model_name.strip()
    try:
        reply = test_provider_reply(profile, model_name)
        if not reply:
            raise ValueError('模型返回空内容')
        db_manager.update_ai_provider_verification(profile_id, user_id, 'verified', '测试回复生成成功')
        token = provider_test_tokens.issue(user_id, profile_id, model_name)
        return {
            'message': '测试回复生成成功，可以应用到账号',
            'reply': reply,
            'test_token': token,
            'model_name': model_name,
        }
    except Exception as e:
        db_manager.update_ai_provider_verification(profile_id, user_id, 'failed', '测试回复生成失败')
        logger.warning(f'AI平台测试失败 profile={profile_id} model={model_name}: {type(e).__name__}')
        raise HTTPException(status_code=400, detail='测试回复生成失败，请检查平台、Key、地址和模型 ID')


@ai_router.get("/ai-reply-settings/{cookie_id}")
def get_ai_reply_settings(cookie_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取指定账号的AI回复设置"""
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限访问该Cookie")

        db_manager.ensure_legacy_ai_provider_profiles(user_id)
        settings = db_manager.get_ai_reply_settings(cookie_id)
        account_api_key = ''
        with db_manager.lock:
            cursor = db_manager.conn.cursor()
            cursor.execute("SELECT api_key FROM ai_reply_settings WHERE cookie_id = ?", (cookie_id,))
            row = cursor.fetchone()
            account_api_key = row[0] if row and row[0] else ''

        system_api_key = db_manager.get_system_setting('ai_api_key') or ''
        profile = db_manager.get_ai_provider_profile(settings.get('provider_profile_id'), user_id)
        effective_key = settings.get('api_key') or account_api_key or system_api_key
        if profile:
            api_key_source = 'provider'
            api_key_masked = profile.get('api_key_masked', '')
        elif account_api_key:
            api_key_source = 'account'
            api_key_masked = _mask_secret(effective_key)
        elif system_api_key:
            api_key_source = 'global'
            api_key_masked = _mask_secret(effective_key)
        else:
            api_key_source = 'missing'
            api_key_masked = ''

        settings.update({
            'api_key': '',
            'api_key_source': api_key_source,
            'api_key_masked': api_key_masked,
            'has_effective_api_key': bool(effective_key),
        })
        return settings
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取AI回复设置异常: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@ai_router.put("/ai-reply-settings/{cookie_id}")
def update_ai_reply_settings(cookie_id: str, settings: AIReplySettings, current_user: Dict[str, Any] = Depends(get_current_user)):
    """更新指定账号的AI回复设置"""
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        # 检查账号是否存在
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail='CookieManager 未就绪')

        db_manager.ensure_legacy_ai_provider_profiles(user_id)
        current_settings = db_manager.get_ai_reply_settings(cookie_id)
        requested_profile_id = settings.provider_profile_id or current_settings.get('provider_profile_id')
        if requested_profile_id is not None:
            profile = db_manager.get_ai_provider_profile(requested_profile_id, user_id)
            if not profile:
                raise HTTPException(status_code=404, detail='所选 AI 平台不存在')
            provider_changed = int(current_settings.get('provider_profile_id') or 0) != int(requested_profile_id)
            model_changed = str(current_settings.get('model_name') or '') != settings.model_name
            if provider_changed or model_changed:
                valid_test = provider_test_tokens.consume(
                    settings.provider_test_token, user_id, requested_profile_id, settings.model_name
                )
                if not valid_test:
                    raise HTTPException(status_code=409, detail='请先用所选平台和模型生成测试回复，成功后再应用')

        # 明确处理旧版账号专属Key：空值默认保留，只有clear才删除。
        settings_dict = settings.dict()
        settings_dict['provider_profile_id'] = requested_profile_id
        with db_manager.lock:
            row = db_manager.conn.execute(
                "SELECT api_key FROM ai_reply_settings WHERE cookie_id = ?", (cookie_id,)
            ).fetchone()
        existing_api_key = row[0] if row and row[0] else ''
        try:
            settings_dict['api_key'] = apply_secret_action(
                existing_api_key, settings.api_key_action, settings.api_key
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        settings_dict.pop('api_key_action', None)
        settings_dict.pop('provider_test_token', None)
        success = db_manager.save_ai_reply_settings(cookie_id, settings_dict)

        if success:

            # 如果启用了AI回复，记录日志
            if settings.ai_enabled:
                logger.info(f"账号 {cookie_id} 启用AI回复")
            else:
                logger.info(f"账号 {cookie_id} 禁用AI回复")

            return {"message": "AI回复设置更新成功"}
        else:
            raise HTTPException(status_code=400, detail="更新失败")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新AI回复设置异常: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@ai_router.get("/ai-reply-settings")
def get_all_ai_reply_settings(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取当前用户所有账号的AI回复设置"""
    try:
        # 只返回当前用户的AI回复设置
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)
        db_manager.ensure_legacy_ai_provider_profiles(user_id)

        all_settings = db_manager.get_all_ai_reply_settings()
        # 过滤只属于当前用户的设置
        user_settings = {}
        system_api_key = db_manager.get_system_setting('ai_api_key') or ''
        for cid, raw_settings in all_settings.items():
            if cid not in user_cookies:
                continue
            settings = db_manager.get_ai_reply_settings(cid)
            account_api_key = raw_settings.get('api_key') or ''
            effective_key = account_api_key or system_api_key
            profile = db_manager.get_ai_provider_profile(settings.get('provider_profile_id'), user_id)
            settings = dict(settings)
            settings.update({
                'api_key': '',
                'api_key_source': 'provider' if profile else ('account' if account_api_key else ('global' if system_api_key else 'missing')),
                'api_key_masked': profile.get('api_key_masked', '') if profile else _mask_secret(effective_key),
                'has_effective_api_key': bool(profile.get('api_key_configured')) if profile else bool(effective_key),
            })
            user_settings[cid] = settings
        return user_settings
    except Exception as e:
        logger.error(f"获取所有AI回复设置异常: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@ai_router.post("/ai-reply-test/{cookie_id}")
def test_ai_reply(
    cookie_id: str,
    test_data: dict,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """测试AI回复功能"""
    try:
        _ensure_ai_cookie_access(cookie_id, current_user)
        # 检查账号是否存在
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail='CookieManager 未就绪')

        if cookie_id not in cookie_manager.manager.cookies:
            raise HTTPException(status_code=404, detail='账号不存在')

        # 检查是否启用AI回复
        if not ai_reply_engine.is_ai_enabled(cookie_id):
            raise HTTPException(status_code=400, detail='该账号未启用AI回复')

        # 检查AI设置是否完整
        settings = db_manager.get_ai_reply_settings(cookie_id)
        if not settings.get('api_key'):
            raise HTTPException(status_code=400, detail='未配置API Key，请先在AI设置中配置API Key')
        if not settings.get('base_url'):
            raise HTTPException(status_code=400, detail='未配置API地址，请先在AI设置中配置API地址')

        # 构造测试数据
        test_message = test_data.get('message', '你好')
        test_item_info = {
            'title': test_data.get('item_title', '测试商品'),
            'price': test_data.get('item_price', 100),
            'desc': test_data.get('item_desc', '这是一个测试商品')
        }

        # 生成测试回复（跳过等待时间）
        reply = ai_reply_engine.generate_reply(
            message=test_message,
            item_info=test_item_info,
            chat_id=f"test_{int(time.time())}",
            cookie_id=cookie_id,
            user_id="test_user",
            item_id="test_item",
            skip_wait=True  # 测试时跳过10秒等待
        )

        if reply:
            return {"message": "测试成功", "reply": reply}
        else:
            raise HTTPException(status_code=400, detail="AI回复生成失败，请检查API Key是否正确、API地址是否可访问")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"测试AI回复异常: {e}")
        import traceback
        logger.error(f"详细错误: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@ai_router.post("/ai-reply-lab/reply/{cookie_id}")
def ai_reply_lab_reply(cookie_id: str, request: AIReplyLabRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    """AI训练实验室回复，不污染正式对话记录和线上提示词。"""
    try:
        _ensure_ai_cookie_access(cookie_id, current_user)

        settings = db_manager.get_ai_reply_settings(cookie_id)
        if not settings.get('ai_enabled'):
            raise HTTPException(status_code=400, detail='该账号未启用AI回复')
        if not settings.get('api_key'):
            raise HTTPException(status_code=400, detail='未配置API Key，请先在AI设置中配置API Key')
        if not settings.get('base_url'):
            raise HTTPException(status_code=400, detail='未配置API地址，请先在AI设置中配置API地址')

        message = (request.message or '').strip()
        if not message:
            raise HTTPException(status_code=400, detail='买家消息不能为空')

        item_title = request.item_title or '测试商品'
        item_price = request.item_price if request.item_price not in (None, '') else 100
        item_desc = request.item_desc or '暂无商品描述'

        if request.item_id:
            db_item = db_manager.get_item_info(cookie_id, request.item_id)
            if not db_item:
                raise HTTPException(status_code=404, detail='当前账号中找不到这个商品，请先同步商品')
            item_title = db_item.get('item_title') or item_title
            item_price = db_item.get('item_price') or item_price
            item_desc = db_item.get('item_detail') or db_item.get('item_description') or item_desc

        current_time = time.time()
        expired_sessions = [
            sid for sid, session in ai_reply_lab_sessions.items()
            if current_time - session.get('timestamp', 0) > 6 * 3600
        ]
        for sid in expired_sessions:
            ai_reply_lab_sessions.pop(sid, None)

        session_id = request.session_id or secrets.token_urlsafe(16)
        registry = get_session_registry()
        persisted = registry.get(session_id)
        if persisted and persisted.get('owner_user_id') != current_user['user_id']:
            raise HTTPException(status_code=403, detail='无权限访问该训练会话')
        session = ai_reply_lab_sessions.get(session_id)
        normalized_item_id = str(request.item_id or '')
        if (not session or session.get('cookie_id') != cookie_id
                or session.get('user_id') != current_user['user_id']
                or session.get('item_id') != normalized_item_id):
            session = {
                'cookie_id': cookie_id,
                'user_id': current_user['user_id'],
                'item_id': normalized_item_id,
                'history': [],
                'timestamp': current_time,
            }
            ai_reply_lab_sessions[session_id] = session
            registry.register(
                session_id,
                "ai_training",
                current_user['user_id'],
                account_id=cookie_id,
                status="processing",
                ttl_seconds=6 * 3600,
                transient=session,
            )
        else:
            registry.update(session_id, status="processing", ttl_seconds=6 * 3600)

        history = session.get('history', [])
        reply_result = ai_reply_engine.generate_lab_reply(
            message=message,
            item_info={
                'title': item_title,
                'price': item_price,
                'desc': item_desc,
            },
            cookie_id=cookie_id,
            context=history,
            training_rules=_normalize_scoped_rules(request.training_rules),
            item_id=normalized_item_id,
            prompt_override=request.prompt_override,
            return_metadata=True,
        )

        if not reply_result or not reply_result.get('reply'):
            raise HTTPException(status_code=400, detail="AI回复生成失败，请检查API Key、API地址或训练规则")
        reply = reply_result['reply']

        history.extend([
            {'role': 'user', 'content': message},
            {'role': 'assistant', 'content': reply},
        ])
        session['history'] = history[-24:]
        session['timestamp'] = current_time
        registry.update(session_id, status="success", ttl_seconds=6 * 3600)

        return {
            "session_id": session_id,
            "reply": reply,
            "warnings": _detect_ai_reply_warnings(reply),
            "history": session['history'],
            "rule_context": reply_result.get('rule_context', {}),
            "rule_audit": reply_result.get('audit', {}),
            "regenerated": bool(reply_result.get('regenerated')),
            "guarded_by_rule": bool(reply_result.get('guarded_by_rule')),
            "guard_reason": reply_result.get('guard_reason', ''),
            "guarded_rule_ids": reply_result.get('guarded_rule_ids', []),
            "knowledge_source": reply_result.get('knowledge_source', 'none'),
            "knowledge_version": reply_result.get('knowledge_version', 0),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI训练实验室回复异常: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@ai_router.post("/ai-reply-lab/save/{cookie_id}")
def save_ai_reply_lab_rules(cookie_id: str, request: AIReplyLabSaveRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    """兼容旧前端：训练规则写入分层规则表，不再污染账号提示词。"""
    try:
        _ensure_ai_cookie_access(cookie_id, current_user)
        rules = _normalize_scoped_rules(request.training_rules)
        if not rules:
            raise HTTPException(status_code=400, detail='没有可保存的训练规则')
        saved = db_manager.save_ai_training_rules(cookie_id, request.item_id, rules)

        return {
            "message": "训练规则已按范围保存",
            "rules": saved,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存AI训练规则异常: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@ai_router.get("/ai-training-rules/{cookie_id}")
def get_ai_training_rules(cookie_id: str, item_id: str = Query(default=''), current_user: Dict[str, Any] = Depends(get_current_user)):
    _ensure_ai_cookie_access(cookie_id, current_user)
    rules = db_manager.get_ai_training_rules(cookie_id, item_id, include_disabled=True)
    return {**rules, 'context': db_manager.get_ai_training_rule_context(cookie_id, item_id)}


@ai_router.post("/ai-training-rules/{cookie_id}")
def save_ai_training_rules(cookie_id: str, request: AIReplyLabSaveRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    _ensure_ai_cookie_access(cookie_id, current_user)
    rules = _normalize_scoped_rules(request.training_rules)
    if not rules:
        raise HTTPException(status_code=400, detail='没有可保存的训练规则')
    saved = db_manager.save_ai_training_rules(cookie_id, request.item_id, rules)
    return {"message": "训练规则已保存", "rules": saved}


@ai_router.delete("/ai-training-rules/{cookie_id}/{rule_id}")
def delete_ai_training_rule(cookie_id: str, rule_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    _ensure_ai_cookie_access(cookie_id, current_user)
    if not db_manager.delete_ai_training_rule(cookie_id, rule_id):
        raise HTTPException(status_code=404, detail='训练规则不存在')
    return {"message": "训练规则已删除"}


@ai_router.patch("/ai-training-rules/{cookie_id}/{rule_id}")
def set_ai_training_rule_status(cookie_id: str, rule_id: int, request: AITrainingRuleStatusRequest,
                                current_user: Dict[str, Any] = Depends(get_current_user)):
    _ensure_ai_cookie_access(cookie_id, current_user)
    if not db_manager.set_ai_training_rule_enabled(cookie_id, rule_id, request.enabled):
        raise HTTPException(status_code=404, detail='训练规则不存在')
    return {"message": "训练规则状态已更新"}


@ai_router.get("/ai-item-knowledge/{cookie_id}/{item_id}")
def get_ai_item_knowledge(cookie_id: str, item_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    item = _get_ai_knowledge_item(cookie_id, item_id, current_user)
    return _item_knowledge_payload(cookie_id, item_id, item)


@ai_router.post("/ai-item-knowledge/{cookie_id}/{item_id}/generate")
def generate_ai_item_knowledge(cookie_id: str, item_id: str, request: AIItemKnowledgeGenerateRequest,
                               current_user: Dict[str, Any] = Depends(get_current_user)):
    item = _get_ai_knowledge_item(cookie_id, item_id, current_user)
    overview = str(request.overview or '').strip()
    seed = dict(request.profile) if isinstance(request.profile, dict) else {}
    if not overview:
        seed_overview = seed.get('overview') if isinstance(seed.get('overview'), dict) else {}
        overview = str(seed_overview.get('text') or '').strip()
    if not overview:
        raise HTTPException(status_code=400, detail='请先填写商品概览，再生成结构化草稿')
    seed['overview'] = {
        **(seed.get('overview') if isinstance(seed.get('overview'), dict) else {}),
        'text': overview,
        'source': 'user',
        'status': 'confirmed',
    }
    source_hash = _item_knowledge_source_hash(item)
    db_manager.save_ai_item_knowledge_draft(cookie_id, item_id, seed, source_hash)
    try:
        generated = ai_reply_engine.generate_item_knowledge_draft({
            'title': item.get('item_title') or '',
            'price': item.get('item_price') or '',
            'desc': item.get('item_detail') or item.get('item_description') or '',
        }, cookie_id, seller_overview=overview)
        draft = ai_reply_engine.merge_generated_knowledge_with_seed(seed, generated)
        db_manager.save_ai_item_knowledge_draft(cookie_id, item_id, draft, source_hash)
        return {
            'message': '概览已保存，AI结构化草稿已生成',
            'draft': draft,
            'source_detail_hash': source_hash,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"生成商品知识草稿失败 {cookie_id}/{item_id}: {e}")
        raise HTTPException(status_code=500, detail='AI草稿生成失败，请检查AI配置')


@ai_router.post("/ai-item-knowledge/{cookie_id}/{item_id}/copy")
def copy_ai_item_knowledge(cookie_id: str, item_id: str, request: AIItemKnowledgeCopyRequest,
                           current_user: Dict[str, Any] = Depends(get_current_user)):
    _get_ai_knowledge_item(cookie_id, item_id, current_user)
    if not request.target_item_ids:
        raise HTTPException(status_code=400, detail='请选择至少一个目标商品')
    try:
        result = db_manager.copy_ai_item_knowledge_draft(
            cookie_id, item_id, request.target_item_ids, request.overwrite
        )
        return {
            **result,
            'message': f"已复制到 {len(result['copied_item_ids'])} 个商品草稿",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@ai_router.put("/ai-item-knowledge/{cookie_id}/{item_id}/draft")
def save_ai_item_knowledge_draft(cookie_id: str, item_id: str, request: AIItemKnowledgeDraftRequest,
                                 current_user: Dict[str, Any] = Depends(get_current_user)):
    item = _get_ai_knowledge_item(cookie_id, item_id, current_user)
    try:
        db_manager.save_ai_item_knowledge_draft(
            cookie_id,
            item_id,
            request.profile,
            _item_knowledge_source_hash(item),
        )
        return {
            "message": "商品知识草稿已保存",
            **_item_knowledge_payload(cookie_id, item_id, item),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@ai_router.post("/ai-item-knowledge/{cookie_id}/{item_id}/publish")
def publish_ai_item_knowledge(cookie_id: str, item_id: str,
                              current_user: Dict[str, Any] = Depends(get_current_user)):
    item = _get_ai_knowledge_item(cookie_id, item_id, current_user)
    try:
        profile = db_manager.publish_ai_item_knowledge(cookie_id, item_id)
        return {
            "message": f"商品知识第 {profile['version']} 版已发布",
            **_item_knowledge_payload(cookie_id, item_id, item),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@ai_router.get("/ai-item-knowledge/{cookie_id}/{item_id}/versions")
def get_ai_item_knowledge_versions(cookie_id: str, item_id: str,
                                   current_user: Dict[str, Any] = Depends(get_current_user)):
    _get_ai_knowledge_item(cookie_id, item_id, current_user)
    return {'versions': db_manager.get_ai_item_knowledge_versions(cookie_id, item_id)}


@ai_router.post("/ai-item-knowledge/{cookie_id}/{item_id}/rollback/{version}")
def rollback_ai_item_knowledge(cookie_id: str, item_id: str, version: int,
                               current_user: Dict[str, Any] = Depends(get_current_user)):
    item = _get_ai_knowledge_item(cookie_id, item_id, current_user)
    try:
        profile = db_manager.rollback_ai_item_knowledge(cookie_id, item_id, version)
        return {
            "message": f"已回滚并发布为第 {profile['version']} 版",
            **_item_knowledge_payload(cookie_id, item_id, item),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _require_owned_cookie(cookie_id: str, user_id: int) -> None:
    if cookie_id not in db_manager.get_all_cookies(user_id):
        raise HTTPException(status_code=403, detail="无权限访问该闲鱼账号")


def _current_session_refresh_status(cookie_id: str) -> Dict[str, Any]:
    refresh_status = db_manager.get_account_session_refresh(cookie_id)
    if (
        refresh_status.get('state') in {'refreshing', 'verification_required'}
        and refresh_status.get('expires_at')
        and time.time() > float(refresh_status['expires_at'])
    ):
        image_path = (refresh_status.get('verification_image_url') or '').lstrip('/')
        remove_verification_image(image_path)
        db_manager.update_account_session_refresh(
            cookie_id,
            state='timeout',
            trigger=refresh_status.get('trigger') or 'automatic',
            message='身份验证已超时，请重新发起刷新',
            error_code='verification_timeout',
        )
        refresh_status = db_manager.get_account_session_refresh(cookie_id)
    return refresh_status


@accounts_router.get("/api/accounts/{cookie_id}/session-status")
def get_account_session_status(cookie_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    _require_owned_cookie(cookie_id, current_user['user_id'])
    status_data = _current_session_refresh_status(cookie_id)
    session_id = f"cookie-refresh:{cookie_id}"
    registry = get_session_registry()
    if registry.get(session_id):
        registry.update(
            session_id,
            status=status_data.get('state') or 'idle',
            error_code=status_data.get('error_code') or '',
            error_message=status_data.get('message') or '',
        )
    return {'success': True, 'data': status_data}


@accounts_router.post("/api/accounts/{cookie_id}/session-refresh")
async def refresh_account_session(cookie_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    _require_owned_cookie(cookie_id, current_user['user_id'])
    current_status = _current_session_refresh_status(cookie_id)
    if active_refresh_registry.is_active(cookie_id):
        return {'success': True, 'message': 'Cookie 刷新已经在进行中', 'data': current_status}

    if current_status.get('state') in {'refreshing', 'verification_required'}:
        db_manager.update_account_session_refresh(
            cookie_id, state='failed', trigger='manual',
            message='上一次刷新会话已中断，正在重新发起', error_code='interrupted',
        )

    from XianyuAutoAsync import XianyuLive
    live_instance = XianyuLive.get_instance(cookie_id)
    if live_instance is None:
        raise HTTPException(status_code=409, detail="账号监听实例未运行，请先开启账号监听")

    get_session_registry().register(
        f"cookie-refresh:{cookie_id}",
        "cookie_refresh",
        current_user['user_id'],
        account_id=cookie_id,
        status="refreshing",
        ttl_seconds=900,
        transient=live_instance,
    )

    manager_loop = getattr(cookie_manager.manager, 'loop', None) if cookie_manager.manager else None
    running_loop = asyncio.get_running_loop()
    if manager_loop and manager_loop is not running_loop and manager_loop.is_running():
        asyncio.run_coroutine_threadsafe(
            live_instance._try_password_login_refresh("手动立即刷新"),
            manager_loop,
        )
    else:
        asyncio.create_task(live_instance._try_password_login_refresh("手动立即刷新"))
    await asyncio.sleep(0)
    return {
        'success': True,
        'message': '已开始刷新 Cookie',
        'data': db_manager.get_account_session_refresh(cookie_id),
    }


@accounts_router.post("/api/accounts/{cookie_id}/session-refresh/cancel")
def cancel_account_session_refresh(cookie_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    _require_owned_cookie(cookie_id, current_user['user_id'])
    status_info = _current_session_refresh_status(cookie_id)
    image_path = (status_info.get('verification_image_url') or '').lstrip('/')
    cancelled = active_refresh_registry.cancel(cookie_id)
    remove_verification_image(image_path)
    db_manager.update_account_session_refresh(
        cookie_id, state='cancelled', trigger=status_info.get('trigger') or 'manual',
        message='Cookie 刷新已取消', error_code='cancelled',
    )
    get_session_registry().update(
        f"cookie-refresh:{cookie_id}",
        status='cancelled',
        error_code='cancelled',
        error_message='Cookie 刷新已取消',
    )
    return {'success': True, 'message': '刷新已取消' if cancelled else '没有正在运行的刷新任务'}


@accounts_router.get("/api/diagnostics/auto-reply/{cookie_id}")
def diagnose_auto_reply(cookie_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """诊断指定账号的自动回复链路"""
    try:
        user_id = current_user['user_id']
        user_cookies = db_manager.get_all_cookies(user_id)
        _require_owned_cookie(cookie_id, user_id)

        cookie_info = db_manager.get_cookie_details(cookie_id) or {}
        cookie_value = user_cookies.get(cookie_id, '')
        ai_settings = db_manager.get_ai_reply_settings(cookie_id)
        default_reply = db_manager.get_default_reply(cookie_id) or {}
        status_enabled = db_manager.get_cookie_status(cookie_id)
        refresh_status = _current_session_refresh_status(cookie_id)

        manager_ready = cookie_manager.manager is not None
        manager_has_cookie = False
        task_running = False
        task_done = False
        task_error = ''
        task_status = {}
        recent_runtime_error = ''
        if manager_ready:
            manager_has_cookie = cookie_id in getattr(cookie_manager.manager, 'cookies', {})
            task_status = getattr(cookie_manager.manager, 'task_status', {}).get(cookie_id, {}) or {}
            task = getattr(cookie_manager.manager, 'tasks', {}).get(cookie_id)
            if task:
                task_done = task.done()
                task_running = not task_done
                if task_done:
                    try:
                        exc = task.exception()
                        task_error = str(exc) if exc else ''
                    except Exception as exc_check_error:
                        task_error = str(exc_check_error)
            if not task_running:
                recent_runtime_error = task_status.get('last_error') or task_error or ''

        with db_manager.lock:
            cursor = db_manager.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM keywords WHERE cookie_id = ?", (cookie_id,))
            keyword_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM default_replies WHERE cookie_id = ?", (cookie_id,))
            default_reply_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM ai_conversations WHERE cookie_id = ?", (cookie_id,))
            conversation_count = cursor.fetchone()[0]
            cursor.execute('''
                SELECT role, content, created_at
                FROM ai_conversations
                WHERE cookie_id = ?
                ORDER BY created_at DESC
                LIMIT 5
            ''', (cookie_id,))
            recent_conversations = [
                {'role': row[0], 'content': (row[1] or '')[:120], 'created_at': row[2]}
                for row in cursor.fetchall()
            ]
            cursor.execute('''
                SELECT event_type, event_description, processing_result, processing_status, error_message, created_at, updated_at
                FROM risk_control_logs
                WHERE cookie_id = ?
                ORDER BY id DESC
                LIMIT 1
            ''', (cookie_id,))
            risk_row = cursor.fetchone()
            latest_risk_control = {
                'event_type': risk_row[0],
                'event_description': risk_row[1],
                'processing_result': risk_row[2],
                'processing_status': risk_row[3],
                'error_message': risk_row[4],
                'created_at': risk_row[5],
                'updated_at': risk_row[6],
            } if risk_row and refresh_status.get('state') in {'refreshing', 'verification_required'} else None

        issues = []
        if not status_enabled:
            issues.append("账号已暂停，自动回复不会运行")
        if len(cookie_value) <= 50:
            issues.append("Cookie 内容过短，可能无效")
        if not manager_ready:
            issues.append("CookieManager 未就绪")
        elif not manager_has_cookie:
            issues.append("运行中的账号管理器没有加载该账号，需要重启服务")
        if manager_ready and manager_has_cookie and not task_running:
            issues.append(recent_runtime_error or task_error or "实时监听任务未运行")
        elif recent_runtime_error:
            issues.append(f"实时监听最近失败: {recent_runtime_error[:120]}")
        if not ai_settings.get('ai_enabled'):
            issues.append("账号 AI 回复未启用")
        if not ai_settings.get('api_key'):
            issues.append("未配置 AI API Key")
        if not ai_settings.get('model_name'):
            issues.append("未配置 AI 模型")
        if keyword_count == 0 and not ai_settings.get('ai_enabled') and not default_reply.get('enabled'):
            issues.append("关键词、AI、默认回复都未配置，无法自动回复")

        refresh_state = refresh_status.get('state')
        if refresh_state == 'verification_required':
            issues.append("Cookie 刷新正在等待身份验证，请在账号卡片中完成验证")
        elif refresh_state == 'refreshing':
            issues.append("Cookie 正在自动刷新，请稍候")
        elif refresh_state in {'failed', 'timeout'}:
            updated_at = refresh_status.get('updated_at')
            if is_runtime_event_active(
                updated_at,
                refresh_status.get('last_success_at'),
                max_age_seconds=600,
            ):
                issues.append(refresh_status.get('message') or "最近一次 Cookie 刷新失败")
        if not cookie_info.get('username') or not cookie_info.get('password'):
            issues.append("未保存闲鱼账号密码，Cookie 过期后无法自动刷新")
            if recent_runtime_error and 'Token获取失败' in recent_runtime_error:
                issues.append("当前 Cookie 已无法换取消息 Token，请重新扫码添加账号或保存闲鱼账号密码后再自动刷新")
        elif not is_valid_account_login_username(cookie_info.get('username')):
            issues.append("已保存的闲鱼登录账号格式异常，请重新填写")

        issues = list(dict.fromkeys(issues))
        blocking_issues = [
            issue for issue in issues
            if issue != "未保存闲鱼账号密码，Cookie 过期后无法自动刷新"
        ]

        return {
            "success": True,
            "data": {
                "cookie_id": cookie_id,
                "ready": len(blocking_issues) == 0,
                "issues": issues,
                "diagnosed_at": time.time(),
                "account": {
                    "enabled": bool(status_enabled),
                    "cookie_length": len(cookie_value),
                    "has_login_username": bool(cookie_info.get('username')),
                    "has_login_password": bool(cookie_info.get('password')),
                    "login_credentials_valid": bool(
                        cookie_info.get('password')
                        and is_valid_account_login_username(cookie_info.get('username'))
                    ),
                    "show_browser": bool(cookie_info.get('show_browser')),
                },
                "runtime": {
                    "manager_ready": manager_ready,
                    "manager_has_cookie": manager_has_cookie,
                    "task_running": task_running,
                    "task_done": task_done,
                    "task_error": task_error,
                    "task_status": task_status,
                    "recent_runtime_error": recent_runtime_error,
                    "latest_risk_control": latest_risk_control,
                },
                "session": refresh_status,
                "reply": {
                    "keyword_count": keyword_count,
                    "default_reply_count": default_reply_count,
                    "default_reply_enabled": bool(default_reply.get('enabled')),
                    "ai_enabled": bool(ai_settings.get('ai_enabled')),
                    "ai_model": ai_settings.get('model_name'),
                    "ai_base_url": ai_settings.get('base_url'),
                    "has_ai_key": bool(ai_settings.get('api_key')),
                    "conversation_count": conversation_count,
                    "recent_conversations": recent_conversations,
                }
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"自动回复诊断异常: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


# ==================== 技能中心API ====================

SKILL_NOTIFICATION_CHANNEL_TYPES = {
    'webhook',
    'wechat',
    'dingtalk',
    'ding_talk',
    'feishu',
    'lark',
    'bark',
    'telegram',
}

def _skill_interval_minutes(value: Any) -> int:
    try:
        return max(15, int(value or 60))
    except (TypeError, ValueError):
        return 60


def _skill_next_run_at(interval_minutes: Any) -> str:
    return (datetime.utcnow() + timedelta(minutes=_skill_interval_minutes(interval_minutes))).strftime("%Y-%m-%d %H:%M:%S")


def _enabled_notification_channels(user_id: int) -> List[Dict[str, Any]]:
    channels = db_manager.get_notification_channels(user_id) or []
    return [
        channel
        for channel in channels
        if channel.get('enabled')
        and str(channel.get('type') or '').strip().lower() in SKILL_NOTIFICATION_CHANNEL_TYPES
    ]


def _parse_channel_config(channel: Dict[str, Any]) -> Dict[str, Any]:
    config = channel.get('config') or {}
    if isinstance(config, dict):
        return config
    try:
        parsed = json.loads(config)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _user_ai_cookie_settings(user_id: int, preferred_cookie_id: str = "") -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    user_cookies = db_manager.get_all_cookies(user_id)
    candidates: List[str] = []
    if preferred_cookie_id and preferred_cookie_id in user_cookies:
        candidates.append(preferred_cookie_id)
    candidates.extend([cookie_id for cookie_id in user_cookies.keys() if cookie_id not in candidates])

    for cookie_id in candidates:
        settings = db_manager.get_ai_reply_settings(cookie_id)
        if settings.get('ai_enabled') and settings.get('api_key') and settings.get('base_url') and settings.get('model_name'):
            return cookie_id, settings
    return None, None


def _user_has_ai_configuration(user_id: int) -> bool:
    return _user_ai_cookie_settings(user_id)[0] is not None


def _json_from_model_text(text: str) -> Dict[str, Any]:
    cleaned = (text or '').strip()
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {}
    except Exception:
        lowered = cleaned.lower()
        return {
            'recommended': any(word in lowered for word in ('yes', 'true', '推荐', '值得', '合适')),
            'score': 80 if any(word in lowered for word in ('yes', 'true', '推荐', '值得', '合适')) else 20,
            'reason': cleaned[:200] or 'AI未返回可解析理由',
        }


def _run_skill_ai_filter(item: Dict[str, Any], task: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    ai_filter = str(task.get('ai_filter') or '').strip()
    if not ai_filter:
        return {'recommended': True, 'score': 0, 'reason': ''}

    cookie_id, settings = _user_ai_cookie_settings(user_id, task.get('account_id') or '')
    if not cookie_id or not settings:
        raise HTTPException(status_code=400, detail="AI筛选需要先为当前用户的账号配置并启用AI")

    client = ai_reply_engine._create_openai_client(cookie_id)
    if not client:
        raise HTTPException(status_code=400, detail="AI筛选需要可用的AI客户端")

    item_summary = {
        'title': item.get('title') or '',
        'price': item.get('price') or '',
        'region': item.get('area') or item.get('region') or '',
        'seller_name': item.get('seller_name') or '',
        'description': item.get('desc') or item.get('description') or '',
        'publish_time': item.get('publish_time') or '',
        'want_count': item.get('want_count') or '',
    }
    messages = [
        {
            'role': 'system',
            'content': (
                '你是闲鱼监控商品筛选助手。根据用户的筛选要求判断商品是否值得保留。'
                '只输出JSON：{"recommended":true/false,"score":0-100,"reason":"简短中文理由"}。'
            ),
        },
        {
            'role': 'user',
            'content': f"筛选要求：{ai_filter}\n商品信息：{json.dumps(item_summary, ensure_ascii=False)}",
        },
    ]
    raw = ai_reply_engine._call_openai_api(client, settings, messages, max_tokens=220, temperature=0.1)
    parsed = _json_from_model_text(raw)
    score = parsed.get('score', 0)
    try:
        score = max(0, min(100, int(float(score))))
    except (TypeError, ValueError):
        score = 0
    recommended = bool(parsed.get('recommended')) and score >= 50
    reason = str(parsed.get('reason') or raw or '').strip()[:300]
    return {'recommended': recommended, 'score': score, 'reason': reason or 'AI筛选完成'}


def _raise_skill_notification_api_error(response: Any, channel_type: str) -> None:
    try:
        payload = response.json()
    except Exception:
        return
    if not isinstance(payload, dict):
        return

    if channel_type in {'wechat', 'dingtalk', 'ding_talk'}:
        code = payload.get('errcode')
        message = payload.get('errmsg') or payload.get('message')
    elif channel_type in {'feishu', 'lark'}:
        code = payload.get('code')
        message = payload.get('msg') or payload.get('message')
    elif channel_type == 'telegram':
        code = 0 if payload.get('ok', True) else payload.get('error_code', -1)
        message = payload.get('description')
    elif channel_type == 'bark':
        code = payload.get('code')
        message = payload.get('message')
        if code == 200:
            code = 0
    else:
        return

    if code not in (None, 0, '0'):
        raise ValueError(str(message or f'{channel_type} API error {code}')[:300])


def _safe_skill_notification_error(error: Exception) -> str:
    message = str(error or type(error).__name__)
    message = re.sub(r'https?://[^\s；]+', '[redacted-url]', message, flags=re.IGNORECASE)
    return message[:240] or type(error).__name__


def _send_skill_notification_to_channel(channel: Dict[str, Any], task: Dict[str, Any], result_payload: Dict[str, Any]) -> None:
    config = _parse_channel_config(channel)
    channel_type = str(channel.get('type') or '').strip().lower()
    title = f"闲鱼监控命中：{result_payload.get('title') or task.get('keyword')}"
    lines = [
        title,
        f"任务：{task.get('name') or task.get('keyword')}",
        f"价格：{result_payload.get('price') if result_payload.get('price') is not None else '-'}",
        f"地区：{result_payload.get('region') or '-'}",
        f"理由：{result_payload.get('ai_reason') or '-'}",
    ]
    if result_payload.get('item_url'):
        lines.append(f"链接：{result_payload['item_url']}")
    message = "\n".join(lines)

    timeout = 10
    if channel_type in {'webhook', 'wechat', 'dingtalk', 'ding_talk', 'feishu', 'lark'}:
        url = config.get('url') or config.get('webhook') or config.get('webhook_url')
        if not url:
            raise ValueError("Webhook通知缺少url")

        request_kwargs: Dict[str, Any] = {}
        if channel_type == 'wechat':
            payload = {'msgtype': 'text', 'text': {'content': message}}
        elif channel_type in {'dingtalk', 'ding_talk'}:
            payload = {'msgtype': 'markdown', 'markdown': {'title': title, 'text': message}}
            secret = str(config.get('secret') or '').strip()
            if secret:
                import base64
                import hashlib
                import hmac

                timestamp = str(round(time.time() * 1000))
                signature = base64.b64encode(
                    hmac.new(
                        secret.encode('utf-8'),
                        f'{timestamp}\n{secret}'.encode('utf-8'),
                        digestmod=hashlib.sha256,
                    ).digest()
                ).decode('utf-8')
                request_kwargs['params'] = {'timestamp': timestamp, 'sign': signature}
        elif channel_type in {'feishu', 'lark'}:
            payload = {'msg_type': 'text', 'content': {'text': message}}
            secret = str(config.get('secret') or '').strip()
            if secret:
                import base64
                import hashlib
                import hmac

                timestamp = str(int(time.time()))
                string_to_sign = f'{timestamp}\n{secret}'
                signature = base64.b64encode(
                    hmac.new(string_to_sign.encode('utf-8'), b'', digestmod=hashlib.sha256).digest()
                ).decode('utf-8')
                payload.update({'timestamp': timestamp, 'sign': signature})
        else:
            payload = config.get('payload_template')
        if not isinstance(payload, dict):
            payload = {'title': title, 'text': message, 'message': message, 'item_url': result_payload.get('item_url')}
        response = requests.post(url, json=payload, timeout=timeout, **request_kwargs)
        response.raise_for_status()
        _raise_skill_notification_api_error(response, channel_type)
        return

    if channel_type == 'bark':
        url = config.get('url')
        if not url:
            server = (config.get('server_url') or config.get('server') or 'https://api.day.app').rstrip('/')
            key = config.get('key') or config.get('device_key')
            if not key:
                raise ValueError("Bark通知缺少url或device_key")
            url = f"{server}/{key}/{quote(title)}/{quote(message)}"
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        _raise_skill_notification_api_error(response, channel_type)
        return

    if channel_type == 'telegram':
        token = config.get('bot_token') or config.get('token')
        chat_id = config.get('chat_id')
        api_base = (config.get('api_base_url') or 'https://api.telegram.org').rstrip('/')
        if not token or not chat_id:
            raise ValueError("Telegram通知缺少bot_token或chat_id")
        response = requests.post(
            f"{api_base}/bot{token}/sendMessage",
            json={'chat_id': chat_id, 'text': message, 'disable_web_page_preview': False},
            timeout=timeout,
        )
        response.raise_for_status()
        _raise_skill_notification_api_error(response, channel_type)
        return

    raise ValueError(f"暂不支持的通知渠道类型: {channel_type}")


def _notify_skill_monitor_result(task: Dict[str, Any], user_id: int, result_id: int, result_payload: Dict[str, Any]) -> Tuple[str, str]:
    if not task.get('notify_enabled'):
        return 'disabled', ''

    channels = _enabled_notification_channels(user_id)
    if not channels:
        db_manager.update_skill_monitor_result_notification(result_id, user_id, 'skipped_no_channel', '没有启用的通知渠道')
        return 'skipped_no_channel', '没有启用的通知渠道'

    errors = []
    sent_count = 0
    for channel in channels:
        try:
            _send_skill_notification_to_channel(channel, task, result_payload)
            sent_count += 1
        except Exception as exc:
            errors.append(
                f"{channel.get('name') or channel.get('type')}: {_safe_skill_notification_error(exc)}"
            )

    error = "；".join(errors)[:500]
    if sent_count == len(channels):
        status = 'sent'
    elif sent_count > 0:
        status = 'partial'
    else:
        status = 'failed'
        error = error or '通知发送失败'
    db_manager.update_skill_monitor_result_notification(result_id, user_id, status, error)
    return status, error


@skills_router.get('/api/skills/capabilities')
def get_skill_capabilities(current_user: Dict[str, Any] = Depends(get_current_user)):
    account_count = len(db_manager.get_all_cookies(current_user['user_id']))
    user_id = current_user['user_id']
    has_ai = _user_has_ai_configuration(user_id)
    channels = _enabled_notification_channels(user_id)
    return {
        'success': True,
        'data': {
            'manual_monitor': {
                'available': True,
                'label': '可用',
                'detail': '使用Playwright执行单次真实搜索',
            },
            'scheduled_monitor': {
                'available': True,
                'label': '可用',
                'detail': '单worker内置调度器按任务间隔运行',
            },
            'ai_filter': {
                'available': has_ai,
                'label': '可用' if has_ai else '需配置AI',
                'detail': '命中规则后调用AI判断是否推荐' if has_ai else '请先为至少一个账号配置并启用AI',
            },
            'notifications': {
                'available': len(channels) > 0,
                'label': '可用' if channels else '缺少渠道',
                'detail': f'将发送到 {len(channels)} 个启用通知渠道' if channels else '请先创建并启用通知渠道',
            },
            'expert_live_reply': {
                'available': account_count > 0,
                'label': '可用' if account_count > 0 else '缺少账号',
                'detail': '价格、技术和默认专家策略同时作用于测试与正式AI回复',
            },
        },
    }

SKILL_AGENT_PROMPT_TITLES = {
    'classify': '意图分类专家',
    'price': '议价专家',
    'tech': '技术专家',
    'default': '默认客服',
}


def _default_skill_agent_prompts() -> Dict[str, str]:
    prompts = {
        key: ai_reply_engine.default_prompts.get(key, '')
        for key in SKILL_AGENT_PROMPT_TITLES.keys()
    }
    prompts['classify'] = '''你是一个闲鱼客服意图分类专家。
只输出一个类别：price、tech、default。
price：砍价、优惠、包邮、最低价。
tech：参数、规格、功能、安装、兼容、使用方法。
default：其它售前、物流、售后和普通咨询。'''
    return prompts


def _ensure_skill_agent_prompts(user_id: int) -> Dict[str, Dict[str, Any]]:
    prompts = db_manager.get_skill_agent_prompts(user_id)
    defaults = _default_skill_agent_prompts()
    for prompt_type, content in defaults.items():
        if prompt_type not in prompts:
            db_manager.upsert_skill_agent_prompt(
                user_id=user_id,
                prompt_type=prompt_type,
                title=SKILL_AGENT_PROMPT_TITLES[prompt_type],
                content=content,
                enabled=True
            )
        elif prompt_type == 'classify' and '已不再被 detect_intent 使用' in prompts[prompt_type].get('content', ''):
            db_manager.upsert_skill_agent_prompt(
                user_id=user_id,
                prompt_type=prompt_type,
                title=SKILL_AGENT_PROMPT_TITLES[prompt_type],
                content=content,
                enabled=True
            )
    return db_manager.get_skill_agent_prompts(user_id)


def _classify_skill_agent_intent(message: str) -> str:
    text = (message or '').lower()
    price_keywords = ['便宜', '优惠', '降价', '少点', '砍价', '刀', '包邮', '最低', 'price', 'cheap']
    tech_keywords = ['怎么用', '功能', '参数', '规格', '版本', '安装', '兼容', '教程', 'tech', '配置']
    if any(keyword in text for keyword in price_keywords):
        return 'price'
    if any(keyword in text for keyword in tech_keywords):
        return 'tech'
    return 'default'


def _build_skill_agent_preview(message: str, intent: str, item_title: str, item_price: float) -> str:
    if intent == 'price':
        return f"{item_title}成色不错，标价{item_price:g}元。可以小优惠，太低不行。"
    if intent == 'tech':
        return f"{item_title}可以正常使用。具体参数看商品描述，不清楚的我帮你确认。"
    return f"{item_title}还在的。可以直接拍，有问题我都会说明。"


def _parse_skill_price(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).replace(',', '').replace('￥', '').replace('¥', '').strip()
    if not text:
        return None
    match = re.search(r'(\d+(?:\.\d+)?)\s*(万)?', text)
    if not match:
        return None
    price = float(match.group(1))
    if match.group(2):
        price *= 10000
    return round(price, 2)


def _parse_skill_publish_timestamp(value: Any) -> Optional[float]:
    if value is None:
        return None

    text = str(value).strip()
    if not text or text in {'未知时间', '未知', '-'}:
        return None

    now = time.time()
    relative_patterns = [
        (r'(\d+)\s*分钟?前', 60),
        (r'(\d+)\s*分钟前', 60),
        (r'(\d+)\s*小时?前', 3600),
        (r'(\d+)\s*小时前', 3600),
        (r'(\d+)\s*天前', 86400),
    ]
    if '刚刚' in text:
        return now
    for pattern, seconds in relative_patterns:
        match = re.search(pattern, text)
        if match:
            return now - int(match.group(1)) * seconds

    if text.isdigit():
        timestamp = int(text)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return float(timestamp)

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m-%d %H:%M",
        "%m-%d",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            if "%Y" not in fmt:
                parsed = parsed.replace(year=datetime.now().year)
                if parsed.timestamp() > now + 86400:
                    parsed = parsed.replace(year=parsed.year - 1)
            return parsed.timestamp()
        except ValueError:
            continue
    return None


def _skill_item_matches_task(item: Dict[str, Any], task: Dict[str, Any]) -> Tuple[bool, str, Optional[float]]:
    keyword = (task.get('keyword') or '').strip()
    keywords = [part.lower() for part in re.split(r'[\s,，]+', keyword) if part.strip()]
    item_text = " ".join([
        str(item.get('title') or ''),
        str(item.get('desc') or ''),
        str(item.get('description') or ''),
    ]).lower()
    if keywords and not any(part in item_text for part in keywords):
        return False, '关键词不匹配', None

    price = _parse_skill_price(item.get('price'))
    if task.get('min_price') is not None and price is not None and price < float(task['min_price']):
        return False, '低于最低价', price
    if task.get('max_price') is not None and price is not None and price > float(task['max_price']):
        return False, '高于最高价', price

    task_region = (task.get('region') or '').strip()
    item_region = (item.get('area') or item.get('region') or '').strip()
    if task_region and task_region not in item_region:
        return False, '地区不匹配', price

    try:
        published_within_hours = float(task.get('published_within_hours') or 0)
    except (TypeError, ValueError):
        published_within_hours = 0
    publish_reason = ''
    if published_within_hours > 0:
        publish_timestamp = _parse_skill_publish_timestamp(item.get('publish_time'))
        if publish_timestamp is None:
            publish_reason = '，发布时间未知'
        elif time.time() - publish_timestamp > published_within_hours * 3600:
            return False, f'超过{published_within_hours:g}小时发布时间范围', price
        else:
            publish_reason = f'，{published_within_hours:g}小时内发布'

    return True, f'命中关键词、价格、地区过滤{publish_reason}', price


async def _run_real_skill_monitor(task: Dict[str, Any], user_id: int, *, scheduled_run: bool = False) -> Tuple[List[int], int, Dict[str, Any]]:
    from utils.item_search import search_xianyu_items

    keyword = (task.get('keyword') or '').strip()
    if str(task.get('ai_filter') or '').strip():
        _user_ai_cookie_settings(user_id, task.get('account_id') or '')
        if not _user_has_ai_configuration(user_id):
            raise HTTPException(status_code=400, detail="AI筛选需要先为当前用户的账号配置并启用AI")

    page_size = 20
    search_result = await search_xianyu_items(keyword=keyword, page=1, page_size=page_size)

    if not search_result or search_result.get('error'):
        error_message = (search_result or {}).get('error') or '真实搜索没有返回结果'
        raise HTTPException(status_code=502, detail=f"闲鱼真实搜索失败: {error_message}")

    if not search_result.get('is_real_data'):
        raise HTTPException(status_code=502, detail="闲鱼搜索没有返回真实数据，已阻止写入样例结果")

    raw_items = search_result.get('items') or []
    created_ids: List[int] = []
    seen_urls = set()

    for item in raw_items:
        matched, reason, price = _skill_item_matches_task(item, task)
        if not matched:
            continue

        item_url = item.get('item_url') or ''
        item_id = str(item.get('item_id') or '')
        dedupe_key = item_url or item_id or item.get('title')
        if dedupe_key in seen_urls:
            continue
        seen_urls.add(dedupe_key)
        if db_manager.skill_monitor_result_exists(task['id'], user_id, item_url, item_id):
            continue

        ai_filter_result = {'recommended': True, 'score': 0, 'reason': reason}
        if str(task.get('ai_filter') or '').strip():
            ai_filter_result = _run_skill_ai_filter(item, task, user_id)
            if not ai_filter_result.get('recommended'):
                continue

        result_payload = {
            'task_id': task['id'],
            'user_id': user_id,
            'title': item.get('title') or keyword,
            'price': price,
            'region': item.get('area') or item.get('region') or '',
            'item_url': item_url,
            'item_image': item.get('main_image') or item.get('item_image') or '',
            'seller_name': item.get('seller_name') or '',
            'ai_score': ai_filter_result.get('score') or 0,
            'ai_reason': ai_filter_result.get('reason') or reason,
            'notify_status': 'pending' if task.get('notify_enabled') else 'disabled',
            'raw_data': {
                'source': search_result.get('source') or 'playwright',
                'is_real_data': True,
                'keyword': keyword,
                'filter_reason': reason,
                'ai_filter': task.get('ai_filter') or '',
                'ai_recommended': bool(ai_filter_result.get('recommended')),
                'scheduled_run': scheduled_run,
                'published_within_hours': task.get('published_within_hours'),
                'item_id': item_id,
                'publish_time': item.get('publish_time'),
                'want_count': item.get('want_count'),
            }
        }
        result_id = db_manager.create_skill_monitor_result(result_payload)
        if result_id:
            notify_status, notify_error = _notify_skill_monitor_result(task, user_id, result_id, result_payload)
            if notify_error:
                result_payload.setdefault('raw_data', {})['notify_error'] = notify_error
            result_payload['notify_status'] = notify_status
            created_ids.append(result_id)

    return created_ids, len(raw_items), search_result


async def execute_skill_monitor_task(task: Dict[str, Any], user_id: int, *, scheduled_run: bool = False) -> Dict[str, Any]:
    if not db_manager.mark_skill_monitor_task_running(task['id'], user_id):
        raise HTTPException(status_code=409, detail="监控任务正在运行，请稍后再试")

    try:
        result_ids, raw_count, search_result = await _run_real_skill_monitor(task, user_id, scheduled_run=scheduled_run)
        next_run_at = _skill_next_run_at(task.get('schedule_interval_minutes')) if task.get('schedule_enabled') else None
        db_manager.update_skill_monitor_task_run(
            task['id'],
            user_id,
            status='success',
            error='',
            next_run_at=next_run_at,
        )
        db_manager.log_skill_event(
            user_id,
            'monitor',
            f"{'定时' if scheduled_run else '手动'}运行监控任务: {task['keyword']}",
            payload={
                'task_id': task['id'],
                'result_ids': result_ids,
                'raw_count': raw_count,
                'source': search_result.get('source'),
                'scheduled_run': scheduled_run,
            }
        )
        return {
            "success": True,
            "message": f"真实监控完成，抓取 {raw_count} 条，命中 {len(result_ids)} 条",
            "result_ids": result_ids,
            "created_count": len(result_ids),
            "raw_count": raw_count,
            "source": search_result.get('source'),
            "is_real_data": True,
            "scheduled_run": scheduled_run,
            "next_run_at": next_run_at,
        }
    except Exception as exc:
        next_run_at = _skill_next_run_at(task.get('schedule_interval_minutes')) if task.get('schedule_enabled') else None
        message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        db_manager.update_skill_monitor_task_run(
            task['id'],
            user_id,
            status='failed',
            error=str(message)[:500],
            next_run_at=next_run_at,
        )
        if isinstance(exc, HTTPException):
            raise
        raise


@skills_router.get("/api/skills/monitor/tasks")
def list_skill_monitor_tasks(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取当前用户的技能监控任务"""
    return {
        "success": True,
        "data": db_manager.list_skill_monitor_tasks(current_user['user_id'])
    }


@skills_router.post("/api/skills/monitor/tasks")
def create_skill_monitor_task(task: SkillMonitorTaskIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    """创建技能监控任务"""
    try:
        if not task.keyword.strip():
            raise HTTPException(status_code=400, detail="关键词不能为空")
        if task.schedule_interval_minutes < 15:
            raise HTTPException(status_code=400, detail="定时监控间隔不能少于15分钟")
        if task.notify_enabled and not _enabled_notification_channels(current_user['user_id']):
            raise HTTPException(status_code=400, detail="请先创建并启用通知渠道，再开启监控通知")
        try:
            validate_skill_monitor_features(
                notify_enabled=task.notify_enabled,
                ai_filter=task.ai_filter,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if task.account_id:
            user_cookies = db_manager.get_all_cookies(current_user['user_id'])
            if task.account_id not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限绑定该闲鱼账号")

        task_payload = task.dict()
        task_payload['schedule_interval_minutes'] = _skill_interval_minutes(task.schedule_interval_minutes)
        if task.schedule_enabled and not task.next_run_at:
            task_payload['next_run_at'] = _skill_next_run_at(task_payload['schedule_interval_minutes'])

        task_id = db_manager.create_skill_monitor_task(current_user['user_id'], task_payload)
        if not task_id:
            raise HTTPException(status_code=400, detail="创建监控任务失败")

        db_manager.log_skill_event(
            current_user['user_id'],
            'monitor',
            f"创建监控任务: {task.keyword}",
            payload={'task_id': task_id}
        )
        return {"success": True, "id": task_id, "message": "监控任务创建成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建技能监控任务异常: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@skills_router.put("/api/skills/monitor/tasks/{task_id}")
def update_skill_monitor_task(task_id: int, task: SkillMonitorTaskUpdate, current_user: Dict[str, Any] = Depends(get_current_user)):
    """更新技能监控任务"""
    try:
        existing = db_manager.get_skill_monitor_task(task_id, current_user['user_id'])
        if not existing:
            raise HTTPException(status_code=404, detail="监控任务不存在")

        task_payload = task.dict(exclude_unset=True)
        if 'keyword' in task_payload and not str(task_payload.get('keyword') or '').strip():
            raise HTTPException(status_code=400, detail="关键词不能为空")
        if 'schedule_interval_minutes' in task_payload:
            if task_payload['schedule_interval_minutes'] is not None and task_payload['schedule_interval_minutes'] < 15:
                raise HTTPException(status_code=400, detail="定时监控间隔不能少于15分钟")
            task_payload['schedule_interval_minutes'] = _skill_interval_minutes(task_payload['schedule_interval_minutes'])
        if task_payload.get('notify_enabled') and not _enabled_notification_channels(current_user['user_id']):
            raise HTTPException(status_code=400, detail="请先创建并启用通知渠道，再开启监控通知")
        account_id = task_payload.get('account_id')
        if account_id:
            user_cookies = db_manager.get_all_cookies(current_user['user_id'])
            if account_id not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限绑定该闲鱼账号")

        schedule_enabled = task_payload.get('schedule_enabled', existing.get('schedule_enabled'))
        interval = task_payload.get('schedule_interval_minutes', existing.get('schedule_interval_minutes') or 60)
        if schedule_enabled and ('next_run_at' not in task_payload or not task_payload.get('next_run_at')):
            task_payload['next_run_at'] = _skill_next_run_at(interval)
        if not schedule_enabled and task_payload.get('schedule_enabled') is False:
            task_payload['next_run_at'] = None

        if not db_manager.update_skill_monitor_task(task_id, current_user['user_id'], task_payload):
            raise HTTPException(status_code=400, detail="更新监控任务失败")
        return {"success": True, "message": "监控任务已更新"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新技能监控任务异常: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@skills_router.post("/api/skills/monitor/tasks/{task_id}/run")
async def run_skill_monitor_task(task_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    """运行技能监控任务，调用真实闲鱼搜索并写入真实结果"""
    try:
        task = db_manager.get_skill_monitor_task(task_id, current_user['user_id'])
        if not task:
            raise HTTPException(status_code=404, detail="监控任务不存在")

        return await execute_skill_monitor_task(task, current_user['user_id'], scheduled_run=False)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"运行技能监控任务异常: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@skills_router.get("/api/skills/monitor/results")
def list_skill_monitor_results(
    task_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """获取技能监控结果"""
    return {
        "success": True,
        "data": db_manager.list_skill_monitor_results(current_user['user_id'], task_id=task_id, limit=limit)
    }


@skills_router.get("/api/skills/agent/prompts")
def get_skill_agent_prompts(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取AI专家客服提示词"""
    prompts = _ensure_skill_agent_prompts(current_user['user_id'])
    return {
        "success": True,
        "data": list(prompts.values())
    }


@skills_router.put("/api/skills/agent/prompts/{prompt_type}")
def update_skill_agent_prompt(
    prompt_type: str,
    prompt: SkillAgentPromptIn,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """更新AI专家客服提示词"""
    if prompt_type not in SKILL_AGENT_PROMPT_TITLES:
        raise HTTPException(status_code=400, detail="不支持的专家类型")
    if not prompt.content.strip():
        raise HTTPException(status_code=400, detail="提示词内容不能为空")

    success = db_manager.upsert_skill_agent_prompt(
        current_user['user_id'],
        prompt_type,
        prompt.title or SKILL_AGENT_PROMPT_TITLES[prompt_type],
        prompt.content,
        prompt.enabled
    )
    if not success:
        raise HTTPException(status_code=400, detail="保存提示词失败")

    db_manager.log_skill_event(
        current_user['user_id'],
        'agent',
        f"更新AI专家提示词: {prompt_type}",
        payload={'prompt_type': prompt_type}
    )
    return {"success": True, "message": "提示词保存成功"}


@skills_router.post("/api/skills/agent/test-reply")
def test_skill_agent_reply(test_data: SkillAgentTestIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    """测试AI专家客服策略，调用真实AI回复引擎"""
    try:
        prompts = _ensure_skill_agent_prompts(current_user['user_id'])
        intent = _classify_skill_agent_intent(test_data.message)
        prompt = prompts.get(intent) or prompts.get('default')

        user_cookies = db_manager.get_all_cookies(current_user['user_id'])
        cookie_id = test_data.cookie_id or next(iter(user_cookies.keys()), '')
        if not cookie_id:
            raise HTTPException(status_code=400, detail="请先添加闲鱼账号，再测试真实AI回复")
        if cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限使用该闲鱼账号")

        settings = db_manager.get_ai_reply_settings(cookie_id)
        if not settings.get('ai_enabled'):
            raise HTTPException(status_code=400, detail="该账号未启用AI回复，请先在账号管理中打开AI助手")
        if not settings.get('api_key'):
            raise HTTPException(status_code=400, detail="未配置AI API Key，请先在系统设置中配置")
        if not settings.get('base_url'):
            raise HTTPException(status_code=400, detail="未配置AI API地址，请先在系统设置中配置")

        if not test_data.item_id:
            raise HTTPException(status_code=400, detail="请选择真实商品")
        db_item = db_manager.get_item_info(cookie_id, test_data.item_id)
        if not db_item:
            raise HTTPException(status_code=404, detail="当前账号中找不到该商品，请先同步商品")
        item_info = {
            'title': db_item.get('item_title') or '未知商品',
            'price': db_item.get('item_price') or '',
            'desc': db_item.get('item_detail') or db_item.get('item_description') or '',
        }
        reply = ai_reply_engine.generate_lab_reply(
            message=test_data.message,
            item_info=item_info,
            cookie_id=cookie_id,
            item_id=test_data.item_id,
        )
        if not reply:
            raise HTTPException(status_code=502, detail="AI回复生成失败，请检查模型名、Key、余额和网络")

        db_manager.log_skill_event(
            current_user['user_id'],
            'agent',
            f"测试AI专家回复: {intent}",
            payload={
                'intent': intent,
                'cookie_id': cookie_id,
                'model_name': settings.get('model_name'),
                'base_url': settings.get('base_url'),
                'message_preview': test_data.message[:40],
            }
        )
        return {
            "success": True,
            "intent": intent,
            "expert": (prompt or {}).get('title', SKILL_AGENT_PROMPT_TITLES.get(intent, '默认客服')),
            "reply": reply,
            "used_prompt": prompt,
            "cookie_id": cookie_id,
            "model_name": settings.get('model_name'),
            "base_url": settings.get('base_url'),
            "is_real_ai": True
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"测试技能AI专家回复异常: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@skills_router.get("/api/skills/ops/health")
def get_skill_ops_health(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取技能中心运维健康信息"""
    db_path = Path(db_manager.db_path)
    logs = db_manager.list_skill_logs(current_user['user_id'], limit=10)
    user_cookie_ids = set(db_manager.get_all_cookies(current_user['user_id']).keys())
    manager_tasks = getattr(cookie_manager.manager, 'tasks', {}) or {}
    listening_ids = {
        str(cookie_id) for cookie_id, task in manager_tasks.items()
        if str(cookie_id) in user_cookie_ids and not getattr(task, 'done', lambda: False)()
    }
    account_ai_settings = [db_manager.get_ai_reply_settings(cookie_id) for cookie_id in user_cookie_ids]
    ai_enabled_count = sum(1 for settings in account_ai_settings if settings.get('ai_enabled'))
    ai_ready_count = sum(
        1 for settings in account_ai_settings
        if settings.get('ai_enabled') and settings.get('api_key') and settings.get('base_url') and settings.get('model_name')
    )
    raw_system_settings = db_manager.get_all_system_settings()
    global_ai_configured = bool(
        raw_system_settings.get('ai_api_url')
        and raw_system_settings.get('ai_model')
        and raw_system_settings.get('ai_api_key')
    )
    return {
        "success": True,
        "data": {
            "api": "ok",
            "database": {
                "path": str(db_path),
                "exists": db_path.exists(),
                "writable": os.access(db_path.parent if db_path.parent else Path('.'), os.W_OK),
                "migration_version": getattr(db_manager, "schema_version", "legacy"),
            },
            "runtime_sessions": get_session_registry().summary(),
            "cookie_manager": "ready" if cookie_manager.manager is not None else "not_ready",
            "accounts": {
                "total": len(user_cookie_ids),
                "listening": len(listening_ids),
                "listener_state": "running" if listening_ids else "stopped",
            },
            "ai": {
                "global_configured": global_ai_configured,
                "enabled_accounts": ai_enabled_count,
                "ready_accounts": ai_ready_count,
                "model": raw_system_settings.get('ai_model') or '',
            },
            "skills": {
                "monitor_tasks": len(db_manager.list_skill_monitor_tasks(current_user['user_id'])),
                "monitor_results": len(db_manager.list_skill_monitor_results(current_user['user_id'], limit=500)),
                "logs": len(logs),
            },
            "recent_logs": logs,
        }
    }


@skills_router.get("/api/skills/ops/browser-status")
def get_skill_browser_status(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取Playwright/浏览器运行状态"""
    browser_info = {
        "playwright_importable": False,
        "playwright_launchable": False,
        "browser_path": "",
        "active_cookie_tasks": 0,
        "account_count": len(db_manager.get_all_cookies(current_user['user_id'])),
    }
    try:
        import playwright  # noqa: F401
        browser_info["playwright_importable"] = True
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright_runtime:
            browser_info["browser_path"] = Path(playwright_runtime.chromium.executable_path).name
            browser = playwright_runtime.chromium.launch(headless=True)
            browser.close()
            browser_info["playwright_launchable"] = True
    except Exception as e:
        first_line = str(e).splitlines()[0].strip() if str(e) else type(e).__name__
        browser_info["playwright_error"] = first_line[:180]

    if cookie_manager.manager is not None:
        browser_info["active_cookie_tasks"] = len(cookie_manager.manager.tasks)

    return {"success": True, "data": browser_info}


@skills_router.get("/api/skills/ops/delivery-diagnostics")
def get_skill_delivery_diagnostics(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取发货、卡券和规则诊断"""
    user_id = current_user['user_id']
    cards = db_manager.get_all_cards(user_id)
    delivery_rules = db_manager.get_all_delivery_rules(user_id)
    orders = db_manager.get_orders_for_analytics(user_id=user_id)
    pending_orders = [
        order for order in orders
        if order.get('order_status') not in ('TRADE_FINISHED', '交易成功', 'completed', 'cancelled')
    ]

    diagnostics = {
        "cards_total": len(cards),
        "delivery_rules_total": len(delivery_rules),
        "pending_orders_sample": len(pending_orders),
        "auto_delivery_ready": len(cards) > 0 and len(delivery_rules) > 0,
        "recommendations": []
    }
    if not cards:
        diagnostics["recommendations"].append("先在卡密库存中添加可发货内容。")
    if not delivery_rules:
        diagnostics["recommendations"].append("为商品关键词绑定发货规则。")
    if diagnostics["auto_delivery_ready"]:
        diagnostics["recommendations"].append("卡券与发货规则已存在，可进行订单发货回归测试。")

    return {"success": True, "data": diagnostics}


# ==================== 日志管理API ====================

@admin_router.get("/logs")
async def get_logs(lines: int = 200, level: str = None, source: str = None, _: None = Depends(require_auth)):
    """获取实时系统日志"""
    try:
        # 获取文件日志收集器
        collector = get_file_log_collector()

        # 获取日志
        logs = collector.get_logs(lines=lines, level_filter=level, source_filter=source)

        return {"success": True, "logs": logs}

    except Exception as e:
        return {"success": False, "message": f"获取日志失败: {str(e)}", "logs": []}


@admin_router.get("/risk-control-logs")
async def get_risk_control_logs(
    cookie_id: str = None,
    limit: int = 100,
    offset: int = 0,
    admin_user: Dict[str, Any] = Depends(require_admin)
):
    """获取风控日志（管理员专用）"""
    try:
        log_with_user('info', f"查询风控日志: cookie_id={cookie_id}, limit={limit}, offset={offset}", admin_user)

        # 获取风控日志
        logs = db_manager.get_risk_control_logs(cookie_id=cookie_id, limit=limit, offset=offset)
        total_count = db_manager.get_risk_control_logs_count(cookie_id=cookie_id)

        log_with_user('info', f"风控日志查询成功，共 {len(logs)} 条记录，总计 {total_count} 条", admin_user)

        return {
            "success": True,
            "data": logs,
            "total": total_count,
            "limit": limit,
            "offset": offset
        }

    except Exception as e:
        log_with_user('error', f"获取风控日志失败: {str(e)}", admin_user)
        return {
            "success": False,
            "message": f"获取风控日志失败: {str(e)}",
            "data": [],
            "total": 0
        }


@admin_router.delete("/risk-control-logs/{log_id}")
async def delete_risk_control_log(
    log_id: int,
    admin_user: Dict[str, Any] = Depends(require_admin)
):
    """删除风控日志记录（管理员专用）"""
    try:
        log_with_user('info', f"删除风控日志记录: {log_id}", admin_user)

        success = db_manager.delete_risk_control_log(log_id)

        if success:
            log_with_user('info', f"风控日志删除成功: {log_id}", admin_user)
            return {"success": True, "message": "删除成功"}
        else:
            log_with_user('warning', f"风控日志删除失败: {log_id}", admin_user)
            return {"success": False, "message": "删除失败，记录可能不存在"}

    except Exception as e:
        log_with_user('error', f"删除风控日志失败: {log_id} - {str(e)}", admin_user)
        return {"success": False, "message": f"删除失败: {str(e)}"}


@admin_router.get("/logs/stats")
async def get_log_stats(_: None = Depends(require_auth)):
    """获取日志统计信息"""
    try:
        collector = get_file_log_collector()
        stats = collector.get_stats()

        return {"success": True, "stats": stats}

    except Exception as e:
        return {"success": False, "message": f"获取日志统计失败: {str(e)}", "stats": {}}


@admin_router.post("/logs/clear")
async def clear_logs(_: None = Depends(require_auth)):
    """清空日志"""
    try:
        collector = get_file_log_collector()
        collector.clear_logs()

        return {"success": True, "message": "日志已清空"}

    except Exception as e:
        return {"success": False, "message": f"清空日志失败: {str(e)}"}


# ==================== 商品管理API ====================

@content_router.post("/items/get-all-from-account")
async def get_all_items_from_account(request: dict, _: None = Depends(require_auth)):
    """从指定账号获取所有商品信息"""
    try:
        cookie_id = request.get('cookie_id')
        if not cookie_id:
            return {"success": False, "message": "缺少cookie_id参数"}

        # 获取指定账号的cookie信息
        cookie_info = db_manager.get_cookie_by_id(cookie_id)
        if not cookie_info:
            return {"success": False, "message": "未找到指定的账号信息"}

        cookies_str = cookie_info.get('cookies_str', '')
        if not cookies_str:
            return {"success": False, "message": "账号cookie信息为空"}

        # 创建XianyuLive实例，传入正确的cookie_id
        from XianyuAutoAsync import XianyuLive
        xianyu_instance = XianyuLive(cookies_str, cookie_id)

        # 调用获取所有商品信息的方法（自动分页）
        logger.info(f"开始获取账号 {cookie_id} 的所有商品信息")
        result = await xianyu_instance.get_all_items()

        # 关闭session
        await xianyu_instance.close_session()

        if result.get('error'):
            logger.error(f"获取商品信息失败: {result['error']}")
            return {"success": False, "message": result['error']}
        else:
            total_count = result.get('total_count', 0)
            total_pages = result.get('total_pages', 1)
            saved_count = result.get('total_saved', 0)
            logger.info(f"成功获取账号 {cookie_id} 的 {total_count} 个商品（共{total_pages}页），保存 {saved_count} 个")
            return {
                "success": True,
                "message": f"成功获取商品，共 {total_count} 件，保存 {saved_count} 件",
                "total_count": total_count,
                "total_pages": total_pages,
                "saved_count": saved_count
            }

    except Exception as e:
        logger.error(f"获取账号商品信息异常: {str(e)}")
        return {"success": False, "message": f"获取商品信息异常: {str(e)}"}


@content_router.post("/items/get-by-page")
async def get_items_by_page(request: dict, _: None = Depends(require_auth)):
    """从指定账号按页获取商品信息"""
    try:
        # 验证参数
        cookie_id = request.get('cookie_id')
        page_number = request.get('page_number', 1)
        page_size = request.get('page_size', 20)

        if not cookie_id:
            return {"success": False, "message": "缺少cookie_id参数"}

        # 验证分页参数
        try:
            page_number = int(page_number)
            page_size = int(page_size)
        except (ValueError, TypeError):
            return {"success": False, "message": "页码和每页数量必须是数字"}

        if page_number < 1:
            return {"success": False, "message": "页码必须大于0"}

        if page_size < 1 or page_size > 100:
            return {"success": False, "message": "每页数量必须在1-100之间"}

        # 获取账号信息
        account = db_manager.get_cookie_by_id(cookie_id)
        if not account:
            return {"success": False, "message": "账号不存在"}

        cookies_str = account['cookies_str']
        if not cookies_str:
            return {"success": False, "message": "账号cookies为空"}

        # 创建XianyuLive实例，传入正确的cookie_id
        from XianyuAutoAsync import XianyuLive
        xianyu_instance = XianyuLive(cookies_str, cookie_id)

        # 调用获取指定页商品信息的方法
        logger.info(f"开始获取账号 {cookie_id} 第{page_number}页商品信息（每页{page_size}条）")
        result = await xianyu_instance.get_item_list_info(page_number, page_size)

        # 关闭session
        await xianyu_instance.close_session()

        if result.get('error'):
            logger.error(f"获取商品信息失败: {result['error']}")
            return {"success": False, "message": result['error']}
        else:
            current_count = result.get('current_count', 0)
            logger.info(f"成功获取账号 {cookie_id} 第{page_number}页 {current_count} 个商品")
            return {
                "success": True,
                "message": f"成功获取第{page_number}页 {current_count} 个商品，详细信息已打印到控制台",
                "page_number": page_number,
                "page_size": page_size,
                "current_count": current_count
            }

    except Exception as e:
        logger.error(f"获取账号商品信息异常: {str(e)}")
        return {"success": False, "message": f"获取商品信息异常: {str(e)}"}


# ------------------------- 用户设置接口 -------------------------

@settings_router.get('/user-settings')
def get_user_settings(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取当前用户的设置"""
    from db_manager import db_manager
    try:
        user_id = current_user['user_id']
        settings = db_manager.get_user_settings(user_id)
        return settings
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@settings_router.put('/user-settings/{key}')
def update_user_setting(key: str, setting_data: dict, current_user: Dict[str, Any] = Depends(get_current_user)):
    """更新用户设置"""
    from db_manager import db_manager
    try:
        user_id = current_user['user_id']
        value = setting_data.get('value')
        description = setting_data.get('description', '')

        log_with_user('info', f"更新用户设置: {key} = {value}", current_user)

        success = db_manager.set_user_setting(user_id, key, value, description)
        if success:
            log_with_user('info', f"用户设置更新成功: {key}", current_user)
            return {'msg': 'setting updated', 'key': key, 'value': value}
        else:
            log_with_user('error', f"用户设置更新失败: {key}", current_user)
            raise HTTPException(status_code=400, detail='更新失败')
    except Exception as e:
        log_with_user('error', f"更新用户设置异常: {key} - {str(e)}", current_user)
        raise HTTPException(status_code=500, detail=str(e))

@settings_router.get('/user-settings/{key}')
def get_user_setting(key: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取用户特定设置"""
    from db_manager import db_manager
    try:
        user_id = current_user['user_id']
        setting = db_manager.get_user_setting(user_id, key)
        if setting:
            return setting
        else:
            raise HTTPException(status_code=404, detail='设置不存在')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------- 管理员专用接口 -------------------------

@admin_router.get('/admin/users')
def get_all_users(admin_user: Dict[str, Any] = Depends(require_admin)):
    """获取所有用户信息（管理员专用）"""
    from db_manager import db_manager
    try:
        log_with_user('info', "查询所有用户信息", admin_user)
        users = db_manager.get_all_users()

        # 为每个用户添加统计信息
        for user in users:
            user_id = user['id']
            # 统计用户的Cookie数量
            user_cookies = db_manager.get_all_cookies(user_id)
            user['cookie_count'] = len(user_cookies)

            # 统计用户的卡券数量
            user_cards = db_manager.get_all_cards(user_id)
            user['card_count'] = len(user_cards) if user_cards else 0

            # 隐藏密码字段
            if 'password_hash' in user:
                del user['password_hash']
            if 'password_hash_v2' in user:
                del user['password_hash_v2']

        log_with_user('info', f"返回用户信息，共 {len(users)} 个用户", admin_user)
        return {"users": users}
    except Exception as e:
        log_with_user('error', f"获取用户信息失败: {str(e)}", admin_user)
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.delete('/admin/users/{user_id}')
def delete_user(user_id: int, admin_user: Dict[str, Any] = Depends(require_admin)):
    """删除用户（管理员专用）"""
    from db_manager import db_manager
    try:
        # 不能删除管理员自己
        if user_id == admin_user['user_id']:
            log_with_user('warning', "尝试删除管理员自己", admin_user)
            raise HTTPException(status_code=400, detail="不能删除管理员自己")

        # 获取要删除的用户信息
        user_to_delete = db_manager.get_user_by_id(user_id)
        if not user_to_delete:
            raise HTTPException(status_code=404, detail="用户不存在")

        log_with_user('info', f"准备删除用户: {user_to_delete['username']} (ID: {user_id})", admin_user)

        # 删除用户及其相关数据
        success = db_manager.delete_user_and_data(user_id)

        if success:
            log_with_user('info', f"用户删除成功: {user_to_delete['username']} (ID: {user_id})", admin_user)
            return {"message": f"用户 {user_to_delete['username']} 删除成功"}
        else:
            log_with_user('error', f"用户删除失败: {user_to_delete['username']} (ID: {user_id})", admin_user)
            raise HTTPException(status_code=400, detail="删除失败")
    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"删除用户异常: {str(e)}", admin_user)
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.get('/admin/risk-control-logs')
async def get_admin_risk_control_logs(
    cookie_id: str = None,
    limit: int = 100,
    offset: int = 0,
    admin_user: Dict[str, Any] = Depends(require_admin)
):
    """获取风控日志（管理员专用）"""
    try:
        log_with_user('info', f"查询风控日志: cookie_id={cookie_id}, limit={limit}, offset={offset}", admin_user)

        # 获取风控日志
        logs = db_manager.get_risk_control_logs(cookie_id=cookie_id, limit=limit, offset=offset)
        total_count = db_manager.get_risk_control_logs_count(cookie_id=cookie_id)

        log_with_user('info', f"风控日志查询成功，共 {len(logs)} 条记录，总计 {total_count} 条", admin_user)

        return {
            "success": True,
            "data": logs,
            "total": total_count,
            "limit": limit,
            "offset": offset
        }

    except Exception as e:
        log_with_user('error', f"查询风控日志失败: {str(e)}", admin_user)
        return {"success": False, "message": f"查询失败: {str(e)}", "data": [], "total": 0}


@admin_router.get('/admin/cookies')
def get_admin_cookies(admin_user: Dict[str, Any] = Depends(require_admin)):
    """获取所有Cookie信息（管理员专用）"""
    try:
        log_with_user('info', "查询所有Cookie信息", admin_user)

        if cookie_manager.manager is None:
            return {
                "success": True,
                "cookies": [],
                "message": "CookieManager 未就绪"
            }

        # 获取所有用户的cookies
        from db_manager import db_manager
        all_users = db_manager.get_all_users()
        all_cookies = []

        for user in all_users:
            user_id = user['id']
            user_cookies = db_manager.get_all_cookies(user_id)
            for cookie_id, cookie_value in user_cookies.items():
                # 获取cookie详细信息
                cookie_details = db_manager.get_cookie_details(cookie_id)
                cookie_info = {
                    'cookie_id': cookie_id,
                    'user_id': user_id,
                    'username': user['username'],
                    'nickname': cookie_details.get('remark', '') if cookie_details else '',
                    'enabled': cookie_manager.manager.get_cookie_status(cookie_id)
                }
                all_cookies.append(cookie_info)

        log_with_user('info', f"获取到 {len(all_cookies)} 个Cookie", admin_user)
        return {
            "success": True,
            "cookies": all_cookies,
            "total": len(all_cookies)
        }

    except Exception as e:
        log_with_user('error', f"获取Cookie信息失败: {str(e)}", admin_user)
        return {
            "success": False,
            "cookies": [],
            "message": f"获取失败: {str(e)}"
        }


@admin_router.get('/admin/logs')
def get_system_logs(admin_user: Dict[str, Any] = Depends(require_admin),
                   lines: int = 100,
                   level: str = None):
    """获取系统日志（管理员专用）"""
    import os
    import glob
    from datetime import datetime

    try:
        log_with_user('info', f"查询系统日志，行数: {lines}, 级别: {level}", admin_user)

        # 查找日志文件
        log_files = glob.glob("logs/xianyu_*.log")
        logger.info(f"找到日志文件: {log_files}")

        if not log_files:
            logger.warning("未找到日志文件")
            return {"logs": [], "message": "未找到日志文件", "success": False}

        # 获取最新的日志文件
        latest_log_file = max(log_files, key=os.path.getctime)
        logger.info(f"使用最新日志文件: {latest_log_file}")

        logs = []
        try:
            with open(latest_log_file, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
                logger.info(f"读取到 {len(all_lines)} 行日志")

                # 如果指定了日志级别，进行过滤
                if level:
                    filtered_lines = [line for line in all_lines if f"| {level.upper()} |" in line]
                    logger.info(f"按级别 {level} 过滤后剩余 {len(filtered_lines)} 行")
                else:
                    filtered_lines = all_lines

                # 获取最后N行
                recent_lines = filtered_lines[-lines:] if len(filtered_lines) > lines else filtered_lines
                logger.info(f"取最后 {len(recent_lines)} 行日志")

                for line in recent_lines:
                    logs.append(line.strip())

        except Exception as e:
            logger.error(f"读取日志文件失败: {str(e)}")
            log_with_user('error', f"读取日志文件失败: {str(e)}", admin_user)
            return {"logs": [], "message": f"读取日志文件失败: {str(e)}", "success": False}

        log_with_user('info', f"返回日志记录 {len(logs)} 条", admin_user)
        logger.info(f"成功返回 {len(logs)} 条日志记录")

        return {
            "logs": logs,
            "log_file": latest_log_file,
            "total_lines": len(logs),
            "success": True
        }

    except Exception as e:
        logger.error(f"获取系统日志失败: {str(e)}")
        log_with_user('error', f"获取系统日志失败: {str(e)}", admin_user)
        return {"logs": [], "message": f"获取系统日志失败: {str(e)}", "success": False}

@admin_router.get('/admin/log-files')
def list_log_files(admin_user: Dict[str, Any] = Depends(require_admin)):
    """列出所有可用的系统日志文件"""
    import os
    import glob
    from datetime import datetime

    try:
        log_with_user('info', "查询日志文件列表", admin_user)

        log_dir = "logs"
        if not os.path.exists(log_dir):
            logger.warning("日志目录不存在")
            return {"success": True, "files": []}

        log_pattern = os.path.join(log_dir, "xianyu_*.log")
        log_files = glob.glob(log_pattern)

        files_info = []
        for file_path in log_files:
            try:
                stat_info = os.stat(file_path)
                files_info.append({
                    "name": os.path.basename(file_path),
                    "size": stat_info.st_size,
                    "modified_at": datetime.fromtimestamp(stat_info.st_mtime).isoformat(),
                    "modified_ts": stat_info.st_mtime
                })
            except OSError as e:
                logger.warning(f"读取日志文件信息失败 {file_path}: {e}")

        # 按修改时间倒序排序
        files_info.sort(key=lambda item: item.get("modified_ts", 0), reverse=True)

        logger.info(f"返回日志文件列表，共 {len(files_info)} 个文件")
        return {"success": True, "files": files_info}

    except Exception as e:
        logger.error(f"获取日志文件列表失败: {str(e)}")
        log_with_user('error', f"获取日志文件列表失败: {str(e)}", admin_user)
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.get('/admin/logs/export')
def export_log_file(file: str, admin_user: Dict[str, Any] = Depends(require_admin)):
    """导出指定的日志文件"""
    import os
    from fastapi.responses import StreamingResponse

    try:
        if not file:
            raise HTTPException(status_code=400, detail="缺少文件参数")

        safe_name = os.path.basename(file)
        log_dir = os.path.abspath("logs")
        target_path = os.path.abspath(os.path.join(log_dir, safe_name))

        # 防止目录遍历
        if not target_path.startswith(log_dir):
            log_with_user('warning', f"尝试访问非法日志文件: {file}", admin_user)
            raise HTTPException(status_code=400, detail="非法的日志文件路径")

        if not os.path.exists(target_path):
            log_with_user('warning', f"日志文件不存在: {file}", admin_user)
            raise HTTPException(status_code=404, detail="日志文件不存在")

        log_with_user('info', f"导出日志文件: {safe_name}", admin_user)
        def iter_file(path: str):
            file_handle = open(path, 'rb')
            try:
                while True:
                    chunk = file_handle.read(8192)
                    if not chunk:
                        break
                    yield chunk
            finally:
                file_handle.close()

        headers = {
            "Content-Disposition": f'attachment; filename="{safe_name}"'
        }
        return StreamingResponse(
            iter_file(target_path),
            media_type='text/plain; charset=utf-8',
            headers=headers
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"导出日志文件失败: {str(e)}")
        log_with_user('error', f"导出日志文件失败: {str(e)}", admin_user)
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.get('/admin/stats')
def get_system_stats(admin_user: Dict[str, Any] = Depends(require_admin)):
    """获取系统统计信息（管理员专用）"""
    from db_manager import db_manager
    try:
        log_with_user('info', "查询系统统计信息", admin_user)

        # 用户统计
        all_users = db_manager.get_all_users()
        total_users = len(all_users)

        # Cookie统计
        all_cookies = db_manager.get_all_cookies()
        total_cookies = len(all_cookies)

        # 活跃账号统计（启用状态的账号）
        active_cookies = 0
        for cookie_id in all_cookies.keys():
            status = db_manager.get_cookie_status(cookie_id)
            if status:
                active_cookies += 1

        # 卡券统计
        all_cards = db_manager.get_all_cards()
        total_cards = len(all_cards) if all_cards else 0

        # 关键词统计
        all_keywords = db_manager.get_all_keywords()
        total_keywords = sum(len(kw_list) for kw_list in all_keywords.values())

        # 订单统计
        total_orders = 0
        try:
            orders = db_manager.get_all_orders()
            total_orders = len(orders) if orders else 0
        except:
            pass

        stats = {
            "total_users": total_users,
            "total_cookies": total_cookies,
            "active_cookies": active_cookies,
            "total_cards": total_cards,
            "total_keywords": total_keywords,
            "total_orders": total_orders
        }

        log_with_user('info', f"系统统计信息查询完成: {stats}", admin_user)
        return stats

    except Exception as e:
        log_with_user('error', f"获取系统统计信息失败: {str(e)}", admin_user)
        raise HTTPException(status_code=500, detail=str(e))

# ------------------------- BI报表分析接口 -------------------------


def _dashboard_period(
    range_key: str,
    start_date: Optional[str],
    end_date: Optional[str],
) -> Dict[str, str]:
    try:
        end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else datetime.now().date()
        if range_key == "custom":
            if not start_date or not end_date:
                raise ValueError("自定义时间范围需要开始和结束日期")
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
        elif range_key == "yesterday":
            end = end - timedelta(days=1)
            start = end
        else:
            days = {"today": 1, "3days": 3, "7days": 7, "30days": 30}[range_key]
            start = end - timedelta(days=days - 1)
        if start > end:
            raise ValueError("开始日期不能晚于结束日期")
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "时间范围无效") from exc
    period_days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "previous_start_date": previous_start.isoformat(),
        "previous_end_date": previous_end.isoformat(),
    }


@orders_router.get('/api/dashboard/summary')
def get_dashboard_summary(
    range_key: Literal['today', 'yesterday', '3days', '7days', '30days', 'custom'] = Query('7days', alias='range'),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    period = _dashboard_period(range_key, start_date, end_date)
    is_admin = bool(current_user.get('is_admin')) or current_user.get('username') == ADMIN_USERNAME
    scoped_user_id = None if is_admin else current_user['user_id']
    valid_statuses = ['pending_ship', 'shipped', 'completed']
    current = db_manager.get_order_analytics(
        start_date=period['start_date'],
        end_date=period['end_date'],
        user_id=scoped_user_id,
        include_statuses=valid_statuses,
    )
    previous = db_manager.get_order_analytics(
        start_date=period['previous_start_date'],
        end_date=period['previous_end_date'],
        user_id=scoped_user_id,
        include_statuses=valid_statuses,
    )
    if 'error' in current or 'error' in previous:
        raise HTTPException(
            status_code=500,
            detail=current.get('error') or previous.get('error') or '仪表盘统计失败',
        )
    return {
        "success": True,
        "scope": "system" if is_admin else "user",
        "range": period,
        "stats": db_manager.get_dashboard_stats(scoped_user_id),
        "current": current,
        "previous": previous,
        "item_names": db_manager.get_dashboard_item_names(
            scoped_user_id,
            [item.get("item_id") for item in current["item_stats"]],
        ),
    }

@orders_router.get('/analytics/orders')
def get_order_analytics(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    获取订单分析数据（BI报表）

    Args:
        start_date: 开始日期 (格式: YYYY-MM-DD)
        end_date: 结束日期 (格式: YYYY-MM-DD)
    """
    from db_manager import db_manager
    try:
        log_with_user('info', f"查询订单分析数据: {start_date} - {end_date}", current_user)

        # 获取当前用户的ID
        user_id = current_user['user_id']

        # 定义有效订单状态（只统计这几种状态）
        valid_statuses = ['pending_ship', 'shipped', 'completed']

        # 调用数据库分析函数，传入包含状态
        analytics_data = db_manager.get_order_analytics(
            start_date=start_date,
            end_date=end_date,
            user_id=user_id,
            include_statuses=valid_statuses
        )

        if 'error' in analytics_data:
            log_with_user('error', f"获取订单分析数据失败: {analytics_data['error']}", current_user)
            raise HTTPException(status_code=500, detail=analytics_data['error'])

        log_with_user('info', "订单分析数据查询成功", current_user)
        return analytics_data

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"获取订单分析数据失败: {str(e)}", current_user)
        raise HTTPException(status_code=500, detail=str(e))

@orders_router.get('/analytics/orders/valid')
def get_valid_orders(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    获取有效订单详情列表（用于统计中的订单明细）

    Args:
        start_date: 开始日期 (格式: YYYY-MM-DD)
        end_date: 结束日期 (格式: YYYY-MM-DD)
    """
    from db_manager import db_manager
    try:
        log_with_user('info', f"查询有效订单列表: {start_date} - {end_date}", current_user)

        # 获取当前用户的ID
        user_id = current_user['user_id']

        # 定义有效订单状态
        valid_statuses = ['pending_ship', 'shipped', 'completed']

        # 调用数据库函数获取有效订单
        orders = db_manager.get_orders_for_analytics(
            start_date=start_date,
            end_date=end_date,
            user_id=user_id,
            include_statuses=valid_statuses
        )

        log_with_user('info', f"查询到 {len(orders)} 个有效订单", current_user)
        return {"orders": orders}

    except Exception as e:
        log_with_user('error', f"获取有效订单列表失败: {str(e)}", current_user)
        raise HTTPException(status_code=500, detail=str(e))

# ------------------------- 指定商品回复接口 -------------------------

@content_router.get("/itemReplays")
def get_all_items(current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取当前用户的所有商品回复信息"""
    try:
        # 只返回当前用户的商品信息
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        all_items = []
        for cookie_id in user_cookies.keys():
            items = db_manager.get_itemReplays_by_cookie(cookie_id)
            all_items.extend(items)

        return {"items": all_items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取商品回复信息失败: {str(e)}")

@content_router.get("/itemReplays/cookie/{cookie_id}")
def get_items_by_cookie(cookie_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取指定Cookie的商品信息"""
    try:
        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(user_id)

        if cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限访问该Cookie")

        items = db_manager.get_itemReplays_by_cookie(cookie_id)
        return {"items": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取商品信息失败: {str(e)}")

@content_router.put("/item-reply/{cookie_id}/{item_id}")
def update_item_reply(
    cookie_id: str,
    item_id: str,
    data: dict,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    更新指定账号和商品的回复内容
    """
    try:
        user_id = current_user['user_id']
        from db_manager import db_manager

        # 验证cookie是否属于用户
        user_cookies = db_manager.get_all_cookies(user_id)
        if cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限访问该Cookie")

        reply_content = data.get("reply_content", "").strip()
        if not reply_content:
            raise HTTPException(status_code=400, detail="回复内容不能为空")

        db_manager.update_item_reply(cookie_id=cookie_id, item_id=item_id, reply_content=reply_content)

        return {"message": "商品回复更新成功"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新商品回复失败: {str(e)}")

@content_router.delete("/item-reply/{cookie_id}/{item_id}")
def delete_item_reply(cookie_id: str, item_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    删除指定账号cookie_id和商品item_id的商品回复
    """
    try:
        user_id = current_user['user_id']
        user_cookies = db_manager.get_all_cookies(user_id)
        if cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限访问该Cookie")

        success = db_manager.delete_item_reply(cookie_id, item_id)
        if not success:
            raise HTTPException(status_code=404, detail="商品回复不存在")

        return {"message": "商品回复删除成功"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除商品回复失败: {str(e)}")

class ItemToDelete(BaseModel):
    cookie_id: str
    item_id: str

class BatchDeleteRequest(BaseModel):
    items: List[ItemToDelete]

@content_router.delete("/item-reply/batch")
async def batch_delete_item_reply(
    req: BatchDeleteRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    批量删除商品回复
    """
    user_id = current_user['user_id']
    from db_manager import db_manager

    # 先校验当前用户是否有权限删除每个cookie对应的回复
    user_cookies = db_manager.get_all_cookies(user_id)
    for item in req.items:
        if item.cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail=f"无权限访问Cookie {item.cookie_id}")

    result = db_manager.batch_delete_item_replies([item.dict() for item in req.items])
    return {
        "success_count": result["success_count"],
        "failed_count": result["failed_count"]
    }

@content_router.get("/item-reply/{cookie_id}/{item_id}")
def get_item_reply(cookie_id: str, item_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    获取指定账号cookie_id和商品item_id的商品回复内容
    """
    try:
        user_id = current_user['user_id']
        # 校验cookie_id是否属于当前用户
        user_cookies = db_manager.get_all_cookies(user_id)
        if cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限访问该Cookie")

        # 获取指定商品回复
        item_replies = db_manager.get_itemReplays_by_cookie(cookie_id)
        # 找对应item_id的回复
        item_reply = next((r for r in item_replies if r['item_id'] == item_id), None)

        if item_reply is None:
            raise HTTPException(status_code=404, detail="商品回复不存在")

        return item_reply

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取商品回复失败: {str(e)}")


# ------------------------- 数据库备份和恢复接口 -------------------------

@admin_router.get('/admin/backup/download')
def download_database_backup(admin_user: Dict[str, Any] = Depends(require_admin)):
    """下载数据库备份文件（管理员专用）"""
    import os
    from fastapi.responses import FileResponse
    from datetime import datetime

    try:
        log_with_user('info', "请求下载数据库备份", admin_user)

        # 使用db_manager的实际数据库路径
        from db_manager import db_manager
        db_file_path = db_manager.db_path

        # 检查数据库文件是否存在
        if not os.path.exists(db_file_path):
            log_with_user('error', f"数据库文件不存在: {db_file_path}", admin_user)
            raise HTTPException(status_code=404, detail="数据库文件不存在")

        # 生成带时间戳的文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        download_filename = f"xianyu_backup_{timestamp}.db"

        log_with_user('info', f"开始下载数据库备份: {download_filename}", admin_user)

        return FileResponse(
            path=db_file_path,
            filename=download_filename,
            media_type='application/octet-stream'
        )

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"下载数据库备份失败: {str(e)}", admin_user)
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.post('/admin/backup/upload')
async def upload_database_backup(admin_user: Dict[str, Any] = Depends(require_admin),
                                backup_file: UploadFile = File(...)):
    """上传并恢复数据库备份文件（管理员专用）"""
    import os
    import shutil
    import sqlite3
    from datetime import datetime

    try:
        log_with_user('info', f"开始上传数据库备份: {backup_file.filename}", admin_user)

        # 验证文件类型
        if not backup_file.filename.endswith('.db'):
            log_with_user('warning', f"无效的备份文件类型: {backup_file.filename}", admin_user)
            raise HTTPException(status_code=400, detail="只支持.db格式的数据库文件")

        # 验证文件大小（限制100MB）
        content = await backup_file.read()
        if len(content) > 100 * 1024 * 1024:  # 100MB
            log_with_user('warning', f"备份文件过大: {len(content)} bytes", admin_user)
            raise HTTPException(status_code=400, detail="备份文件大小不能超过100MB")

        # 验证是否为有效的SQLite数据库文件
        temp_file_path = f"temp_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"

        try:
            # 保存临时文件
            with open(temp_file_path, 'wb') as temp_file:
                temp_file.write(content)

            # 验证数据库文件完整性
            conn = sqlite3.connect(temp_file_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            conn.close()

            # 检查是否包含必要的表
            table_names = [table[0] for table in tables]
            required_tables = ['users', 'cookies']  # 最基本的表

            missing_tables = [table for table in required_tables if table not in table_names]
            if missing_tables:
                log_with_user('warning', f"备份文件缺少必要的表: {missing_tables}", admin_user)
                raise HTTPException(status_code=400, detail=f"备份文件不完整，缺少表: {', '.join(missing_tables)}")

            log_with_user('info', f"备份文件验证通过，包含 {len(table_names)} 个表", admin_user)

        except sqlite3.Error as e:
            log_with_user('error', f"备份文件验证失败: {str(e)}", admin_user)
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            raise HTTPException(status_code=400, detail="无效的数据库文件")

        # 备份当前数据库
        from db_manager import db_manager
        current_db_path = db_manager.db_path

        # 生成备份文件路径（与原数据库在同一目录）
        db_dir = os.path.dirname(current_db_path)
        backup_filename = f"xianyu_data_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        backup_current_path = os.path.join(db_dir, backup_filename)

        if os.path.exists(current_db_path):
            shutil.copy2(current_db_path, backup_current_path)
            log_with_user('info', f"当前数据库已备份为: {backup_current_path}", admin_user)

        # 关闭当前数据库连接
        if hasattr(db_manager, 'conn') and db_manager.conn:
            db_manager.conn.close()
            log_with_user('info', "已关闭当前数据库连接", admin_user)

        # 替换数据库文件
        shutil.move(temp_file_path, current_db_path)
        log_with_user('info', f"数据库文件已替换: {current_db_path}", admin_user)

        # 重新初始化数据库连接（使用原有的db_path）
        db_manager.__init__(db_manager.db_path)
        log_with_user('info', "数据库连接已重新初始化", admin_user)

        # 验证新数据库
        try:
            test_users = db_manager.get_all_users()
            log_with_user('info', f"数据库恢复成功，包含 {len(test_users)} 个用户", admin_user)
        except Exception as e:
            log_with_user('error', f"数据库恢复后验证失败: {str(e)}", admin_user)
            # 如果验证失败，尝试恢复原数据库
            if os.path.exists(backup_current_path):
                shutil.copy2(backup_current_path, current_db_path)
                db_manager.__init__()
                log_with_user('info', "已恢复原数据库", admin_user)
            raise HTTPException(status_code=500, detail="数据库恢复失败，已回滚到原数据库")

        return {
            "success": True,
            "message": "数据库恢复成功",
            "backup_file": backup_current_path,
            "user_count": len(test_users)
        }

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"上传数据库备份失败: {str(e)}", admin_user)
        # 清理临时文件
        if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.get('/admin/backup/list')
def list_backup_files(admin_user: Dict[str, Any] = Depends(require_admin)):
    """列出服务器上的备份文件（管理员专用）"""
    import os
    import glob
    from datetime import datetime

    try:
        log_with_user('info', "查询备份文件列表", admin_user)

        # 查找备份文件（在data目录中）
        backup_files = glob.glob("data/xianyu_data_backup_*.db")

        backup_list = []
        for file_path in backup_files:
            try:
                stat = os.stat(file_path)
                backup_list.append({
                    'filename': os.path.basename(file_path),
                    'size': stat.st_size,
                    'size_mb': round(stat.st_size / (1024 * 1024), 2),
                    'created_time': datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                    'modified_time': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                })
            except Exception as e:
                log_with_user('warning', f"读取备份文件信息失败: {file_path} - {str(e)}", admin_user)

        # 按修改时间倒序排列
        backup_list.sort(key=lambda x: x['modified_time'], reverse=True)

        log_with_user('info', f"找到 {len(backup_list)} 个备份文件", admin_user)

        return {
            "backups": backup_list,
            "total": len(backup_list)
        }

    except Exception as e:
        log_with_user('error', f"查询备份文件列表失败: {str(e)}", admin_user)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------- 系统管理接口 -------------------------

@admin_router.post('/admin/reload-cache')
async def reload_system_cache(admin_user: Dict[str, Any] = Depends(require_admin)):
    """刷新系统缓存（管理员专用）"""
    try:
        log_with_user('info', "刷新系统缓存", admin_user)

        # 这里可以添加实际的缓存刷新逻辑
        # 例如：重新加载配置、清理内存缓存等

        log_with_user('info', "系统缓存刷新成功", admin_user)
        return {"success": True, "message": "系统缓存已刷新"}

    except Exception as e:
        log_with_user('error', f"刷新系统缓存失败: {str(e)}", admin_user)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------- 数据管理接口 -------------------------

@admin_router.get('/admin/data/{table_name}')
def get_table_data(table_name: str, admin_user: Dict[str, Any] = Depends(require_admin)):
    """获取指定表的所有数据（管理员专用）"""
    from db_manager import db_manager
    try:
        log_with_user('info', f"查询表数据: {table_name}", admin_user)

        # 验证表名安全性
        allowed_tables = [
            'users', 'cookies', 'cookie_status', 'keywords', 'default_replies', 'default_reply_records',
            'ai_reply_settings', 'ai_conversations', 'ai_item_cache', 'ai_training_rules',
            'ai_item_knowledge_profiles', 'ai_item_knowledge_versions', 'item_info',
            'message_notifications', 'cards', 'delivery_rules', 'notification_channels',
            'user_settings', 'system_settings', 'email_verifications', 'captcha_codes', 'orders', "item_replay",
            'risk_control_logs'
        ]

        if table_name not in allowed_tables:
            log_with_user('warning', f"尝试访问不允许的表: {table_name}", admin_user)
            raise HTTPException(status_code=400, detail="不允许访问该表")

        # 获取表数据
        data, columns = db_manager.get_table_data(table_name)

        log_with_user('info', f"表 {table_name} 查询成功，共 {len(data)} 条记录", admin_user)

        return {
            "success": True,
            "data": data,
            "columns": columns,
            "count": len(data)
        }

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"查询表数据失败: {table_name} - {str(e)}", admin_user)
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.delete('/admin/data/{table_name}/{record_id}')
def delete_table_record(table_name: str, record_id: str, admin_user: Dict[str, Any] = Depends(require_admin)):
    """删除指定表的指定记录（管理员专用）"""
    from db_manager import db_manager
    try:
        log_with_user('info', f"删除表记录: {table_name}.{record_id}", admin_user)

        # 验证表名安全性
        allowed_tables = [
            'users', 'cookies', 'cookie_status', 'keywords', 'default_replies', 'default_reply_records',
            'ai_reply_settings', 'ai_conversations', 'ai_item_cache', 'ai_training_rules',
            'ai_item_knowledge_profiles', 'ai_item_knowledge_versions', 'item_info',
            'message_notifications', 'cards', 'delivery_rules', 'notification_channels',
            'user_settings', 'system_settings', 'email_verifications', 'captcha_codes', 'orders','item_replay'
        ]

        if table_name not in allowed_tables:
            log_with_user('warning', f"尝试删除不允许的表记录: {table_name}", admin_user)
            raise HTTPException(status_code=400, detail="不允许操作该表")

        # 特殊保护：不能删除管理员用户
        if table_name == 'users' and record_id == str(admin_user['user_id']):
            log_with_user('warning', "尝试删除管理员自己", admin_user)
            raise HTTPException(status_code=400, detail="不能删除管理员自己")

        # 删除记录
        success = db_manager.delete_table_record(table_name, record_id)

        if success:
            log_with_user('info', f"表记录删除成功: {table_name}.{record_id}", admin_user)
            return {"success": True, "message": "删除成功"}
        else:
            log_with_user('warning', f"表记录删除失败: {table_name}.{record_id}", admin_user)
            raise HTTPException(status_code=400, detail="删除失败，记录可能不存在")

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"删除表记录异常: {table_name}.{record_id} - {str(e)}", admin_user)
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.delete('/admin/data/{table_name}')
def clear_table_data(table_name: str, admin_user: Dict[str, Any] = Depends(require_admin)):
    """清空指定表的所有数据（管理员专用）"""
    from db_manager import db_manager
    try:
        log_with_user('info', f"清空表数据: {table_name}", admin_user)

        # 验证表名安全性
        allowed_tables = [
            'cookies', 'cookie_status', 'keywords', 'default_replies', 'default_reply_records',
            'ai_reply_settings', 'ai_conversations', 'ai_item_cache', 'ai_training_rules',
            'ai_item_knowledge_profiles', 'ai_item_knowledge_versions', 'item_info',
            'message_notifications', 'cards', 'delivery_rules', 'notification_channels',
            'user_settings', 'system_settings', 'email_verifications', 'captcha_codes', 'orders', 'item_replay',
            'risk_control_logs'
        ]

        # 不允许清空用户表
        if table_name == 'users':
            log_with_user('warning', "尝试清空用户表", admin_user)
            raise HTTPException(status_code=400, detail="不允许清空用户表")

        if table_name not in allowed_tables:
            log_with_user('warning', f"尝试清空不允许的表: {table_name}", admin_user)
            raise HTTPException(status_code=400, detail="不允许清空该表")

        # 清空表数据
        success = db_manager.clear_table_data(table_name)

        if success:
            log_with_user('info', f"表数据清空成功: {table_name}", admin_user)
            return {"success": True, "message": "清空成功"}
        else:
            log_with_user('warning', f"表数据清空失败: {table_name}", admin_user)
            raise HTTPException(status_code=400, detail="清空失败")

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"清空表数据异常: {table_name} - {str(e)}", admin_user)
        raise HTTPException(status_code=500, detail=str(e))


# 商品多规格管理API
@content_router.put("/items/{cookie_id}/{item_id}/multi-spec")
def update_item_multi_spec(
    cookie_id: str,
    item_id: str,
    spec_data: dict,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """更新商品的多规格状态"""
    try:
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(current_user['user_id'])
        if cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        is_multi_spec = spec_data.get('is_multi_spec', False)

        success = db_manager.update_item_multi_spec_status(cookie_id, item_id, is_multi_spec)

        if success:
            return {"message": f"商品多规格状态已{'开启' if is_multi_spec else '关闭'}"}
        else:
            raise HTTPException(status_code=404, detail="商品不存在")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 商品多数量发货管理API
@content_router.put("/items/{cookie_id}/{item_id}/multi-quantity-delivery")
def update_item_multi_quantity_delivery(
    cookie_id: str,
    item_id: str,
    delivery_data: dict,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """更新商品的多数量发货状态"""
    try:
        from db_manager import db_manager
        user_cookies = db_manager.get_all_cookies(current_user['user_id'])
        if cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限操作该Cookie")

        multi_quantity_delivery = delivery_data.get('multi_quantity_delivery', False)

        success = db_manager.update_item_multi_quantity_delivery_status(cookie_id, item_id, multi_quantity_delivery)

        if success:
            return {"message": f"商品多数量发货状态已{'开启' if multi_quantity_delivery else '关闭'}"}
        else:
            raise HTTPException(status_code=404, detail="商品不存在")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))





# ==================== 订单管理接口 ====================

async def _fetch_order_details_for_sync(**kwargs):
    from utils.order_fetcher_optimized import process_orders_batch

    return await process_orders_batch(
        order_ids=kwargs["order_ids"],
        cookie_id=kwargs["cookie_id"],
        cookie_string=kwargs["cookie_string"],
        max_concurrent=3,
        timeout=30,
        headless=True,
        use_pool=True,
        force_refresh=True,
    )


async def _sync_recent_orders(
    current_user: Dict[str, Any],
    cookie_id: Optional[str] = None,
    days: int = 90,
) -> JSONResponse:
    user_cookies = db_manager.get_all_cookies(current_user["user_id"])
    if cookie_id:
        if cookie_id not in user_cookies:
            raise HTTPException(status_code=404, detail="账号不存在或无权访问")
        user_cookies = {cookie_id: user_cookies[cookie_id]}
    if not user_cookies:
        raise HTTPException(status_code=400, detail="当前没有可同步的闲鱼账号")

    client = XianyuOrderListClient()
    coordinator = OrderSyncCoordinator(
        db_manager,
        discoverer=client.discover,
        detail_fetcher=_fetch_order_details_for_sync,
    )
    account_results = []
    total_summary = {
        "total_seen": 0,
        "discovered": 0,
        "status_updated": 0,
        "details_updated": 0,
        "unchanged": 0,
        "failed": 0,
    }
    for account_id, cookie_string in user_cookies.items():
        result = await coordinator.sync_account(
            cookie_id=account_id,
            cookie_string=cookie_string,
            days=days,
        )
        account_results.append({"cookie_id": account_id, **result})
        for key in total_summary:
            total_summary[key] += int((result.get("summary") or {}).get(key) or 0)

    requires_login = [row["cookie_id"] for row in account_results if row.get("requires_login")]
    successful = sum(1 for row in account_results if row.get("success"))
    partial = any(row.get("partial") for row in account_results) or (successful > 0 and successful < len(account_results))
    payload = {
        "success": successful == len(account_results),
        "partial": partial,
        "message": (
            "订单同步完成"
            if successful == len(account_results)
            else "登录状态已过期，请先在账号管理更新登录状态"
            if requires_login and successful == 0
            else "订单同步部分完成"
        ),
        "days": days,
        "summary": total_summary,
        "requires_login": requires_login,
        "accounts": account_results,
    }
    status_code = 409 if requires_login and successful == 0 else 200
    return JSONResponse(status_code=status_code, content=payload)


@orders_router.post('/api/orders/sync')
async def sync_recent_orders(
    request: OrderSyncRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Discover and reconcile recent seller orders with truthful partial failures."""
    return await _sync_recent_orders(
        current_user=current_user,
        cookie_id=request.cookie_id,
        days=request.days,
    )

@orders_router.get('/api/orders')
def get_user_orders(
    current_user: Dict[str, Any] = Depends(get_current_user),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    cookie_id: Optional[str] = Query(None, description="筛选Cookie ID"),
    status: Optional[str] = Query(None, description="筛选状态")
):
    """获取当前用户的订单信息（支持分页）"""
    try:
        from db_manager import db_manager

        user_id = current_user['user_id']
        log_with_user('info', f"查询用户订单信息 (page={page}, page_size={page_size})", current_user)

        # 获取用户的所有Cookie
        user_cookies = db_manager.get_all_cookies(user_id)

        # 如果指定了cookie_id筛选
        if cookie_id and cookie_id in user_cookies:
            user_cookies = {cookie_id: user_cookies[cookie_id]}

        # 获取所有订单数据
        all_orders = []
        # 先获取所有商品的 item_id 到 item_title 的映射
        item_titles = {}
        with db_manager.lock:
            cursor = db_manager.conn.cursor()
            cursor.execute('SELECT item_id, item_title FROM item_info')
            for row in cursor.fetchall():
                item_titles[row[0]] = row[1]

        for cid in user_cookies.keys():
            orders = db_manager.get_orders_by_cookie(cid, limit=1000)
            for order in orders:
                order['cookie_id'] = cid
                # 添加 item_title 字段
                order['item_title'] = item_titles.get(order.get('item_id'), '')
                # 状态筛选
                if status and order.get('status') != status:
                    continue
                all_orders.append(order)

        # 按创建时间倒序排列
        all_orders.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        # 分页处理
        total = len(all_orders)
        total_pages = (total + page_size - 1) // page_size
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_orders = all_orders[start_idx:end_idx]

        log_with_user('info', f"用户订单查询成功，共 {total} 条记录，第 {page}/{total_pages} 页", current_user)
        return {
            "success": True,
            "data": paginated_orders,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages
        }

    except Exception as e:
        log_with_user('error', f"查询用户订单失败: {str(e)}", current_user)
        raise HTTPException(status_code=500, detail=f"查询订单失败: {str(e)}")


@orders_router.get('/api/orders/{order_id}')
def get_order_detail(order_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """获取订单详情"""
    try:
        from db_manager import db_manager

        user_id = current_user['user_id']
        log_with_user('info', f"查询订单详情: {order_id}", current_user)

        # 获取用户的所有Cookie
        user_cookies = db_manager.get_all_cookies(user_id)

        # 在用户的订单中查找
        for cookie_id in user_cookies.keys():
            order = db_manager.get_order_by_id(order_id)
            if order and order.get('cookie_id') == cookie_id:
                log_with_user('info', f"订单详情查询成功: {order_id}", current_user)
                return {"success": True, "data": order}

        log_with_user('warning', f"订单不存在或无权访问: {order_id}", current_user)
        raise HTTPException(status_code=404, detail="订单不存在或无权访问")

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"查询订单详情失败: {str(e)}", current_user)
        raise HTTPException(status_code=500, detail=f"查询订单详情失败: {str(e)}")


@orders_router.delete('/api/orders/{order_id}')
def delete_order(order_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """删除订单"""
    try:
        from db_manager import db_manager

        user_id = current_user['user_id']
        log_with_user('info', f"删除订单: {order_id}", current_user)

        # 获取用户的所有Cookie
        user_cookies = db_manager.get_all_cookies(user_id)

        # 验证订单属于当前用户
        order = db_manager.get_order_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="订单不存在")

        if order.get('cookie_id') not in user_cookies:
            raise HTTPException(status_code=403, detail="无权删除此订单")

        # 删除订单
        success = db_manager.delete_order(order_id)
        if success:
            log_with_user('info', f"订单删除成功: {order_id}", current_user)
            return {"success": True, "message": "删除成功"}
        else:
            raise HTTPException(status_code=500, detail="删除失败")

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"删除订单失败: {str(e)}", current_user)
        raise HTTPException(status_code=500, detail=f"删除订单失败: {str(e)}")


@orders_router.post('/api/orders/{order_id}/refresh')
async def refresh_single_order(
    order_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """刷新单条订单状态"""
    try:
        from db_manager import db_manager
        from utils.order_fetcher_optimized import process_orders_batch

        user_id = current_user['user_id']
        log_with_user('info', f"刷新单条订单: {order_id}", current_user)

        # 获取用户的所有Cookie
        user_cookies = db_manager.get_all_cookies(user_id)

        # 验证订单存在且属于当前用户
        order = db_manager.get_order_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="订单不存在")

        cookie_id = order.get('cookie_id')
        if not cookie_id or cookie_id not in user_cookies:
            raise HTTPException(status_code=403, detail="无权刷新此订单")

        cookies_str = user_cookies[cookie_id]
        if not cookies_str:
            raise HTTPException(status_code=400, detail="Cookie无效")

        # 调用批量刷新函数处理单条订单
        batch_results = await process_orders_batch(
            order_ids=[order_id],
            cookie_id=cookie_id,
            cookie_string=cookies_str,
            max_concurrent=1,
            timeout=30,
            headless=True,
            use_pool=True,
            force_refresh=True
        )

        if not batch_results or len(batch_results) == 0:
            raise HTTPException(status_code=500, detail="刷新失败")

        result = batch_results[0]
        if result.get('error'):
            status_code = 409 if result.get('requires_login') or result.get('error_code') == 'session_expired' else 502
            raise HTTPException(
                status_code=status_code,
                detail={
                    "code": result.get('error_code') or 'order_refresh_failed',
                    "message": result.get('error'),
                    "requires_login": status_code == 409,
                },
            )

        order_status = normalize_order_status(
            result.get('order_status', 'unknown'),
            result.get('status_text', ''),
        )
        update_result = db_manager.apply_order_sync_update(
            order_id=order_id,
            cookie_id=cookie_id,
            incoming_status=order_status,
            platform_status_code=str(result.get('api_status') or result.get('order_status') or ''),
            platform_status_text=str(result.get('status_text') or ''),
            status_source='order_detail',
            sync_error='' if order_status != 'unknown' else '无法确认平台订单状态',
            item_id=result.get('item_id') or None,
            buyer_id=result.get('buyer_id') or None,
            spec_name=result.get('spec_name') or None,
            spec_value=result.get('spec_value') or None,
            quantity=result.get('quantity') or None,
            amount=result.get('amount') or None,
            created_at=result.get('order_time') or None,
            receiver_name=result.get('receiver_name') or None,
            receiver_phone=result.get('receiver_phone') or None,
            receiver_address=result.get('receiver_address') or None,
            receiver_city=result.get('receiver_city') or None,
        )

        log_with_user('info', f"订单刷新成功: {order_id}, 新状态: {order_status}", current_user)
        return JSONResponse({
            "success": True,
            "message": "订单刷新成功",
            "data": {
                "order_id": order_id,
                "order_status": update_result.get('new_status', order_status),
                "status_changed": bool(update_result.get('status_changed')),
                "details_changed": bool(update_result.get('details_changed')),
            }
        })

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"刷新订单失败: {str(e)}", current_user)
        raise HTTPException(status_code=500, detail=f"刷新订单失败: {str(e)}")


def check_order_data_completeness(order: Dict[str, Any]) -> bool:
    """
    检查订单数据是否完整

    Args:
        order: 订单数据字典

    Returns:
        True表示数据完整，False表示需要刷新
    """
    # 检查关键字段是否为空或为'unknown'
    incomplete_conditions = [
        not order.get('receiver_name') or order.get('receiver_name') == 'unknown',
        not order.get('receiver_phone') or order.get('receiver_phone') == 'unknown',
        not order.get('receiver_address') or order.get('receiver_address') == 'unknown',
        order.get('order_status') == 'unknown',
        not order.get('buyer_id') or order.get('buyer_id') == 'unknown',
    ]

    return not any(incomplete_conditions)


@orders_router.put('/api/orders/{order_id}')
async def update_order(
    order_id: str,
    update_data: dict,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    更新订单信息
    自动检查订单数据完整性，如数据不完整则通过 Playwright 从订单详情页获取最新完整数据
    获取完整信息包括：订单ID、商品ID、买家ID、规格、数量、金额、订单状态、收货人信息
    """
    try:
        from db_manager import db_manager
        from utils.order_fetcher_optimized import fetch_order_complete

        user_id = current_user['user_id']
        log_with_user('info', f"更新订单: {order_id}, 数据: {update_data}", current_user)

        # 获取用户的所有Cookie
        user_cookies = db_manager.get_all_cookies(user_id)

        # 验证订单属于当前用户
        order = db_manager.get_order_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="订单不存在")

        if order.get('cookie_id') not in user_cookies:
            raise HTTPException(status_code=403, detail="无权修改此订单")

        # 检查订单数据完整性
        is_complete = check_order_data_completeness(order)

        if not is_complete:
            log_with_user('info', f"订单 {order_id} 数据不完整，开始使用Playwright获取完整数据", current_user)

            # 获取该订单对应的Cookie字符串
            cookie_id = order.get('cookie_id')
            cookie_string = user_cookies.get(cookie_id)

            if cookie_string:

                try:
                    # 使用优化后的合并函数：一次浏览器访问获取所有数据
                    log_with_user('info', f"使用优化方法获取订单 {order_id} 的完整数据", current_user)

                    complete_result = await fetch_order_complete(
                        order_id=order_id,
                        cookie_id=cookie_id,
                        cookie_string=cookie_string,
                        timeout=30,
                        headless=True,
                        use_pool=True  # 使用浏览器池
                    )

                    if complete_result:
                        log_with_user('info', f"成功获取订单 {order_id} 的完整数据（一次浏览器调用）", current_user)

                        # 状态码映射（如果需要转换）
                        order_status = complete_result.get('order_status', 'unknown')
                        if order_status and isinstance(order_status, str) and order_status.isdigit():
                            status_mapping = {
                                '1': 'processing',
                                '2': 'pending_ship',
                                '3': 'shipped',
                                '4': 'completed',
                                '5': 'refunding',
                                '6': 'cancelled',
                                '7': 'refunding',
                                '8': 'cancelled',
                                '9': 'refunding',
                                '10': 'cancelled',
                            }
                            order_status = status_mapping.get(order_status, order_status)

                        # 构建要更新的完整数据
                        refresh_data = {
                            'order_id': order_id,
                            'item_id': complete_result.get('item_id') or order.get('item_id'),
                            'buyer_id': complete_result.get('buyer_id') or order.get('buyer_id'),
                            'order_status': order_status or order.get('order_status'),
                            'spec_name': complete_result.get('spec_name') or None,
                            'spec_value': complete_result.get('spec_value') or None,
                            'quantity': complete_result.get('quantity') or None,
                            'amount': complete_result.get('amount') or None,
                            'created_at': complete_result.get('order_time') or None,
                            'receiver_name': complete_result.get('receiver_name') or None,
                            'receiver_phone': complete_result.get('receiver_phone') or None,
                            'receiver_address': complete_result.get('receiver_address') or None
                        }

                        # 更新数据库
                        db_manager.insert_or_update_order(**refresh_data)
                        log_with_user('info', f"订单 {order_id} 完整数据已更新到数据库", current_user)
                    else:
                        log_with_user('warning', f"订单 {order_id} 详情获取失败，继续使用现有数据", current_user)

                except Exception as e:
                    log_with_user('error', f"获取订单 {order_id} 详情时出错: {str(e)}", current_user)
                    # 继续执行，即使刷新失败也允许用户手动更新
            else:
                log_with_user('warning', f"订单 {order_id} 的Cookie信息不完整，无法刷新", current_user)

        # 提取可更新的字段
        allowed_fields = {
            'item_id', 'buyer_id', 'spec_name', 'spec_value',
            'quantity', 'amount', 'order_status',
            'receiver_name', 'receiver_phone', 'receiver_address',
            'system_shipped', 'created_at'
        }

        # 只保留允许更新的字段
        filtered_data = {k: v for k, v in update_data.items() if k in allowed_fields}

        if not filtered_data:
            # 如果没有用户提供的更新数据
            if not is_complete:
                # 数据不完整，已经进行了自动刷新，返回刷新后的订单
                updated_order = db_manager.get_order_by_id(order_id)
                return {
                    "success": True,
                    "message": "订单数据已自动刷新",
                    "data": updated_order,
                    "refreshed": True
                }
            else:
                # 数据完整，直接返回当前订单信息
                updated_order = db_manager.get_order_by_id(order_id)
                return {
                    "success": True,
                    "message": "订单数据已是最新",
                    "data": updated_order,
                    "refreshed": False
                }

        # 应用用户提供的更新
        success = db_manager.insert_or_update_order(
            order_id=order_id,
            **filtered_data
        )

        if success:
            log_with_user('info', f"订单更新成功: {order_id}", current_user)
            # 返回更新后的订单
            updated_order = db_manager.get_order_by_id(order_id)
            return {
                "success": True,
                "message": "更新成功",
                "data": updated_order,
                "refreshed": not is_complete  # 标记是否进行了自动刷新
            }
        else:
            raise HTTPException(status_code=500, detail="更新失败")

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"更新订单失败: {str(e)}", current_user)
        raise HTTPException(status_code=500, detail=f"更新订单失败: {str(e)}")


@orders_router.post('/api/orders/refresh', deprecated=True)
async def refresh_orders_status(
    cookie_id: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    智能刷新订单状态
    1. 从数据库获取订单列表（支持筛选）
    2. 对非'已发货'状态的订单，使用Playwright查询最新状态
    3. 更新数据库中有变化的订单
    """
    # 兼容旧前端；状态筛选不再缩小核对范围，避免漏掉签收或退款变化。
    return await _sync_recent_orders(current_user=current_user, cookie_id=cookie_id, days=90)

    try:
        from db_manager import db_manager
        from utils.order_fetcher_optimized import process_orders_batch

        user_id = current_user['user_id']
        log_with_user('info', f"开始智能刷新订单状态（优化版：并发处理） (cookie_id={cookie_id}, status={status})", current_user)

        # 获取用户的所有Cookie
        user_cookies = db_manager.get_all_cookies(user_id)

        # 如果指定了cookie_id，只使用该Cookie
        if cookie_id:
            if cookie_id not in user_cookies:
                raise HTTPException(status_code=404, detail="Cookie不存在或无权访问")
            user_cookies = {cookie_id: user_cookies[cookie_id]}

        # 获取需要刷新的订单
        orders_to_refresh = []
        for cid in user_cookies.keys():
            # 获取该Cookie的所有订单
            orders = db_manager.get_orders_by_cookie(cid, limit=1000)

            # 筛选需要刷新的订单
            for order in orders:
                # 如果指定了状态筛选，只刷新该状态的订单
                if status and order.get('status') != status:
                    continue

                order_status = order.get('status', 'unknown')

                # 判断是否需要刷新：只根据状态判断
                # 稳定状态（已发货、交易成功、交易关闭）的订单不需要刷新
                needs_refresh = order_status not in ['shipped', 'completed', 'cancelled']

                if needs_refresh:
                    orders_to_refresh.append({
                        'order_id': order['order_id'],
                        'cookie_id': cid,
                        'current_status': order_status
                    })

        log_with_user('info', f"找到 {len(orders_to_refresh)} 个需要刷新的订单", current_user)

        if not orders_to_refresh:
            return JSONResponse({
                "success": True,
                "message": "没有需要刷新的订单",
                "summary": {
                    "total": 0,
                    "updated": 0,
                    "no_change": 0,
                    "failed": 0
                },
                "results": []
            })

        # 刷新订单信息（包括状态、买家ID、金额等）
        updated_count = 0
        failed_count = 0
        no_change_count = 0
        refresh_results = []

        # 按cookie_id分组订单（因为每个cookie需要单独的浏览器实例）
        orders_by_cookie = {}
        for order_info in orders_to_refresh:
            cid = order_info['cookie_id']
            if cid not in orders_by_cookie:
                orders_by_cookie[cid] = []
            orders_by_cookie[cid].append(order_info)

        # 对每个cookie的订单进行并发批量处理
        for cid, cookie_orders in orders_by_cookie.items():
            cookies_str = user_cookies[cid]
            if not cookies_str:
                log_with_user('warning', f"Cookie {cid} 的值为空，跳过", current_user)
                failed_count += len(cookie_orders)
                continue

            # 提取订单ID列表
            order_ids = [o['order_id'] for o in cookie_orders]
            log_with_user('info', f"使用并发处理Cookie {cid} 的 {len(order_ids)} 个订单", current_user)

            # 并发批量处理（一次浏览器调用获取所有数据）
            batch_results = await process_orders_batch(
                order_ids=order_ids,
                cookie_id=cid,
                cookie_string=cookies_str,
                max_concurrent=5,  # 并发5个
                timeout=30,
                headless=True,
                use_pool=True,  # 使用浏览器池
                force_refresh=True  # 强制刷新，跳过缓存检查
            )

            # 处理结果并更新数据库
            for i, result in enumerate(batch_results):
                order_info = cookie_orders[i]
                order_id = order_info['order_id']
                current_status = order_info['current_status']

                if result and not result.get('error'):
                    # 调试：打印API和DOM状态
                    api_status = result.get('api_status', 'N/A')
                    dom_status = result.get('dom_status', 'N/A')
                    log_with_user('debug', f"订单 {order_id} - API状态: {api_status}, DOM状态: {dom_status}", current_user)

                    # 状态码映射
                    order_status = result.get('order_status', 'unknown')
                    if order_status and str(order_status).isdigit():
                        status_mapping = {
                            '1': 'processing',
                            '2': 'pending_ship',
                            '3': 'shipped',
                            '4': 'completed',
                            '5': 'refunding',
                            '6': 'cancelled',
                            '7': 'refunding',
                            '8': 'cancelled',
                            '9': 'refunding',
                            '10': 'cancelled',
                            '11': 'completed',  # 交易完成
                            '12': 'cancelled',  # 交易关闭
                        }
                        order_status = status_mapping.get(str(order_status), order_status)

                    # 更新数据库
                    success = db_manager.insert_or_update_order(
                        order_id=order_id,
                        item_id=result.get('item_id') or None,
                        buyer_id=result.get('buyer_id') or None,
                        spec_name=result.get('spec_name') or None,
                        spec_value=result.get('spec_value') or None,
                        quantity=result.get('quantity') or None,
                        amount=result.get('amount') or None,
                        order_status=order_status if order_status != current_status else None,
                        cookie_id=cid,
                        created_at=result.get('order_time') or None,
                        receiver_name=result.get('receiver_name') or None,
                        receiver_phone=result.get('receiver_phone') or None,
                        receiver_address=result.get('receiver_address') or None
                    )

                    if success:
                        # 检查是否有更新
                        has_changes = (
                            order_status != current_status or
                            result.get('buyer_id') or
                            result.get('amount')
                        )

                        if has_changes:
                            updated_count += 1
                            refresh_results.append({
                                'order_id': order_id,
                                'old_status': current_status,
                                'new_status': order_status,
                                'status_text': result.get('status_text', '')
                            })
                            log_with_user('info', f"订单 {order_id} 已更新 | {current_status} -> {order_status}", current_user)
                        else:
                            no_change_count += 1
                    else:
                        failed_count += 1
                        log_with_user('error', f"订单 {order_id} 更新失败", current_user)
                else:
                    failed_count += 1
                    error_msg = result.get('error', '未知错误') if result else '未知错误'
                    log_with_user('warning', f"订单 {order_id} 获取失败: {error_msg}", current_user)

        # 由于我们已经处理完所有订单，跳过原来的循环
        # 下面的代码需要删除，所以我们需要找到循环结束的位置
        if False:  # 这个if永远不会执行，只是为了保持代码结构
            from order_status_query_playwright import OrderStatusQueryPlaywright
            from utils.order_detail_fetcher import fetch_order_detail_simple

            for order_info in orders_to_refresh:
                order_id = order_info['order_id']
                cookie_id = order_info['cookie_id']
                current_status = order_info['current_status']

                try:
                    # 获取Cookie (get_all_cookies返回的是 {cookie_id: cookie_value} 格式)
                    cookies_str = user_cookies[cookie_id]

                    if not cookies_str:
                        log_with_user('warning', f"Cookie {cookie_id} 的值为空，跳过订单 {order_id}", current_user)
                        failed_count += 1
                        continue

                    # 使用订单详情获取器获取完整信息（包括买家ID、金额、收货人信息）
                    # 注意：fetch_order_detail_simple 已经能获取所有需要的数据，无需再调用 OrderStatusQueryPlaywright
                    order_detail = await fetch_order_detail_simple(order_id, cookies_str, headless=True)

                    if order_detail:
                        # 提取订单详情（从页面获取）
                        spec_name = order_detail.get('spec_name', '')
                        spec_value = order_detail.get('spec_value', '')
                        quantity = order_detail.get('quantity', '')
                        amount = order_detail.get('amount', '')
                        receiver_name = order_detail.get('receiver_name', '')
                        receiver_phone = order_detail.get('receiver_phone', '')
                        receiver_address = order_detail.get('receiver_address', '')

                        # 只使用状态查询获取订单状态和买家ID（因为DOM解析无法获取这些）
                        query = OrderStatusQueryPlaywright(cookies_str, cookie_id, headless=True)
                        status_result = await query.query_order_status(order_id)

                        new_status = current_status
                        new_status_text = ''
                        buyer_id = ''
                        item_id = ''
                        is_bargain = None

                        if status_result.get('success'):
                            new_status_code = status_result.get('order_status')
                            new_status_text = status_result.get('status_text', '')

                            # 将状态码转换为数据库状态
                            # 完整的订单状态码映射（基于闲鱼API）
                            status_mapping = {
                                1: 'processing',      # 处理中
                                2: 'pending_ship',    # 待发货
                                3: 'shipped',         # 已发货
                                4: 'completed',       # 已完成/交易成功
                                5: 'refunding',       # 退款中
                                6: 'cancelled',       # 已取消/已关闭
                                7: 'refunding',       # 退款申请中
                                8: 'cancelled',       # 退款成功（订单关闭）
                                9: 'refunding',       # 退款协商中
                                10: 'cancelled',      # 退款关闭
                            }
                            new_status = status_mapping.get(new_status_code, 'unknown')

                            # 特殊处理：根据状态文本智能识别（优先检查最终状态）
                            if new_status == 'unknown':
                                # 优先级1: 检查"退款成功"（最终状态）
                                if '退款' in new_status_text and '成功' in new_status_text:
                                    new_status = 'cancelled'  # 退款成功=订单关闭
                                # 优先级2: 检查"关闭"或"取消"（最终状态）
                                elif '关闭' in new_status_text or '取消' in new_status_text or '超时' in new_status_text:
                                    new_status = 'cancelled'
                                # 优先级3: 检查"完成"或"交易成功"（最终状态）
                                elif '完成' in new_status_text or '交易成功' in new_status_text or '确认收货' in new_status_text:
                                    new_status = 'completed'
                                # 优先级4: 检查"退款"（中间状态）
                                elif '退款' in new_status_text:
                                    new_status = 'refunding'

                            log_with_user('debug', f"订单 {order_id}: 状态码={new_status_code}, 状态文本={new_status_text}, 映射结果={new_status}", current_user)

                            # 从 raw_data 中提取完整信息
                            raw_data = status_result.get('raw_data', {})

                            # 提取买家ID、商品ID、时间信息
                            created_at = None
                            try:
                                # 方法1: 从根级别提取 peerUserId (买家ID)
                                buyer_id = str(raw_data.get('peerUserId', ''))

                                # 方法2: 从根级别提取 itemId (商品ID)
                                item_id = str(raw_data.get('itemId', ''))

                                # 方法3: 从 orderStatusVO 组件中提取下单时间
                                if 'components' in raw_data:
                                    for component in raw_data['components']:
                                        if component.get('render') == 'orderStatusVO':
                                            order_status_data = component.get('data', {})
                                            # 从 orderStatusNodeList 中找到第一个时间节点（已拍下时间 = 创建时间）
                                            node_list = order_status_data.get('orderStatusNodeList', [])
                                            if node_list and len(node_list) > 0:
                                                created_at = node_list[0].get('time')  # 第一个是"已拍下"时间
                                            break

                                # 方法4: 从 orderInfoVO 组件中提取是否小刀（如果有 bargainInfo）
                                if 'components' in raw_data:
                                    for component in raw_data['components']:
                                        if component.get('render') == 'orderInfoVO':
                                            data = component.get('data', {})
                                            # 检查是否有小刀信息
                                            if 'bargainInfo' in data:
                                                bargain_info = data.get('bargainInfo', {})
                                                is_bargain = bargain_info.get('bargain', False)
                                            # 如果前面没找到商品ID，尝试从 jumpUrl 中提取
                                            if not item_id:
                                                item_info = data.get('itemInfo', {})
                                                jump_url = item_info.get('jumpUrl', '')
                                                if 'id=' in jump_url:
                                                    item_id = jump_url.split('id=')[1].split('&')[0]
                                            break

                                if created_at:
                                    log_with_user('debug', f"提取到订单创建时间: {created_at}", current_user)

                            except Exception as e:
                                log_with_user('warning', f"提取订单信息失败: {str(e)}", current_user)

                        # 更新数据库（包含所有字段）
                        success = db_manager.insert_or_update_order(
                            order_id=order_id,
                            item_id=item_id if item_id else None,
                            buyer_id=buyer_id if buyer_id else None,
                            spec_name=spec_name if spec_name else None,
                            spec_value=spec_value if spec_value else None,
                            quantity=quantity if quantity else None,
                            amount=amount if amount else None,
                            order_status=new_status if new_status != current_status else None,
                            is_bargain=is_bargain if is_bargain is not None else None,
                            cookie_id=cookie_id,
                            created_at=created_at,  # 添加创建时间（从API提取的北京时间）
                            receiver_name=receiver_name if receiver_name else None,
                            receiver_phone=receiver_phone if receiver_phone else None,
                            receiver_address=receiver_address if receiver_address else None
                        )

                        if success:
                            # 检查是否有任何更新
                            has_changes = (
                                new_status != current_status or
                                (buyer_id and buyer_id != 'unknown_user') or
                                amount
                            )

                            if has_changes:
                                updated_count += 1
                                refresh_results.append({
                                    'order_id': order_id,
                                    'old_status': current_status,
                                    'new_status': new_status,
                                    'status_text': new_status_text
                                })
                                log_with_user('info', f"订单 {order_id} 信息已更新 | 状态: {current_status} -> {new_status} | 买家: {buyer_id} | 金额: {amount}", current_user)
                            else:
                                no_change_count += 1
                                log_with_user('debug', f"订单 {order_id} 信息无变化", current_user)
                        else:
                            failed_count += 1
                            log_with_user('error', f"订单 {order_id} 信息更新失败", current_user)
                    else:
                        failed_count += 1
                        log_with_user('warning', f"订单 {order_id} 详情获取失败", current_user)

                except Exception as e:
                    failed_count += 1
                    log_with_user('error', f"刷新订单 {order_id} 时发生异常: {str(e)}", current_user)

        # 返回刷新结果
        log_with_user('info', f"订单刷新完成: 更新{updated_count}个, 无变化{no_change_count}个, 失败{failed_count}个", current_user)

        return JSONResponse({
            "success": True,
            "message": f"刷新完成: 更新{updated_count}个, 无变化{no_change_count}个, 失败{failed_count}个",
            "summary": {
                "total": len(orders_to_refresh),
                "updated": updated_count,
                "no_change": no_change_count,
                "failed": failed_count
            },
            "updated_orders": refresh_results
        })

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"刷新订单状态失败: {str(e)}", current_user)
        raise HTTPException(status_code=500, detail=f"刷新订单状态失败: {str(e)}")


# 已取消：全量核对订单数据功能
# 现在使用更新订单状态接口进行单个订单的数据核查
# @app.post('/api/orders/verify-all')
# async def verify_all_orders(current_user: Dict[str, Any] = Depends(get_current_user)):
#     """
#     全量核对所有订单数据
#     通过 Playwright 访问每个订单的详情页，更新时间、收货人信息等
#     """
#     pass


@orders_router.post('/api/orders/manual-ship')
async def manual_ship_orders(
    order_ids: List[str] = Body(..., description="订单ID列表"),
    ship_mode: str = Body(..., description="发货模式: status_only（仅修改发货状态）或 full_delivery（完整发货流程）"),
    custom_content: Optional[str] = Body(None, description="自定义发货内容（保留兼容）"),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    手动发货

    发货模式：
    - status_only: 仅在闲鱼标记为已发货（不发送卡券给买家）
    - full_delivery: 完整发货流程（匹配卡券、发送卡券给买家、标记发货状态）
    """
    try:
        from db_manager import db_manager
        from XianyuAutoAsync import XianyuLive
        import asyncio

        user_id = current_user['user_id']
        log_with_user('info', f"开始手动发货: 订单数量={len(order_ids)}, 模式={ship_mode}", current_user)

        # 验证发货模式
        if ship_mode not in ['status_only', 'full_delivery']:
            raise HTTPException(status_code=400, detail="发货模式必须是 status_only 或 full_delivery")

        # 获取用户的所有Cookie
        user_cookies = db_manager.get_all_cookies(user_id)

        success_count = 0
        failed_count = 0
        results = []

        # 遍历每个订单
        for order_id in order_ids:
            try:
                # 获取订单信息
                order = db_manager.get_order_by_id(order_id)
                if not order:
                    results.append({
                        'order_id': order_id,
                        'success': False,
                        'message': '订单不存在'
                    })
                    failed_count += 1
                    continue

                # 验证订单属于当前用户
                cookie_id = order.get('cookie_id')
                if cookie_id not in user_cookies:
                    results.append({
                        'order_id': order_id,
                        'success': False,
                        'message': '无权操作此订单'
                    })
                    failed_count += 1
                    continue

                item_id = order.get('item_id')
                buyer_id = order.get('buyer_id')

                if ship_mode == 'status_only':
                    # ====== 仅修改闲鱼发货状态 ======
                    if not item_id:
                        results.append({
                            'order_id': order_id,
                            'success': False,
                            'message': '订单缺少商品ID'
                        })
                        failed_count += 1
                        continue

                    # 获取cookies_str用于创建独立session
                    cookies_str = user_cookies.get(cookie_id)
                    if not cookies_str:
                        results.append({
                            'order_id': order_id,
                            'success': False,
                            'message': '无法获取账号Cookie信息'
                        })
                        failed_count += 1
                        continue

                    # 创建独立的aiohttp session（避免跨异步上下文问题）
                    import aiohttp
                    from secure_confirm_decrypted import SecureConfirm

                    try:
                        async with aiohttp.ClientSession(
                            headers={'cookie': cookies_str},
                            timeout=aiohttp.ClientTimeout(total=30)
                        ) as session:
                            confirm = SecureConfirm(session, cookies_str, cookie_id, None)
                            confirm_result = await confirm.auto_confirm(order_id, item_id)

                        if confirm_result and confirm_result.get('success'):
                            # 更新本地数据库状态
                            db_manager.insert_or_update_order(
                                order_id=order_id,
                                order_status='shipped',
                                system_shipped=True
                            )
                            results.append({
                                'order_id': order_id,
                                'success': True,
                                'message': '已成功修改闲鱼发货状态'
                            })
                            success_count += 1
                        else:
                            error_msg = confirm_result.get('error', '未知错误') if confirm_result else '确认发货返回空结果'
                            results.append({
                                'order_id': order_id,
                                'success': False,
                                'message': f'修改发货状态失败: {error_msg}'
                            })
                            failed_count += 1
                    except Exception as e:
                        log_with_user('error', f"确认发货异常: {str(e)}", current_user)
                        results.append({
                            'order_id': order_id,
                            'success': False,
                            'message': f'确认发货异常: {str(e)}'
                        })
                        failed_count += 1

                elif ship_mode == 'full_delivery':
                    # ====== 完整发货流程：匹配卡券 + 发送卡券 + 修改状态 ======
                    if not item_id:
                        results.append({
                            'order_id': order_id,
                            'success': False,
                            'message': '订单缺少商品ID，无法匹配发货规则'
                        })
                        failed_count += 1
                        continue

                    if not buyer_id:
                        results.append({
                            'order_id': order_id,
                            'success': False,
                            'message': '订单缺少买家ID，无法发送卡券'
                        })
                        failed_count += 1
                        continue

                    # 必须有运行中的实例（需要WebSocket发送消息）
                    live_instance = XianyuLive.get_instance(cookie_id)
                    if not live_instance:
                        results.append({
                            'order_id': order_id,
                            'success': False,
                            'message': '该账号未在线运行，无法执行完整发货。请先启动账号。'
                        })
                        failed_count += 1
                        continue

                    if not live_instance.ws or live_instance.ws.closed:
                        results.append({
                            'order_id': order_id,
                            'success': False,
                            'message': '该账号WebSocket连接已断开，无法发送消息。请等待重连后重试。'
                        })
                        failed_count += 1
                        continue

                    # 查找与买家的chat_id（优先从订单记录获取，回退到AI对话记录）
                    chat_id = order.get('chat_id') or ''
                    if not chat_id:
                        chat_id = db_manager.find_chat_id_by_buyer(cookie_id, buyer_id)
                    if not chat_id:
                        results.append({
                            'order_id': order_id,
                            'success': False,
                            'message': '未找到与该买家的聊天记录，无法发送卡券消息。请等待买家发送消息后重试。'
                        })
                        failed_count += 1
                        continue

                    # 检查多数量发货
                    quantity_to_send = 1
                    multi_quantity_delivery = db_manager.get_item_multi_quantity_delivery_status(cookie_id, item_id)
                    if multi_quantity_delivery:
                        try:
                            order_detail = await live_instance.fetch_order_detail_info(order_id, item_id, buyer_id)
                            if order_detail and isinstance(order_detail, dict):
                                qty = order_detail.get('quantity', 1)
                                if isinstance(qty, int) and qty > 1:
                                    quantity_to_send = qty
                        except Exception as e:
                            log_with_user('warning', f"获取订单数量失败，使用默认数量1: {str(e)}", current_user)

                    # 调用_auto_delivery获取卡券内容（内部会调用auto_confirm）
                    delivery_contents = []
                    for i in range(quantity_to_send):
                        try:
                            delivery_content = await live_instance._auto_delivery(
                                item_id, '', order_id, buyer_id
                            )
                            if delivery_content:
                                delivery_contents.append(delivery_content)
                        except Exception as e:
                            log_with_user('error', f"获取第{i+1}个卡券失败: {str(e)}", current_user)

                    if not delivery_contents:
                        results.append({
                            'order_id': order_id,
                            'success': False,
                            'message': '未匹配到发货规则或卡券获取失败'
                        })
                        failed_count += 1
                        continue

                    # 发送卡券内容给买家
                    send_success = True
                    for idx, content in enumerate(delivery_contents):
                        try:
                            if content.startswith("__IMAGE_SEND__"):
                                image_data = content.replace("__IMAGE_SEND__", "")
                                card_id = None
                                if "|" in image_data:
                                    card_id_str, image_url = image_data.split("|", 1)
                                    try:
                                        card_id = int(card_id_str)
                                    except ValueError:
                                        card_id = None
                                else:
                                    image_url = image_data
                                await live_instance.send_image_msg(
                                    live_instance.ws, chat_id, buyer_id,
                                    image_url, card_id=card_id
                                )
                            else:
                                await live_instance.send_msg(
                                    live_instance.ws, chat_id, buyer_id, content
                                )

                            # 多条消息之间间隔1秒
                            if len(delivery_contents) > 1 and idx < len(delivery_contents) - 1:
                                await asyncio.sleep(1)
                        except Exception as e:
                            log_with_user('error', f"发送第{idx+1}条卡券消息失败: {str(e)}", current_user)
                            send_success = False

                    # 更新本地数据库状态
                    db_manager.insert_or_update_order(
                        order_id=order_id,
                        order_status='shipped',
                        system_shipped=True
                    )

                    if send_success:
                        results.append({
                            'order_id': order_id,
                            'success': True,
                            'message': f'完整发货成功，已发送{len(delivery_contents)}条卡券信息给买家'
                        })
                        success_count += 1
                    else:
                        results.append({
                            'order_id': order_id,
                            'success': True,
                            'message': f'发货状态已更新，但部分卡券消息发送失败（共{len(delivery_contents)}条）'
                        })
                        success_count += 1

            except Exception as e:
                results.append({
                    'order_id': order_id,
                    'success': False,
                    'message': str(e)
                })
                failed_count += 1
                log_with_user('error', f"发货订单 {order_id} 时发生异常: {str(e)}", current_user)

        log_with_user('info', f"手动发货完成: 成功{success_count}个, 失败{failed_count}个", current_user)

        return {
            "success": True,
            "message": f"发货完成: 成功{success_count}个, 失败{failed_count}个",
            "total": len(order_ids),
            "success_count": success_count,
            "failed_count": failed_count,
            "results": results
        }

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"手动发货失败: {str(e)}", current_user)
        raise HTTPException(status_code=500, detail=f"手动发货失败: {str(e)}")


@orders_router.post('/api/orders/import')
async def import_orders(
    orders: List[Dict[str, Any]] = Body(..., description="订单列表"),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    导入订单
    支持批量导入自定义订单数据
    """
    try:
        from db_manager import db_manager

        user_id = current_user['user_id']
        log_with_user('info', f"开始导入订单: 订单数量={len(orders)}", current_user)

        # 获取用户的所有Cookie
        user_cookies = db_manager.get_all_cookies(user_id)

        success_count = 0
        failed_count = 0
        results = []

        # 必需字段验证
        required_fields = ['order_id', 'cookie_id']
        optional_fields = [
            'item_id', 'item_title', 'item_price', 'item_image',
            'buyer_id',
            'receiver_name', 'receiver_phone', 'receiver_address', 'receiver_city',
            'status', 'status_text', 'order_time', 'pay_time',
            'quantity', 'amount'
        ]

        for order_data in orders:
            try:
                # 验证必需字段
                missing_fields = [f for f in required_fields if not order_data.get(f)]
                if missing_fields:
                    results.append({
                        'order_id': order_data.get('order_id', 'unknown'),
                        'success': False,
                        'message': f'缺少必需字段: {", ".join(missing_fields)}'
                    })
                    failed_count += 1
                    continue

                order_id = str(order_data['order_id'])
                cookie_id = str(order_data['cookie_id'])

                # 验证Cookie属于当前用户
                if cookie_id not in user_cookies:
                    results.append({
                        'order_id': order_id,
                        'success': False,
                        'message': '无权操作此账号的订单'
                    })
                    failed_count += 1
                    continue

                # 检查订单是否已存在
                existing_order = db_manager.get_order_by_id(order_id)

                # 准备订单数据，直接使用 insert_or_update_order 的参数名
                # 构建参数字典，只传递非 None 的值
                insert_params = {
                    'order_id': order_id,
                    'cookie_id': cookie_id
                }

                # 前端字段名 -> 数据库参数名映射
                param_mapping = {
                    'item_id': 'item_id',
                    'buyer_id': 'buyer_id',
                    'receiver_name': 'receiver_name',
                    'receiver_phone': 'receiver_phone',
                    'receiver_address': 'receiver_address',
                    'receiver_city': 'receiver_city',
                    'status': 'order_status',  # 注意：前端用 status，后端用 order_status
                    'status_text': 'status_text',
                    'order_time': 'order_time',
                    'pay_time': 'pay_time',
                    'quantity': 'quantity',
                    'amount': 'amount',
                    'item_title': 'item_title',
                    'item_price': 'item_price',
                    'item_image': 'item_image'
                }

                # 遍历订单数据，添加到参数字典
                for field, value in order_data.items():
                    if value is not None and field in param_mapping:
                        param_name = param_mapping[field]
                        insert_params[param_name] = value

                # 使用 insert_or_update_order 统一处理
                db_manager.insert_or_update_order(**insert_params)

                results.append({
                    'order_id': order_id,
                    'success': True,
                    'message': '订单已更新' if existing_order else '订单已导入'
                })

                success_count += 1

            except Exception as e:
                results.append({
                    'order_id': order_data.get('order_id', 'unknown'),
                    'success': False,
                    'message': str(e)
                })
                failed_count += 1
                log_with_user('error', f"导入订单时发生异常: {str(e)}", current_user)

        log_with_user('info', f"导入订单完成: 成功{success_count}个, 失败{failed_count}个", current_user)

        return {
            "success": True,
            "message": f"导入完成: 成功{success_count}个, 失败{failed_count}个",
            "total": len(orders),
            "success_count": success_count,
            "failed_count": failed_count,
            "results": results
        }

    except HTTPException:
        raise
    except Exception as e:
        log_with_user('error', f"导入订单失败: {str(e)}", current_user)
        raise HTTPException(status_code=500, detail=f"导入订单失败: {str(e)}")


# ==================== 前端 SPA Catch-All 路由 ====================
# 必须放在所有 API 路由之后，用于处理前端 SPA 的直接访问
# 这样用户直接访问 /dashboard、/accounts 等前端路由时，会返回 index.html
# 然后由 React Router 在客户端处理路由

# 定义不需要返回前端页面的路径前缀（API 路径）
API_PREFIXES = ['/api/', '/static/', '/health', '/login', '/logout', '/register', '/verify', '/check-default-password', '/change-password', '/change-admin-password']

@frontend_router.get('/{path:path}', response_class=HTMLResponse)
async def catch_all_route(path: str):
    """
    Catch-all 路由：处理所有未匹配的 GET 请求
    如果是 API 请求，返回 404；否则返回前端 index.html
    """
    # 检查是否是 API 请求
    full_path = f'/{path}'
    for prefix in API_PREFIXES:
        if full_path.startswith(prefix):
            raise HTTPException(status_code=404, detail="Not Found")

    # 返回前端页面
    return await serve_frontend()


include_domain_routers(app)


# 移除自动启动，由Start.py或手动启动
# if __name__ == "__main__":
#     uvicorn.run(app, host="0.0.0.0", port=8080)
