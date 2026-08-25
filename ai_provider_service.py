"""AI provider profiles, model discovery and connection verification helpers."""

import base64
import hashlib
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from utils.outbound_http import request_public_http_sync


PROVIDER_PRESETS: Dict[str, Dict[str, str]] = {
    "deepseek": {
        "label": "DeepSeek",
        "provider_type": "openai_compatible",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI",
        "provider_type": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4.1-mini",
    },
    "qwen": {
        "label": "通义千问",
        "provider_type": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
    },
    "openrouter": {
        "label": "OpenRouter",
        "provider_type": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4.1-mini",
    },
    "siliconflow": {
        "label": "硅基流动",
        "provider_type": "openai_compatible",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3",
    },
    "gemini": {
        "label": "Google Gemini",
        "provider_type": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-2.5-flash",
    },
    "custom": {
        "label": "自定义兼容接口",
        "provider_type": "openai_compatible",
        "base_url": "",
        "default_model": "",
    },
}
PROVIDER_MODEL_MAX_COUNT = 500
PROVIDER_MODEL_NAME_MAX_LENGTH = 200


def normalize_provider_models(values: Any) -> List[str]:
    """Keep provider model IDs bounded, unique, non-empty, and stable."""
    models = set()
    for value in values if isinstance(values, list) else []:
        name = str(value or "").strip()
        if not name or len(name) > PROVIDER_MODEL_NAME_MAX_LENGTH:
            continue
        models.add(name)
        if len(models) >= PROVIDER_MODEL_MAX_COUNT:
            break
    return sorted(models, key=str.lower)


def _local_encryption_secret() -> str:
    db_path = Path(os.getenv("DB_PATH", "data/xianyu_data.db"))
    key_path = Path(os.getenv("AI_PROVIDER_KEY_FILE", str(db_path.parent / ".ai_provider_key")))
    if key_path.exists():
        return key_path.read_text(encoding="ascii").strip()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(48)
    try:
        descriptor = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return key_path.read_text(encoding="ascii").strip()
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(secret)
    return secret


def _fernet() -> Fernet:
    secret = os.getenv("AI_PROVIDER_ENCRYPTION_KEY") or _local_encryption_secret()
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_provider_key(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    return "fernet:" + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_provider_key(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if not value.startswith("fernet:"):
        return value
    try:
        return _fernet().decrypt(value[7:].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("平台密钥无法解密，请重新设置 Key") from exc


def mask_provider_key(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * max(4, len(value) - 2) + value[-2:]
    return value[:3] + "*" * min(12, len(value) - 7) + value[-4:]


def extract_openai_models(payload: Dict[str, Any]) -> List[str]:
    return normalize_provider_models([
        item.get("id")
        for item in payload.get("data", [])
        if isinstance(item, dict)
    ])


def extract_gemini_models(payload: Dict[str, Any]) -> List[str]:
    return normalize_provider_models([
        str(item.get("name") or "").strip().removeprefix("models/")
        for item in payload.get("models", [])
        if isinstance(item, dict)
        and "generateContent" in (item.get("supportedGenerationMethods") or [])
    ])


def _openai_endpoint(base_url: str, suffix: str) -> str:
    return f"{str(base_url or '').rstrip('/')}/{suffix.lstrip('/')}"


def discover_provider_models(
    profile: Dict[str, Any],
    get: Optional[Callable[..., Any]] = None,
    timeout: int = 20,
) -> List[str]:
    api_key = str(profile.get("api_key") or "")
    base_url = str(profile.get("base_url") or "").rstrip("/")
    if not api_key:
        raise ValueError("请先配置 API Key")
    if not base_url:
        raise ValueError("请先配置 API 地址")

    if profile.get("provider_type") == "gemini":
        if get is not None:
            response = get(f"{base_url}/models", params={"key": api_key}, timeout=timeout)
        else:
            response = request_public_http_sync(
                "GET",
                f"{base_url}/models",
                params={"key": api_key},
                timeout_seconds=timeout,
                allowed_methods=("GET",),
                require_https=True,
            )
        response.raise_for_status()
        return extract_gemini_models(response.json())

    if get is not None:
        response = get(
            _openai_endpoint(base_url, "models"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
    else:
        response = request_public_http_sync(
            "GET",
            _openai_endpoint(base_url, "models"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout_seconds=timeout,
            allowed_methods=("GET",),
            require_https=True,
        )
    response.raise_for_status()
    return extract_openai_models(response.json())


def test_provider_reply(
    profile: Dict[str, Any],
    model_name: str,
    post: Optional[Callable[..., Any]] = None,
    timeout: int = 30,
) -> str:
    model_name = str(model_name or "").strip()
    api_key = str(profile.get("api_key") or "")
    base_url = str(profile.get("base_url") or "").rstrip("/")
    if not model_name:
        raise ValueError("请选择或填写模型")
    if not api_key:
        raise ValueError("请先配置 API Key")
    if not base_url:
        raise ValueError("请先配置 API 地址")

    prompt = "请只回复：连接成功"
    if profile.get("provider_type") == "gemini":
        url = (
            f"{base_url}/models/"
            f"{quote(model_name, safe='-._')}:generateContent"
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 32},
        }
        if post is not None:
            response = post(
                url,
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
        else:
            response = request_public_http_sync(
                "POST",
                url,
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json_body=payload,
                timeout_seconds=timeout,
                allowed_methods=("POST",),
                require_https=True,
            )
        response.raise_for_status()
        payload = response.json()
        try:
            return str(payload["candidates"][0]["content"]["parts"][0]["text"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Gemini 返回了无法识别的响应") from exc

    body: Dict[str, Any] = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 32,
    }
    if profile.get("preset") == "deepseek" or "deepseek" in base_url.lower():
        body["thinking"] = {"type": "disabled"}
    if post is not None:
        response = post(
            _openai_endpoint(base_url, "chat/completions"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
    else:
        response = request_public_http_sync(
            "POST",
            _openai_endpoint(base_url, "chat/completions"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json_body=body,
            timeout_seconds=timeout,
            allowed_methods=("POST",),
            require_https=True,
        )
    response.raise_for_status()
    payload = response.json()
    try:
        return str(payload["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("兼容接口返回了无法识别的响应") from exc


class ProviderTestTokenStore:
    """Short-lived, one-time proof that a provider/model generated a reply."""

    def __init__(
        self,
        ttl_seconds: int = 600,
        *,
        max_entries: int = 512,
        max_entries_per_user: int = 16,
    ):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max(1, int(max_entries))
        self.max_entries_per_user = max(1, int(max_entries_per_user))
        self._tokens: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def issue(self, user_id: int, profile_id: int, model_name: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._cleanup()
            matching_user_tokens = [
                existing_token
                for existing_token, record in self._tokens.items()
                if record["user_id"] == int(user_id)
            ]
            overflow = len(matching_user_tokens) - self.max_entries_per_user + 1
            for existing_token in matching_user_tokens[:max(0, overflow)]:
                self._tokens.pop(existing_token, None)
            while len(self._tokens) >= self.max_entries:
                self._tokens.pop(next(iter(self._tokens)), None)
            self._tokens[token] = {
                "user_id": int(user_id),
                "profile_id": int(profile_id),
                "model_name": str(model_name),
                "expires_at": time.time() + self.ttl_seconds,
            }
        return token

    def consume(self, token: str, user_id: int, profile_id: int, model_name: str) -> bool:
        with self._lock:
            self._cleanup()
            record = self._tokens.get(str(token or ""))
            if not record:
                return False
            matches = (
                record["user_id"] == int(user_id)
                and record["profile_id"] == int(profile_id)
                and record["model_name"] == str(model_name)
            )
            if matches:
                self._tokens.pop(str(token), None)
            return matches

    def _cleanup(self) -> None:
        now = time.time()
        expired = [token for token, record in self._tokens.items() if record["expires_at"] <= now]
        for token in expired:
            self._tokens.pop(token, None)


provider_test_tokens = ProviderTestTokenStore()
