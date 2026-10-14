import hashlib
import hmac
import json
import re
import time
from uuid import UUID

from fastapi import HTTPException

from .config import get_settings


def webhook_secret(tenant_id: UUID) -> str:
    try:
        secrets = json.loads(get_settings().webhook_secrets_json)
        secret = secrets.get(str(tenant_id))
    except (json.JSONDecodeError, AttributeError):
        secret = None
    if not isinstance(secret, str) or len(secret) < 32:
        raise HTTPException(status_code=503, detail="Webhook is not configured")
    return secret


def verify_webhook(tenant_id: UUID, timestamp: str | None, signature: str | None, body: bytes) -> None:
    verify_signed_payload(webhook_secret(tenant_id), timestamp, signature, body)


def verify_signed_payload(secret: str, timestamp: str | None, signature: str | None, body: bytes) -> None:
    if timestamp is None or signature is None:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    if not re.fullmatch(r"[0-9]{1,12}", timestamp):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        sent_at = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid webhook signature") from None
    if abs(time.time() - sent_at) > 300:
        raise HTTPException(status_code=401, detail="Expired webhook signature")
    normalized_signature = signature.removeprefix("sha256=")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", normalized_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    digest = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(digest, normalized_signature.lower()):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def webhook_event_key(tenant_id: UUID, event_id: str) -> str:
    return hmac.new(
        webhook_secret(tenant_id).encode("utf-8"),
        event_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

