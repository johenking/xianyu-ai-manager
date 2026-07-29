"""Private storage helpers for human-verification screenshots."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterable, Optional


def private_verification_root() -> Path:
    configured = str(os.getenv("XIANYU_PRIVATE_VERIFICATION_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()
    db_path = Path(os.getenv("DB_PATH", "data/xianyu_data.db")).expanduser()
    return db_path.parent / "verification_images"


def ensure_private_verification_root(root: Path | str | None = None) -> Path:
    path = Path(root) if root is not None else private_verification_root()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def verification_identity_key(value: str) -> str:
    raw = str(value or "login").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def resolve_private_verification_image(
    path: str | Path | None,
    *,
    root: Path | str | None = None,
) -> Optional[Path]:
    if not path:
        return None
    allowed_root = (Path(root) if root is not None else private_verification_root()).resolve()
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(allowed_root)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def remove_private_verification_image(path: str | Path | None) -> None:
    resolved = resolve_private_verification_image(path)
    if resolved is None:
        return
    try:
        resolved.unlink()
    except OSError:
        pass


def list_private_verification_images(identities: Iterable[str]) -> list[Path]:
    root = private_verification_root()
    if not root.is_dir():
        return []
    found: dict[Path, Path] = {}
    for identity in identities:
        key = verification_identity_key(identity)
        for prefix in ("xianyu_verify", "xianyu_login"):
            for candidate in root.glob(f"{prefix}_{key}_*.png"):
                resolved = resolve_private_verification_image(candidate, root=root)
                if resolved is not None:
                    found[resolved] = resolved
    return sorted(
        found.values(),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def latest_private_verification_image(identities: Iterable[str]) -> Optional[Path]:
    images = list_private_verification_images(identities)
    return images[0] if images else None
