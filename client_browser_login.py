"""Client-device proof and one-time credential delivery primitives."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


DEVICE_CHALLENGE_TTL_SECONDS = 60
RENEWAL_TASK_TTL_SECONDS = 60
CLIENT_LOGIN_TTL_SECONDS = 300
DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
ALLOWED_DEVICE_PURPOSES = {
    "login_import",
    "renewal_claim",
    "renewal_complete",
    "renewal_action_required",
}
ALLOWED_LOGIN_MODES = {"qr", "sms", "password"}
ALLOWED_BROWSER_FAMILIES = {"chrome", "edge"}
ALLOWED_CLIENT_TYPES = {"extension", "native_helper"}


class ClientBrowserError(ValueError):
    def __init__(self, message: str, *, error_code: str, http_status: int = 400):
        super().__init__(message)
        self.error_code = error_code
        self.http_status = http_status


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    text = str(value or "").strip()
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise ClientBrowserError(
            "编码字段无效", error_code="invalid_encoding"
        ) from exc


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def normalize_device_id(value: str) -> str:
    device_id = str(value or "").strip()
    if not DEVICE_ID_PATTERN.fullmatch(device_id):
        raise ClientBrowserError(
            "设备标识无效", error_code="invalid_device_id"
        )
    return device_id


def normalize_login_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode not in ALLOWED_LOGIN_MODES:
        raise ClientBrowserError(
            "当前设备登录方式无效", error_code="invalid_login_mode"
        )
    return mode


def normalize_browser_family(value: str) -> str:
    family = str(value or "").strip().lower()
    if family not in ALLOWED_BROWSER_FAMILIES:
        raise ClientBrowserError(
            "仅支持 Chrome 或 Edge", error_code="unsupported_browser"
        )
    return family


def normalize_client_type(value: str) -> str:
    client_type = str(value or "extension").strip().lower()
    if client_type not in ALLOWED_CLIENT_TYPES:
        raise ClientBrowserError(
            "设备连接类型无效", error_code="unsupported_client_type"
        )
    return client_type


def load_p256_public_key(jwk: Mapping[str, Any]) -> ec.EllipticCurvePublicKey:
    if str(jwk.get("kty") or "") != "EC" or str(jwk.get("crv") or "") != "P-256":
        raise ClientBrowserError(
            "设备公钥类型无效", error_code="invalid_device_public_key"
        )
    x = b64url_decode(str(jwk.get("x") or ""))
    y = b64url_decode(str(jwk.get("y") or ""))
    if len(x) != 32 or len(y) != 32:
        raise ClientBrowserError(
            "设备公钥长度无效", error_code="invalid_device_public_key"
        )
    try:
        return ec.EllipticCurvePublicNumbers(
            int.from_bytes(x, "big"),
            int.from_bytes(y, "big"),
            ec.SECP256R1(),
        ).public_key()
    except ValueError as exc:
        raise ClientBrowserError(
            "设备公钥无效", error_code="invalid_device_public_key"
        ) from exc


def normalize_public_jwk(jwk: Mapping[str, Any]) -> dict[str, str]:
    load_p256_public_key(jwk)
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": str(jwk["x"]),
        "y": str(jwk["y"]),
    }


def _raw_ecdsa_to_der(signature: bytes) -> bytes:
    if len(signature) != 64:
        return signature
    return __import__(
        "cryptography.hazmat.primitives.asymmetric.utils",
        fromlist=["encode_dss_signature"],
    ).encode_dss_signature(
        int.from_bytes(signature[:32], "big"),
        int.from_bytes(signature[32:], "big"),
    )


def verify_device_signature(
    public_jwk: Mapping[str, Any],
    message: bytes,
    signature: str,
) -> None:
    public_key = load_p256_public_key(public_jwk)
    encoded_signature = _raw_ecdsa_to_der(b64url_decode(signature))
    try:
        public_key.verify(encoded_signature, message, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError) as exc:
        raise ClientBrowserError(
            "设备签名无效", error_code="invalid_device_signature", http_status=403
        ) from exc


@dataclass
class DeviceChallenge:
    challenge_id: str
    device_id: str
    owner_user_id: int
    purpose: str
    nonce: str
    created_at: float
    expires_at: float
    consumed_at: Optional[float] = None


class DeviceChallengeManager:
    def __init__(self, *, ttl_seconds: float = DEVICE_CHALLENGE_TTL_SECONDS):
        self.ttl_seconds = min(60.0, max(1.0, float(ttl_seconds)))
        self._records: dict[str, DeviceChallenge] = {}
        self._lock = threading.RLock()

    def create(
        self,
        *,
        device_id: str,
        owner_user_id: int,
        purpose: str,
    ) -> dict[str, Any]:
        normalized_id = normalize_device_id(device_id)
        normalized_purpose = str(purpose or "").strip()
        if normalized_purpose not in ALLOWED_DEVICE_PURPOSES:
            raise ClientBrowserError(
                "设备挑战用途无效", error_code="invalid_challenge_purpose"
            )
        now = time.time()
        record = DeviceChallenge(
            challenge_id=secrets.token_urlsafe(18),
            device_id=normalized_id,
            owner_user_id=int(owner_user_id),
            purpose=normalized_purpose,
            nonce=secrets.token_urlsafe(32),
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._cleanup_locked(now)
            self._records[record.challenge_id] = record
        return self._safe_record(record)

    def verify(
        self,
        *,
        challenge_id: str,
        device_id: str,
        purpose: str,
        public_jwk: Mapping[str, Any],
        signature: str,
        binding: Mapping[str, Any],
        owner_user_id: Optional[int] = None,
    ) -> DeviceChallenge:
        with self._lock:
            record = self._records.get(str(challenge_id or ""))
            if record is None:
                raise ClientBrowserError(
                    "设备挑战不存在", error_code="challenge_not_found", http_status=404
                )
            if record.expires_at <= time.time():
                raise ClientBrowserError(
                    "设备挑战已过期", error_code="challenge_expired", http_status=410
                )
            if record.consumed_at is not None:
                raise ClientBrowserError(
                    "设备挑战已使用", error_code="challenge_already_used", http_status=409
                )
            if owner_user_id is not None and record.owner_user_id != int(owner_user_id):
                raise ClientBrowserError(
                    "设备挑战归属不匹配",
                    error_code="challenge_owner_mismatch",
                    http_status=403,
                )
            if record.device_id != normalize_device_id(device_id) or record.purpose != purpose:
                raise ClientBrowserError(
                    "设备挑战不匹配", error_code="challenge_binding_mismatch", http_status=403
                )
            proof = self.proof_payload(record, binding)
            verify_device_signature(public_jwk, canonical_json(proof), signature)
            record.consumed_at = time.time()
            return record

    @staticmethod
    def proof_payload(
        challenge: DeviceChallenge | Mapping[str, Any],
        binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        read = (
            (lambda key: getattr(challenge, key))
            if isinstance(challenge, DeviceChallenge)
            else (lambda key: challenge[key])
        )
        return {
            "version": 1,
            "challenge_id": read("challenge_id"),
            "device_id": read("device_id"),
            "purpose": read("purpose"),
            "nonce": read("nonce"),
            "binding": dict(binding),
        }

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def _cleanup_locked(self, now: float) -> None:
        remove = [
            key for key, value in self._records.items()
            if value.expires_at + self.ttl_seconds <= now
        ]
        for key in remove:
            self._records.pop(key, None)

    @staticmethod
    def _safe_record(record: DeviceChallenge) -> dict[str, Any]:
        return {
            "challenge_id": record.challenge_id,
            "device_id": record.device_id,
            "purpose": record.purpose,
            "nonce": record.nonce,
            "expires_at": record.expires_at,
        }


def public_jwk_from_key(public_key: ec.EllipticCurvePublicKey) -> dict[str, str]:
    numbers = public_key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url_encode(numbers.x.to_bytes(32, "big")),
        "y": b64url_encode(numbers.y.to_bytes(32, "big")),
    }


def seal_renewal_credential(
    *,
    encryption_public_jwk: Mapping[str, Any],
    username: str,
    password: str,
    context: Mapping[str, Any],
) -> dict[str, str]:
    """Seal one credential to an extension-only ECDH private key."""
    device_key = load_p256_public_key(encryption_public_jwk)
    ephemeral_private = ec.generate_private_key(ec.SECP256R1())
    shared_secret = ephemeral_private.exchange(ec.ECDH(), device_key)
    salt = secrets.token_bytes(32)
    aad = canonical_json(context)
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"xmc-client-renewal-v1\0" + hashlib.sha256(aad).digest(),
    ).derive(shared_secret)
    nonce = secrets.token_bytes(12)
    plaintext = canonical_json({"username": username, "password": password})
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return {
        "algorithm": "ECDH-P256+HKDF-SHA256+A256GCM",
        "ephemeral_public_key": json.dumps(
            public_jwk_from_key(ephemeral_private.public_key()),
            separators=(",", ":"),
            sort_keys=True,
        ),
        "salt": b64url_encode(salt),
        "nonce": b64url_encode(nonce),
        "ciphertext": b64url_encode(ciphertext),
        "aad": b64url_encode(aad),
    }


device_challenges = DeviceChallengeManager()


@dataclass
class ClientLoginSession:
    session_id: str
    owner_user_id: int
    device_id: str
    mode: str
    client_type: str
    state: str
    message: str
    created_at: float
    expires_at: float
    account_id: str = ""
    error_code: str = ""
    consumed_at: Optional[float] = None
    ended_by: str = ""
    renewal_authorized_at: Optional[float] = None


class ClientLoginSessionManager:
    """Five-minute owner/device/mode-bound login handoff sessions."""

    def __init__(self, *, ttl_seconds: float = CLIENT_LOGIN_TTL_SECONDS):
        self.ttl_seconds = min(300.0, max(1.0, float(ttl_seconds)))
        self._records: dict[str, ClientLoginSession] = {}
        self._lock = threading.RLock()

    def create(
        self,
        *,
        owner_user_id: int,
        device_id: str,
        mode: str,
        client_type: str = "extension",
    ) -> dict[str, Any]:
        now = time.time()
        record = ClientLoginSession(
            session_id=secrets.token_urlsafe(18),
            owner_user_id=int(owner_user_id),
            device_id=normalize_device_id(device_id),
            mode=normalize_login_mode(mode),
            client_type=normalize_client_type(client_type),
            state="waiting_device",
            message="等待当前设备浏览器打开官方登录页",
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._cleanup_locked(now)
            self._records[record.session_id] = record
        return self._safe_status(record)

    def get_for_owner(self, session_id: str, owner_user_id: int) -> dict[str, Any]:
        with self._lock:
            record = self._record_locked(session_id)
            if record.owner_user_id != int(owner_user_id):
                raise ClientBrowserError(
                    "当前设备登录会话不存在",
                    error_code="client_login_not_found",
                    http_status=404,
                )
            self._expire_locked(record)
            return self._safe_status(record)

    def get_for_device(
        self,
        *,
        session_id: str,
        device_id: str,
        mode: str,
        client_type: Optional[str] = None,
    ) -> ClientLoginSession:
        with self._lock:
            record = self._record_locked(session_id)
            self._expire_locked(record)
            if record.state == "expired":
                raise ClientBrowserError(
                    "当前设备登录会话已过期",
                    error_code="client_login_expired",
                    http_status=410,
                )
            if record.state in {"failed", "cancelled"}:
                raise ClientBrowserError(
                    "当前设备登录会话已结束",
                    error_code="client_login_ended",
                    http_status=409,
                )
            if (
                record.device_id != normalize_device_id(device_id)
                or record.mode != normalize_login_mode(mode)
                or (
                    client_type is not None
                    and record.client_type != normalize_client_type(client_type)
                )
            ):
                raise ClientBrowserError(
                    "当前设备登录会话绑定不匹配",
                    error_code="client_login_binding_mismatch",
                    http_status=403,
                )
            return record

    def pending_for_device(self, device_id: str) -> list[dict[str, Any]]:
        normalized_device_id = normalize_device_id(device_id)
        now = time.time()
        with self._lock:
            self._cleanup_locked(now)
            pending = []
            for record in self._records.values():
                self._expire_locked(record)
                if record.device_id != normalized_device_id:
                    continue
                if record.state not in {"waiting_device", "waiting_user"}:
                    continue
                pending.append(self._safe_status(record))
            return sorted(pending, key=lambda item: item["expires_at"])

    def mark_waiting_user(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._record_locked(session_id)
            if record.state == "waiting_device":
                record.state = "waiting_user"
                record.message = "请在当前设备浏览器完成登录和全部安全验证"
            return self._safe_status(record)

    def consume_for_import(
        self,
        *,
        session_id: str,
        device_id: str,
        mode: str,
        client_type: Optional[str] = None,
    ) -> ClientLoginSession:
        with self._lock:
            current = self._record_locked(session_id)
            self._expire_locked(current)
            if current.consumed_at is not None or current.state in {
                "validating", "awaiting_confirmation", "success"
            }:
                raise ClientBrowserError(
                    "当前设备登录结果已提交",
                    error_code="client_login_already_used",
                    http_status=409,
                )
            record = self.get_for_device(
                session_id=session_id,
                device_id=device_id,
                mode=mode,
                client_type=client_type,
            )
            record.consumed_at = time.time()
            record.state = "validating"
            record.message = "正在验证平台 Token 和账号身份"
            record.error_code = ""
            return record

    def retryable(
        self,
        session_id: str,
        *,
        message: str,
        error_code: str,
    ) -> dict[str, Any]:
        """Release one failed import so the device can use a fresh challenge."""
        with self._lock:
            record = self._record_locked(session_id)
            if record.state != "validating":
                raise ClientBrowserError(
                    "当前设备登录状态无效",
                    error_code="client_login_state_invalid",
                    http_status=409,
                )
            record.consumed_at = None
            record.state = "waiting_user"
            record.message = str(
                message or "平台连接暂时异常，保持页面开启并自动重试"
            )[:200]
            record.error_code = str(error_code or "client_login_retryable")[:80]
            record.ended_by = ""
            return self._safe_status(record)

    def persisted(self, session_id: str, *, account_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._record_locked(session_id)
            if record.state != "validating":
                raise ClientBrowserError(
                    "当前设备登录状态无效",
                    error_code="client_login_state_invalid",
                    http_status=409,
                )
            record.state = "awaiting_confirmation"
            record.message = "账号已验证并落库，等待页面确认账号列表"
            record.account_id = str(account_id or "")
            record.error_code = ""
            return self._safe_status(record)

    def confirm(
        self,
        *,
        session_id: str,
        owner_user_id: int,
        account_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            record = self._record_locked(session_id)
            if record.owner_user_id != int(owner_user_id):
                raise ClientBrowserError(
                    "当前设备登录会话不存在",
                    error_code="client_login_not_found",
                    http_status=404,
                )
            if record.state != "awaiting_confirmation" or record.account_id != str(account_id):
                raise ClientBrowserError(
                    "账号列表确认与登录结果不匹配",
                    error_code="account_confirmation_mismatch",
                    http_status=409,
                )
            record.state = "success"
            record.message = "当前设备浏览器登录成功"
            record.ended_by = "validated_persisted_and_confirmed"
            return self._safe_status(record)

    def fail(self, session_id: str, *, message: str, error_code: str) -> dict[str, Any]:
        with self._lock:
            record = self._record_locked(session_id)
            record.state = "failed"
            record.message = str(message or "当前设备登录失败")[:200]
            record.error_code = str(error_code or "client_login_failed")[:80]
            record.ended_by = "validation_failed"
            return self._safe_status(record)

    def authorize_renewal(
        self,
        *,
        session_id: str,
        owner_user_id: int,
        device_id: str,
        account_id: str,
    ) -> dict[str, Any]:
        """Consume the one explicit post-login password renewal authorization."""
        with self._lock:
            record = self._record_locked(session_id)
            if record.owner_user_id != int(owner_user_id):
                raise ClientBrowserError(
                    "当前设备登录会话不存在",
                    error_code="client_login_not_found",
                    http_status=404,
                )
            if record.expires_at <= time.time():
                raise ClientBrowserError(
                    "登录授权已过期，请重新登录",
                    error_code="client_login_expired",
                    http_status=410,
                )
            if (
                record.state != "success"
                or record.mode != "password"
                or record.client_type != "extension"
                or record.device_id != normalize_device_id(device_id)
                or record.account_id != str(account_id or "")
            ):
                raise ClientBrowserError(
                    "续期授权必须来自已确认的账号密码登录",
                    error_code="renewal_login_authorization_required",
                    http_status=409,
                )
            if record.renewal_authorized_at is not None:
                raise ClientBrowserError(
                    "该登录会话的续期授权已使用",
                    error_code="renewal_login_authorization_used",
                    http_status=409,
                )
            record.renewal_authorized_at = time.time()
            return self._safe_status(record)

    def cancel(self, session_id: str, owner_user_id: int) -> dict[str, Any]:
        with self._lock:
            record = self._record_locked(session_id)
            if record.owner_user_id != int(owner_user_id):
                raise ClientBrowserError(
                    "当前设备登录会话不存在",
                    error_code="client_login_not_found",
                    http_status=404,
                )
            if record.state not in {"success", "failed", "expired"}:
                record.state = "cancelled"
                record.message = "当前设备登录已取消"
                record.ended_by = "user_cancelled"
            return self._safe_status(record)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def _record_locked(self, session_id: str) -> ClientLoginSession:
        record = self._records.get(str(session_id or ""))
        if record is None:
            raise ClientBrowserError(
                "当前设备登录会话不存在",
                error_code="client_login_not_found",
                http_status=404,
            )
        return record

    def _expire_locked(self, record: ClientLoginSession) -> None:
        if record.state not in {"success", "failed", "cancelled", "expired"} and record.expires_at <= time.time():
            record.state = "expired"
            record.message = "当前设备登录会话已过期"
            record.error_code = "client_login_expired"
            record.ended_by = "expired"

    def _cleanup_locked(self, now: float) -> None:
        remove = [
            key for key, value in self._records.items()
            if value.expires_at + self.ttl_seconds <= now
        ]
        for key in remove:
            self._records.pop(key, None)

    @staticmethod
    def _safe_status(record: ClientLoginSession) -> dict[str, Any]:
        return {
            "session_id": record.session_id,
            "device_id": record.device_id,
            "mode": record.mode,
            "client_type": record.client_type,
            "state": record.state,
            "message": record.message,
            "error_code": record.error_code,
            "account_id": record.account_id,
            "expires_at": record.expires_at,
            "ended_by": record.ended_by,
        }


client_login_sessions = ClientLoginSessionManager()


__all__ = [
    "CLIENT_LOGIN_TTL_SECONDS",
    "ClientBrowserError",
    "ClientLoginSessionManager",
    "DeviceChallengeManager",
    "RENEWAL_TASK_TTL_SECONDS",
    "b64url_decode",
    "b64url_encode",
    "canonical_json",
    "device_challenges",
    "client_login_sessions",
    "load_p256_public_key",
    "normalize_browser_family",
    "normalize_client_type",
    "normalize_device_id",
    "normalize_login_mode",
    "normalize_public_jwk",
    "public_jwk_from_key",
    "seal_renewal_credential",
]
