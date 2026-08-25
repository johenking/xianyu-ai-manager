"""Small, side-effect-free helpers for Goofish IM message payloads."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit


MAX_INBOUND_IMAGES = 8
IMAGE_PLACEHOLDER = "[图片]"


@dataclass(frozen=True)
class ImageReference:
    url: str
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class InboundContent:
    text: str = ""
    images: tuple[ImageReference, ...] = ()
    content_type: int | None = None

    @property
    def has_content(self) -> bool:
        return bool(self.text or self.images)


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _json_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw.startswith("{"):
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _decode_custom_content(content: Mapping[str, Any]) -> Mapping[str, Any]:
    if content.get("contentType") != 101:
        return content
    custom = _mapping(content.get("custom")) or {}
    encoded = custom.get("data")
    if not isinstance(encoded, str) or len(encoded) > 2 * 1024 * 1024:
        return content
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return content
    return _json_mapping(decoded) or content


def _content_candidates(message: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    operation = _mapping(message.get("operation"))
    if operation:
        content = _mapping(operation.get("content"))
        if content:
            yield content
            decoded = _decode_custom_content(content)
            if decoded is not content:
                yield decoded

    one = _mapping(message.get("1"))
    message_data = _mapping(one.get("6")) if one else None
    nested = _mapping(message_data.get("3")) if message_data else None
    content = _json_mapping(nested.get("5")) if nested else None
    if content:
        yield content
        decoded = _decode_custom_content(content)
        if decoded is not content:
            yield decoded

    direct = _mapping(message.get("content"))
    if direct:
        yield direct
        decoded = _decode_custom_content(direct)
        if decoded is not direct:
            yield decoded


def _content_type(content: Mapping[str, Any]) -> int | None:
    try:
        value = content.get("contentType")
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _content_text(content: Mapping[str, Any]) -> str:
    text_node = content.get("text")
    if isinstance(text_node, Mapping):
        text = text_node.get("text")
    else:
        text = text_node
    if isinstance(text, str) and text.strip():
        return text.strip()

    reminder = _mapping(content.get("reminder")) or content
    reminder_text = reminder.get("reminderContent")
    return reminder_text.strip() if isinstance(reminder_text, str) else ""


def _image_nodes(content: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    image = _mapping(content.get("image"))
    if image:
        pics = image.get("pics")
        if isinstance(pics, list):
            for value in pics:
                if isinstance(value, Mapping):
                    yield value
        elif isinstance(image.get("url"), str):
            yield image

    for key in ("images", "pics"):
        values = content.get(key)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, Mapping):
                    yield value


def _valid_image_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip()
    if not url or len(url) > 4096:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    return url


def _image_reference(node: Mapping[str, Any]) -> ImageReference | None:
    url = _valid_image_url(
        node.get("url")
        or node.get("imageUrl")
        or node.get("image_url")
        or node.get("mediaUrl")
        or node.get("media_url")
    )
    if not url:
        return None

    def dimension(name: str) -> int | None:
        try:
            value = int(node.get(name))
        except (TypeError, ValueError):
            return None
        return value if 0 < value <= 10000 else None

    return ImageReference(url, dimension("width"), dimension("height"))


def extract_inbound_content(message: Mapping[str, Any]) -> InboundContent:
    """Extract text and image references from old and new IM envelopes."""
    if not isinstance(message, Mapping):
        return InboundContent()

    one = _mapping(message.get("1"))
    details = _mapping(one.get("10")) if one else None
    text = ""
    if details and isinstance(details.get("reminderContent"), str):
        text = details["reminderContent"].strip()

    content_type = None
    images: list[ImageReference] = []
    seen_urls: set[str] = set()
    for raw_content in _content_candidates(message):
        content = _decode_custom_content(raw_content)
        content_type = content_type or _content_type(content)
        text = _content_text(content) or text
        for node in _image_nodes(content):
            reference = _image_reference(node)
            if reference and reference.url not in seen_urls:
                seen_urls.add(reference.url)
                images.append(reference)
                if len(images) >= MAX_INBOUND_IMAGES:
                    break
        if len(images) >= MAX_INBOUND_IMAGES:
            break

    return InboundContent(text=text, images=tuple(images), content_type=content_type)


def normalize_operation_message(message: Mapping[str, Any]) -> dict[str, Any] | None:
    """Convert a new web push envelope to the legacy shape used by the runtime."""
    operation = _mapping(message.get("operation"))
    content = _mapping(operation.get("content")) if operation else None
    if not operation or not content:
        return None
    if "sessionArouse" in content:
        return None

    extracted = extract_inbound_content(message)
    if not extracted.has_content:
        return None

    session_info = _mapping(operation.get("sessionInfo")) or {}
    sender_info = _mapping(operation.get("senderInfo")) or {}
    reminder = _mapping(content.get("reminder")) or {}
    cid = str(
        message.get("sessionId")
        or operation.get("sessionId")
        or session_info.get("sessionId")
        or ""
    ).strip()
    sender_id = str(
        sender_info.get("senderUserId")
        or reminder.get("senderUserId")
        or ""
    ).strip()
    if not cid or not sender_id:
        return None

    sender_name = str(
        sender_info.get("senderNick")
        or sender_info.get("senderName")
        or reminder.get("reminderTitle")
        or "未知用户"
    ).strip()
    display_text = extracted.text or (IMAGE_PLACEHOLDER if extracted.images else "")
    try:
        create_time = int(message.get("timestamp") or operation.get("timestamp") or 0)
    except (TypeError, ValueError):
        create_time = 0
    reminder_url = str(reminder.get("reminderUrl") or "")
    session_type = str(reminder.get("sessionType") or session_info.get("sessionType") or "1")
    content_payload = _decode_custom_content(content)
    content_type = extracted.content_type or (2 if extracted.images else 1)
    return {
        "1": {
            "2": cid if "@" in cid else f"{cid}@goofish",
            "5": create_time,
            "6": {"3": {"1": content_type, "2": display_text, "5": json.dumps(content_payload, ensure_ascii=False)}},
            "10": {
                "senderNick": sender_name,
                "senderUserId": sender_id,
                "reminderContent": display_text,
                "reminderUrl": reminder_url,
                "sessionType": session_type,
            },
        },
        "_xianyu_operation": dict(message),
    }


def message_has_content(message: Mapping[str, Any]) -> bool:
    return extract_inbound_content(message).has_content
