"""Device identity and proof primitives shared by the native helper."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any, Mapping

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from . import HELPER_VERSION


DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,80}$")


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    text = str(value or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def public_jwk_from_key(public_key: ec.EllipticCurvePublicKey) -> dict[str, str]:
    numbers = public_key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url_encode(numbers.x.to_bytes(32, "big")),
        "y": b64url_encode(numbers.y.to_bytes(32, "big")),
    }


def _private_der(key: ec.EllipticCurvePrivateKey) -> str:
    return b64url_encode(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def _private_from_der(value: str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_der_private_key(b64url_decode(value), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("native helper key is not an EC private key")
    return key


@dataclass
class DeviceIdentity:
    device_id: str
    browser_family: str
    signing_private_key: ec.EllipticCurvePrivateKey
    encryption_private_key: ec.EllipticCurvePrivateKey

    @classmethod
    def generate(cls, browser_family: str) -> "DeviceIdentity":
        device_id = "helper_" + b64url_encode(secrets.token_bytes(24))
        return cls(
            device_id=device_id,
            browser_family=browser_family,
            signing_private_key=ec.generate_private_key(ec.SECP256R1()),
            encryption_private_key=ec.generate_private_key(ec.SECP256R1()),
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "DeviceIdentity":
        device_id = str(record.get("device_id") or "")
        if not DEVICE_ID_PATTERN.fullmatch(device_id):
            raise ValueError("native helper device id is invalid")
        return cls(
            device_id=device_id,
            browser_family=str(record.get("browser_family") or "chrome"),
            signing_private_key=_private_from_der(str(record["signing_private_der"])),
            encryption_private_key=_private_from_der(str(record["encryption_private_der"])),
        )

    def to_record(self) -> dict[str, str]:
        return {
            "device_id": self.device_id,
            "browser_family": self.browser_family,
            "signing_private_der": _private_der(self.signing_private_key),
            "encryption_private_der": _private_der(self.encryption_private_key),
        }

    def public_record(self) -> dict[str, Any]:
        return {
            "deviceId": self.device_id,
            "browserFamily": self.browser_family,
            "clientType": "native_helper",
            "helperVersion": HELPER_VERSION,
            "protocolVersion": 1,
            "signingPublicJwk": public_jwk_from_key(self.signing_private_key.public_key()),
            "encryptionPublicJwk": public_jwk_from_key(self.encryption_private_key.public_key()),
        }

    def sign_proof(self, challenge: Mapping[str, Any], binding: Mapping[str, Any]) -> str:
        proof = {
            "version": 1,
            "challenge_id": challenge["challenge_id"],
            "device_id": challenge["device_id"],
            "purpose": challenge["purpose"],
            "nonce": challenge["nonce"],
            "binding": dict(binding),
        }
        signature = self.signing_private_key.sign(
            canonical_json(proof), ec.ECDSA(hashes.SHA256())
        )
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        r, s = decode_dss_signature(signature)
        return b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


__all__ = [
    "DeviceIdentity",
    "b64url_decode",
    "b64url_encode",
    "canonical_json",
    "public_jwk_from_key",
]
