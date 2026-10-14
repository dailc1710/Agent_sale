import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .db import enter_tenant_scope
from .dependencies import (
    get_hermes_client,
    get_platform_db,
    get_public_session_db,
    get_public_shop_db,
)
from .hermes import HermesClient, HermesError, api_config_fingerprint
from .models import (
    ConsultationRequest,
    GoogleFormSubmission,
    Message,
    PublicChatMessage,
    PublicChatSession,
    Product,
    ReplyDraft,
    Shop,
    ShopKnowledge,
    Tenant,
    now_utc,
)
from .pii_crypto import PIIEncryptionUnavailable, decrypt_pii
from .privacy import redact_pii
from .schemas import (
    AdminLoginIn,
    AdminProductCreate,
    AdminProductUpdate,
    AdminShopCreate,
    AdminShopUpdate,
    AdminStaffMessageIn,
    WebsiteSyncIn,
)
from .website_knowledge import WebsiteFetchError, read_public_website


STATIC_DIR = Path(__file__).resolve().parent / "static"
admin_router = APIRouter()
public_router = APIRouter()
COOKIE_NAME = "hermes_admin_session"
COOKIE_TTL_SECONDS = 8 * 60 * 60
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SlidingRateLimiter:
    """Small-process limiter; production multi-instance deployments should use a shared store."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int) -> None:
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and events[0] <= now - 60:
                events.popleft()
            if len(events) >= limit:
                raise HTTPException(status_code=429, detail="Bạn gửi yêu cầu quá nhanh. Vui lòng thử lại sau một phút.")
            events.append(now)
            if len(self._events) > 5000:
                stale = [name for name, values in self._events.items() if not values or values[-1] <= now - 60]
                for name in stale:
                    self._events.pop(name, None)


rate_limiter = SlidingRateLimiter()


def _client_key(request: Request) -> str:
    # Do not trust X-Forwarded-For unless a trusted proxy has already normalized it.
    return request.client.host if request.client else "unknown"


def _admin_key() -> bytes:
    settings = get_settings()
    secret = settings.admin_secret or settings.jwt_secret
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="Admin secret chưa được cấu hình an toàn")
    return secret.encode("utf-8")


def _b64(value: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _new_admin_cookie(username: str, role: str) -> str:
    payload = _b64(json.dumps({"sub": username, "role": role, "exp": int(time.time()) + COOKIE_TTL_SECONDS}, separators=(",", ":")).encode())
    signature = hmac.new(_admin_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _portal_identity(cookie: str | None) -> tuple[str, str] | None:
    if not cookie:
        return None
    try:
        payload, signature = cookie.split(".", 1)
        expected = hmac.new(_admin_key(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(_unb64(payload))
        if int(data["exp"]) < int(time.time()):
            return None
        username = str(data["sub"])
        # Existing signed admin cookies predate the role claim and remain admin-only.
        role = str(data.get("role", "admin"))
        settings = get_settings()
        if role == "admin" and username == settings.admin_username:
            return username, role
        if role == "staff" and settings.staff_username.strip() and username == settings.staff_username.strip():
            return username, role
        return None
    except (HTTPException, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def require_admin(
    cookie: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> str:
    identity = _portal_identity(cookie)
    if identity is None:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập quản trị")
    username, role = identity
    if role != "admin":
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên mới được phép")
    return username


def require_staff(
    cookie: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> tuple[str, str]:
    identity = _portal_identity(cookie)
    if identity is None:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập")
    return identity


def _origin(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        host = parsed.hostname.lower().rstrip(".")
        port = parsed.port
        default_port = (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
        authority = host if port is None or default_port else f"{host}:{port}"
        return f"{parsed.scheme.lower()}://{authority}"
    except ValueError:
        return None


def _public_base(request: Request) -> str:
    settings = get_settings()
    configured = getattr(settings, "public_base_url", "").strip()
    if not configured:
        if getattr(settings, "app_env", "development") != "development":
            raise HTTPException(status_code=503, detail="Cần cấu hình PUBLIC_BASE_URL cho môi trường deploy")
        return str(request.base_url).rstrip("/")
    base = _origin(configured)
    if base is None:
        raise HTTPException(status_code=503, detail="PUBLIC_BASE_URL cần là origin HTTP(S) hợp lệ")
    if getattr(settings, "app_env", "development") != "development" and not base.startswith("https://"):
        raise HTTPException(status_code=503, detail="PUBLIC_BASE_URL cần dùng HTTPS khi deploy")
    return base


def _secure_cookie(request: Request) -> bool:
    return _public_base(request).startswith("https://")


def _referer_origin(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        return _origin(f"{parsed.scheme}://{parsed.netloc}")
    except ValueError:
        return None


def _allowed_origins(shop: Shop) -> list[str]:
    try:
        data = json.loads(shop.allowed_origins)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [origin for value in data if isinstance(value, str) if (origin := _origin(value))]


def _check_embed_origin(shop: Shop, origin: str | None, app_origin: str | None = None) -> str:
    normalized = _origin(origin)
    if normalized is None or (normalized != app_origin and normalized not in _allowed_origins(shop)):
        raise HTTPException(status_code=403, detail="Domain này chưa được shop cho phép nhúng chatbot")
    return normalized


def _admin_csrf_check(request: Request) -> None:
    supplied = _referer_origin(request.headers.get("origin"))
    allowed_origins = {_origin(_public_base(request))}
    if getattr(get_settings(), "app_env", "development") == "development":
        allowed_origins.add(_origin(str(request.base_url).rstrip("/")))
    if supplied is not None and supplied not in allowed_origins:
        raise HTTPException(status_code=403, detail="Nguồn yêu cầu quản trị không hợp lệ")


def _shop_payload(shop: Shop, request: Request, include_origins: bool = True) -> dict:
    base = _public_base(request)
    result = {
        "id": str(shop.id),
        "slug": shop.slug,
        "name": shop.display_name,
        "welcome_message": shop.welcome_message,
        "theme_color": shop.theme_color,
        "enabled": shop.enabled,
        "chat_url": f"{base}/chat/{shop.slug}",
        "embed_url": f"{base}/embed/{shop.slug}",
    }
    if include_origins:
        result["allowed_origins"] = _allowed_origins(shop)
    return result


def _inbox_shop_payload(shop: Shop, request: Request) -> dict:
    """Return only the shop details staff need for the inbox."""
    return {
        "id": str(shop.id),
        "slug": shop.slug,
        "name": shop.display_name,
        "enabled": shop.enabled,
        "chat_url": f"{_public_base(request)}/chat/{shop.slug}",
    }


def _shop_or_404(db: Session, slug: str, active_only: bool = False) -> Shop:
    shop = db.scalar(select(Shop).where(Shop.slug == slug))
    if shop is None or (active_only and not shop.enabled):
        raise HTTPException(status_code=404, detail="Không tìm thấy shop")
    return shop


def _shop_id_or_404(db: Session, shop_id: UUID) -> Shop:
    shop = db.get(Shop, shop_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy shop")
    return shop


def _clean_origins(values: list[str]) -> list[str]:
    clean: list[str] = []
    for value in values:
        origin = _origin(value)
        if origin is None:
            raise HTTPException(status_code=422, detail="Mỗi domain cần là origin đầy đủ, ví dụ https://shop.example")
        if origin not in clean:
            clean.append(origin)
    return clean


def _products(db: Session, tenant_id: UUID, active_only: bool = False) -> list[Product]:
    query = select(Product).where(Product.tenant_id == tenant_id).order_by(Product.name)
    if active_only:
        query = query.where(Product.active.is_(True))
    return list(db.scalars(query.limit(200)))


def _product_payload(product: Product) -> dict:
    return {
        "id": str(product.id),
        "sku": product.sku,
        "name": product.name,
        "description": product.description,
        "price": str(product.price),
        "currency": product.currency,
        "active": product.active,
    }


def _utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@admin_router.get("/admin/api/shops/{shop_id}/conversations")
def admin_list_public_conversations(
    shop_id: UUID,
    _identity: Annotated[tuple[str, str], Depends(require_staff)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> list[dict]:
    shop = _shop_id_or_404(db, shop_id)
    sessions = list(
        db.scalars(
            select(PublicChatSession)
            .where(
                PublicChatSession.tenant_id == shop.tenant_id,
                PublicChatSession.shop_id == shop.id,
                PublicChatSession.conversation_id.is_not(None),
                select(PublicChatMessage.id)
                .where(
                    PublicChatMessage.tenant_id == shop.tenant_id,
                    PublicChatMessage.session_id == PublicChatSession.id,
                )
                .exists(),
            )
            .order_by(PublicChatSession.created_at.desc())
            .limit(100)
        )
    )
    if not sessions:
        return []

    session_ids = [item.id for item in sessions]
    messages = list(
        db.scalars(
            select(PublicChatMessage)
            .where(
                PublicChatMessage.tenant_id == shop.tenant_id,
                PublicChatMessage.session_id.in_(session_ids),
            )
            .order_by(PublicChatMessage.created_at.desc(), PublicChatMessage.id.desc())
            .limit(5000)
        )
    )
    last_by_session: dict[UUID, PublicChatMessage] = {}
    for item in messages:
        last_by_session.setdefault(item.session_id, item)
    pending_conversations = set(
        db.scalars(
            select(ReplyDraft.conversation_id)
            .join(
                PublicChatSession,
                (PublicChatSession.tenant_id == ReplyDraft.tenant_id)
                & (PublicChatSession.conversation_id == ReplyDraft.conversation_id),
            )
            .where(
                ReplyDraft.tenant_id == shop.tenant_id,
                ReplyDraft.source == "public_chat",
                ReplyDraft.status == "pending",
                PublicChatSession.shop_id == shop.id,
            )
        )
    )
    return [
        {
            "conversation_id": str(session.conversation_id),
            "session_id": str(session.id),
            "customer_label": f"Khách {str(session.id)[-6:]}",
            "last_message": last_by_session[session.id].body[:240],
            "last_message_at": last_by_session[session.id].created_at.isoformat(),
            "expires_at": session.expires_at.isoformat(),
            "active": _utc_datetime(session.expires_at) > now_utc(),
            "has_pending_draft": session.conversation_id in pending_conversations,
        }
        for session in sessions
        if session.id in last_by_session
    ]


@admin_router.get("/admin/api/shops/{shop_id}/consultation-requests")
def admin_list_consultation_requests(
    shop_id: UUID,
    _identity: Annotated[tuple[str, str], Depends(require_staff)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> list[dict]:
    shop = _shop_id_or_404(db, shop_id)
    rows = list(
        db.scalars(
            select(ConsultationRequest)
            .where(
                ConsultationRequest.tenant_id == shop.tenant_id,
                ConsultationRequest.shop_id == shop.id,
            )
            .order_by(ConsultationRequest.created_at.desc(), ConsultationRequest.id.desc())
            .limit(200)
        )
    )
    form_rows = list(
        db.scalars(
            select(GoogleFormSubmission)
            .where(
                GoogleFormSubmission.tenant_id == shop.tenant_id,
                GoogleFormSubmission.shop_id == shop.id,
            )
            .order_by(GoogleFormSubmission.submitted_at.desc(), GoogleFormSubmission.id.desc())
            .limit(200)
        )
    )
    try:
        combined = [
            (
                row.created_at,
                {
                    "id": str(row.id),
                    "created_at": row.created_at.isoformat(),
                    "name": decrypt_pii(row.name_ciphertext),
                    "phone": decrypt_pii(row.phone_ciphertext),
                    "desired_time": decrypt_pii(row.desired_time_ciphertext),
                    "note": decrypt_pii(row.note_ciphertext) if row.note_ciphertext else "",
                    "source": "chatbot",
                },
            )
            for row in rows
        ]
        combined.extend(
            (
                row.submitted_at,
                {
                    "id": str(row.id),
                    "created_at": row.submitted_at.isoformat(),
                    "name": decrypt_pii(row.name_ciphertext),
                    "phone": decrypt_pii(row.phone_ciphertext),
                    "desired_time": decrypt_pii(row.desired_time_ciphertext),
                    "note": decrypt_pii(row.note_ciphertext) if row.note_ciphertext else "",
                    "source": "google_forms",
                },
            )
            for row in form_rows
        )
        combined.sort(key=lambda item: _utc_datetime(item[0]), reverse=True)
        return [item[1] for item in combined[:200]]
    except PIIEncryptionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


def _public_conversation_session(db: Session, shop: Shop, conversation_id: UUID) -> PublicChatSession:
    chat_session = db.scalar(
        select(PublicChatSession).where(
            PublicChatSession.tenant_id == shop.tenant_id,
            PublicChatSession.shop_id == shop.id,
            PublicChatSession.conversation_id == conversation_id,
        )
    )
    if chat_session is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại")
    return chat_session


@admin_router.get("/admin/api/shops/{shop_id}/conversations/{conversation_id}")
def admin_get_public_conversation(
    shop_id: UUID,
    conversation_id: UUID,
    _identity: Annotated[tuple[str, str], Depends(require_staff)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> dict:
    shop = _shop_id_or_404(db, shop_id)
    chat_session = _public_conversation_session(db, shop, conversation_id)
    rows = list(
        db.execute(
            select(PublicChatMessage, Message.author)
            .outerjoin(
                Message,
                (Message.tenant_id == PublicChatMessage.tenant_id)
                & (Message.id == PublicChatMessage.crm_message_id),
            )
            .where(
                PublicChatMessage.tenant_id == shop.tenant_id,
                PublicChatMessage.session_id == chat_session.id,
            )
            .order_by(PublicChatMessage.created_at.desc(), PublicChatMessage.id.desc())
            .limit(300)
        )
    )
    rows.reverse()
    inbound_message_ids = [
        item.crm_message_id for item, _author in rows
        if item.direction == "user" and item.crm_message_id is not None
    ]
    drafts = list(
        db.scalars(
            select(ReplyDraft).where(
                ReplyDraft.tenant_id == shop.tenant_id,
                ReplyDraft.message_id.in_(inbound_message_ids),
                ReplyDraft.source == "public_chat",
            )
        )
    ) if inbound_message_ids else []
    drafts_by_message = {item.message_id: item for item in drafts}

    events: list[tuple[datetime, dict]] = []
    for item, author in rows:
        if item.direction == "user":
            draft = drafts_by_message.get(item.crm_message_id)
            draft_payload = None
            if draft is not None:
                draft_payload = {
                    "id": str(draft.id),
                    "body": draft.body,
                    "status": draft.status,
                    "created_at": draft.created_at.isoformat(),
                    "decided_by": draft.decided_by,
                    "decision_note": draft.decision_note,
                }
                if draft.status == "approved":
                    answer_at = draft.decided_at or draft.created_at
                    events.append((
                        _utc_datetime(answer_at),
                        {
                            "role": "assistant",
                            "body": draft.body,
                            "author": "Hermes",
                            "status": "approved",
                            "created_at": answer_at.isoformat(),
                        },
                    ))
            events.append((
                _utc_datetime(item.created_at),
                {
                    "role": "user",
                    "body": item.body,
                    "author": f"Khách {str(chat_session.id)[-6:]}",
                    "status": draft.status if draft else "unavailable",
                    "created_at": item.created_at.isoformat(),
                    "draft": draft_payload,
                },
            ))
        elif item.direction == "assistant":
            events.append((
                _utc_datetime(item.created_at),
                {
                    "role": "assistant",
                    "body": item.body,
                    "author": f"Nhân viên · {author}" if author else "Nhân viên",
                    "status": "sent",
                    "created_at": item.created_at.isoformat(),
                },
            ))

    events.sort(key=lambda event: event[0])
    return {
        "conversation_id": str(conversation_id),
        "customer_label": f"Khách {str(chat_session.id)[-6:]}",
        "started_at": chat_session.created_at.isoformat(),
        "expires_at": chat_session.expires_at.isoformat(),
        "active": _utc_datetime(chat_session.expires_at) > now_utc(),
        "messages": [payload for _timestamp, payload in events],
    }


@admin_router.post("/admin/api/shops/{shop_id}/conversations/{conversation_id}/messages", status_code=201)
def admin_send_staff_message(
    shop_id: UUID,
    conversation_id: UUID,
    data: AdminStaffMessageIn,
    request: Request,
    identity: Annotated[tuple[str, str], Depends(require_staff)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> dict[str, str]:
    username, _role = identity
    _admin_csrf_check(request)
    shop = _shop_id_or_404(db, shop_id)
    chat_session = _public_conversation_session(db, shop, conversation_id)
    if _utc_datetime(chat_session.expires_at) <= now_utc():
        raise HTTPException(status_code=410, detail="Phiên chat đã hết hạn; không thể gửi tin mới")
    body = data.message.strip()
    if not body:
        raise HTTPException(status_code=422, detail="Tin nhắn không được để trống")
    safe_body = redact_pii(body)
    has_customer_message = db.scalar(
        select(PublicChatMessage.id).where(
            PublicChatMessage.tenant_id == shop.tenant_id,
            PublicChatMessage.session_id == chat_session.id,
            PublicChatMessage.direction == "user",
        ).limit(1)
    )
    if has_customer_message is None:
        raise HTTPException(status_code=409, detail="Khách chưa gửi tin nhắn trong hội thoại này")

    pending_draft = db.scalar(
        select(ReplyDraft).where(
            ReplyDraft.tenant_id == shop.tenant_id,
            ReplyDraft.conversation_id == conversation_id,
            ReplyDraft.source == "public_chat",
            ReplyDraft.status == "pending",
        )
    )
    if pending_draft is not None:
        pending_draft.status = "rejected"
        pending_draft.decided_by = f"platform:{username}"[:120]
        pending_draft.decision_note = "Được thay bằng tin nhắn do nhân viên gửi"
        pending_draft.decided_at = now_utc()

    crm_message = Message(
        tenant_id=shop.tenant_id,
        conversation_id=conversation_id,
        direction="outbound",
        body=safe_body,
        author=username[:120],
    )
    db.add(crm_message)
    db.flush()
    public_message = PublicChatMessage(
        tenant_id=shop.tenant_id,
        session_id=chat_session.id,
        request_id=uuid4(),
        crm_message_id=crm_message.id,
        direction="assistant",
        body=safe_body,
    )
    db.add(public_message)
    db.commit()
    return {
        "id": str(public_message.id),
        "status": "sent",
        "author": username,
        "body": safe_body,
        "created_at": public_message.created_at.isoformat(),
    }


def _public_product_payload(product: Product) -> dict:
    return {
        "name": redact_pii(product.name),
        "description": redact_pii(product.description),
        "price": str(product.price),
        "currency": product.currency,
    }


def _session_from_authorization(
    db: Session,
    authorization: str | None,
    embed_origin: str | None,
    app_origin: str | None = None,
) -> PublicChatSession:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Phiên chat không hợp lệ")
    token = authorization[7:].strip()
    if len(token) < 32:
        raise HTTPException(status_code=401, detail="Phiên chat không hợp lệ")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    chat_session = db.scalar(select(PublicChatSession).where(PublicChatSession.token_hash == token_hash))
    now = now_utc()
    if chat_session is None:
        raise HTTPException(status_code=401, detail="Phiên chat không hợp lệ hoặc đã hết hạn")
    enter_tenant_scope(db, str(chat_session.tenant_id))
    expires_at = chat_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        db.execute(delete(PublicChatMessage).where(PublicChatMessage.tenant_id == chat_session.tenant_id,
                                                   PublicChatMessage.session_id == chat_session.id))
        db.delete(chat_session)
        db.commit()
        raise HTTPException(status_code=401, detail="Phiên chat đã hết hạn. Hãy bắt đầu phiên mới.")
    shop = db.scalar(
        select(Shop).where(Shop.id == chat_session.shop_id, Shop.tenant_id == chat_session.tenant_id)
    )
    if shop is None or not shop.enabled:
        raise HTTPException(status_code=403, detail="Chatbot của shop hiện đang tắt")
    if chat_session.embed_origin is None:
        if embed_origin:
            raise HTTPException(status_code=403, detail="Nguồn phiên chat không khớp")
    else:
        normalized = _check_embed_origin(shop, embed_origin, app_origin)
        if not hmac.compare_digest(normalized, chat_session.embed_origin):
            raise HTTPException(status_code=403, detail="Nguồn phiên chat không khớp")
    return chat_session


def _mock_status() -> str:
    return "mock" if not get_settings().hermes_api_key else "hermes"


@admin_router.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html")


@admin_router.post("/admin/api/login")
def admin_login(data: AdminLoginIn, request: Request, response: Response) -> dict[str, str]:
    _admin_csrf_check(request)
    rate_limiter.check(f"admin-login:{_client_key(request)}", 10)
    settings = get_settings()
    _admin_key()  # Fail closed until a strong signing secret is configured.
    if len(settings.admin_password) < 12:
        raise HTTPException(status_code=503, detail="Mật khẩu admin cần có ít nhất 12 ký tự")
    user_ok = hmac.compare_digest(data.username.encode(), settings.admin_username.encode())
    pass_ok = hmac.compare_digest(data.password.encode(), settings.admin_password.encode())
    role = "admin" if user_ok and pass_ok else None
    staff_username = settings.staff_username.strip()
    staff_name_ok = bool(staff_username) and staff_username != settings.admin_username and hmac.compare_digest(data.username.encode(), staff_username.encode())
    if role is None and staff_name_ok:
        if len(settings.staff_password) < 12:
            raise HTTPException(status_code=503, detail="Mật khẩu nhân viên cần có ít nhất 12 ký tự")
        if hmac.compare_digest(data.password.encode(), settings.staff_password.encode()):
            role = "staff"
    if role is None:
        raise HTTPException(status_code=401, detail="Tên đăng nhập hoặc mật khẩu chưa đúng")
    response.set_cookie(
        COOKIE_NAME,
        _new_admin_cookie(data.username, role),
        max_age=COOKIE_TTL_SECONDS,
        httponly=True,
        secure=_secure_cookie(request),
        samesite="strict",
        path="/admin",
    )
    return {"status": "ok", "role": role}


@admin_router.post("/admin/api/logout")
def admin_logout(
    request: Request,
    response: Response,
    _identity: Annotated[tuple[str, str], Depends(require_staff)],
) -> dict[str, str]:
    _admin_csrf_check(request)
    response.delete_cookie(COOKIE_NAME, path="/admin", httponly=True, secure=_secure_cookie(request), samesite="strict")
    return {"status": "ok"}


@admin_router.get("/admin/api/me")
def admin_me(identity: Annotated[tuple[str, str], Depends(require_staff)]) -> dict[str, str]:
    username, role = identity
    return {"username": username, "role": role}


@admin_router.get("/admin/api/model/status")
def admin_model_status(
    _username: Annotated[str, Depends(require_admin)],
) -> dict[str, str | bool]:
    settings = get_settings()
    configured = bool(
        settings.hermes_api_key.strip()
        and settings.hermes_api_url.strip()
        and settings.hermes_model.strip()
    )
    return {
        "configured": configured,
        "model": settings.hermes_model if configured else "",
    }


@admin_router.post("/admin/api/model/test")
async def admin_test_model(
    request: Request,
    _username: Annotated[str, Depends(require_admin)],
    hermes: Annotated[HermesClient, Depends(get_hermes_client)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> dict[str, str]:
    _admin_csrf_check(request)
    rate_limiter.check("model-api-test", 5)
    settings = get_settings()
    if not settings.hermes_api_key.strip():
        raise HTTPException(
            status_code=503,
            detail="Chưa cấu hình HERMES_API_KEY trên Vercel. Hãy lưu khóa ở Environment Variables rồi redeploy.",
        )
    try:
        await hermes.test_connection()
    except HermesError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    db.execute(
        update(ShopKnowledge).values(api_fingerprint=api_config_fingerprint(settings))
    )
    db.commit()
    return {"status": "connected", "model": settings.hermes_model}


@admin_router.get("/admin/api/shops")
def admin_list_shops(
    request: Request,
    identity: Annotated[tuple[str, str], Depends(require_staff)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> list[dict]:
    shops = list(db.scalars(select(Shop).order_by(Shop.display_name)))
    if identity[1] != "admin":
        return [_inbox_shop_payload(shop, request) for shop in shops]
    return [
        {**_shop_payload(shop, request), "products": [_product_payload(p) for p in _products(db, shop.tenant_id)]}
        for shop in shops
    ]


@admin_router.get("/admin/api/shops/{shop_id}/knowledge")
def admin_get_shop_knowledge(
    shop_id: UUID,
    _username: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> dict:
    shop = _shop_id_or_404(db, shop_id)
    knowledge = db.scalar(
        select(ShopKnowledge).where(
            ShopKnowledge.tenant_id == shop.tenant_id,
            ShopKnowledge.shop_id == shop.id,
        )
    )
    if knowledge is None:
        return {
            "synced": False,
            "api_verified": False,
            "source_url": "",
            "page_count": 0,
            "content_length": 0,
            "synced_at": None,
        }
    return {
        "synced": bool(knowledge.content.strip()),
        "api_verified": knowledge.api_fingerprint == api_config_fingerprint(get_settings()),
        "source_url": knowledge.source_url,
        "page_count": knowledge.page_count,
        "content_length": len(knowledge.content),
        "synced_at": knowledge.synced_at.isoformat(),
    }


@admin_router.post("/admin/api/shops/{shop_id}/knowledge/sync")
async def admin_sync_shop_knowledge(
    shop_id: UUID,
    data: WebsiteSyncIn,
    request: Request,
    _username: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_platform_db)],
    hermes: Annotated[HermesClient, Depends(get_hermes_client)],
) -> dict:
    _admin_csrf_check(request)
    rate_limiter.check(f"website-sync:{shop_id}", 3)
    shop = _shop_id_or_404(db, shop_id)
    try:
        await hermes.test_connection()
    except HermesError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    fingerprint = api_config_fingerprint(get_settings())
    try:
        result = await read_public_website(data.source_url)
    except WebsiteFetchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    safe_content = redact_pii(str(result["content"]))
    knowledge = db.scalar(
        select(ShopKnowledge).where(
            ShopKnowledge.tenant_id == shop.tenant_id,
            ShopKnowledge.shop_id == shop.id,
        )
    )
    if knowledge is None:
        knowledge = ShopKnowledge(
            tenant_id=shop.tenant_id,
            shop_id=shop.id,
            source_url=str(result["source_url"]),
            content=safe_content,
            page_count=int(result["page_count"]),
            api_fingerprint=fingerprint,
            synced_at=now_utc(),
        )
        db.add(knowledge)
    else:
        knowledge.source_url = str(result["source_url"])
        knowledge.content = safe_content
        knowledge.page_count = int(result["page_count"])
        knowledge.api_fingerprint = fingerprint
        knowledge.synced_at = now_utc()
    db.commit()
    db.refresh(knowledge)
    return {
        "synced": True,
        "api_verified": True,
        "source_url": knowledge.source_url,
        "page_count": knowledge.page_count,
        "content_length": len(knowledge.content),
        "synced_at": knowledge.synced_at.isoformat(),
    }


@admin_router.post("/admin/api/shops", status_code=status.HTTP_201_CREATED)
def admin_create_shop(
    data: AdminShopCreate,
    request: Request,
    _username: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> dict:
    _admin_csrf_check(request)
    if not SLUG_RE.fullmatch(data.slug):
        raise HTTPException(status_code=422, detail="Mã shop chỉ gồm chữ thường, số và dấu gạch ngang")
    tenant_id = uuid4()
    shop = Shop(
        tenant_id=tenant_id,
        slug=data.slug,
        display_name=data.name.strip(),
        welcome_message=data.welcome_message.strip(),
        theme_color=data.theme_color.lower(),
        allowed_origins=json.dumps(_clean_origins(data.allowed_origins)),
        enabled=True,
    )
    db.add(Tenant(id=tenant_id, name=data.name.strip()))
    db.flush()
    db.add(shop)
    try:
        db.commit()
        db.refresh(shop)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Mã shop này đã được dùng") from None
    return _shop_payload(shop, request)


@admin_router.put("/admin/api/shops/{shop_id}")
def admin_update_shop(
    shop_id: UUID,
    data: AdminShopUpdate,
    request: Request,
    _username: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> dict:
    _admin_csrf_check(request)
    shop = _shop_id_or_404(db, shop_id)
    shop.display_name = data.name.strip()
    shop.welcome_message = data.welcome_message.strip()
    shop.theme_color = data.theme_color.lower()
    shop.allowed_origins = json.dumps(_clean_origins(data.allowed_origins))
    shop.enabled = data.enabled
    db.commit()
    db.refresh(shop)
    return _shop_payload(shop, request)


@admin_router.post("/admin/api/shops/{shop_id}/products", status_code=status.HTTP_201_CREATED)
def admin_create_product(
    shop_id: UUID,
    data: AdminProductCreate,
    request: Request,
    _username: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> dict:
    _admin_csrf_check(request)
    shop = _shop_id_or_404(db, shop_id)
    product = Product(tenant_id=shop.tenant_id, **data.model_dump())
    db.add(product)
    try:
        db.commit()
        db.refresh(product)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="SKU này đã có trong shop") from None
    return _product_payload(product)


@admin_router.patch("/admin/api/shops/{shop_id}/products/{product_id}")
def admin_update_product(
    shop_id: UUID,
    product_id: UUID,
    data: AdminProductUpdate,
    request: Request,
    _username: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> dict:
    _admin_csrf_check(request)
    shop = _shop_id_or_404(db, shop_id)
    product = db.scalar(select(Product).where(Product.id == product_id, Product.tenant_id == shop.tenant_id))
    if product is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm")
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if value is not None:
            setattr(product, field, value)
    try:
        db.commit()
        db.refresh(product)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="SKU này đã được dùng trong shop") from None
    return _product_payload(product)


def _public_chat_draft_for_shop(db: Session, shop: Shop, draft_id: UUID) -> ReplyDraft | None:
    return db.scalar(
        select(ReplyDraft)
        .join(
            Message,
            (Message.tenant_id == ReplyDraft.tenant_id)
            & (Message.id == ReplyDraft.message_id),
        )
        .join(
            PublicChatMessage,
            (PublicChatMessage.tenant_id == Message.tenant_id)
            & (PublicChatMessage.crm_message_id == Message.id),
        )
        .join(
            PublicChatSession,
            (PublicChatSession.tenant_id == PublicChatMessage.tenant_id)
            & (PublicChatSession.id == PublicChatMessage.session_id),
        )
        .where(
            ReplyDraft.id == draft_id,
            ReplyDraft.tenant_id == shop.tenant_id,
            ReplyDraft.source == "public_chat",
            PublicChatSession.shop_id == shop.id,
        )
    )


@admin_router.get("/admin/api/shops/{shop_id}/drafts")
def admin_list_public_drafts(
    shop_id: UUID,
    _username: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> list[dict]:
    shop = _shop_id_or_404(db, shop_id)
    rows = db.execute(
        select(ReplyDraft, Message)
        .join(
            Message,
            (Message.tenant_id == ReplyDraft.tenant_id)
            & (Message.id == ReplyDraft.message_id),
        )
        .join(
            PublicChatMessage,
            (PublicChatMessage.tenant_id == Message.tenant_id)
            & (PublicChatMessage.crm_message_id == Message.id),
        )
        .join(
            PublicChatSession,
            (PublicChatSession.tenant_id == PublicChatMessage.tenant_id)
            & (PublicChatSession.id == PublicChatMessage.session_id),
        )
        .where(
            ReplyDraft.tenant_id == shop.tenant_id,
            ReplyDraft.source == "public_chat",
            ReplyDraft.status == "pending",
            PublicChatSession.shop_id == shop.id,
        )
        .order_by(ReplyDraft.created_at)
        .limit(100)
    )
    return [
        {
            "id": str(draft.id),
            "conversation_id": str(draft.conversation_id),
            "customer_message": message.body,
            "body": draft.body,
            "created_at": draft.created_at.isoformat(),
        }
        for draft, message in rows
    ]


def _decide_public_draft(
    shop_id: UUID,
    draft_id: UUID,
    decision: str,
    username: str,
    request: Request,
    db: Session,
) -> dict[str, str]:
    _admin_csrf_check(request)
    shop = _shop_id_or_404(db, shop_id)
    draft = _public_chat_draft_for_shop(db, shop, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản nháp")
    result = db.execute(
        update(ReplyDraft)
        .where(
            ReplyDraft.id == draft.id,
            ReplyDraft.tenant_id == shop.tenant_id,
            ReplyDraft.status == "pending",
        )
        .values(
            status=decision,
            decided_by=f"platform:{username}"[:120],
            decided_at=now_utc(),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bản nháp đã được xử lý")
    db.commit()
    return {"id": str(draft.id), "status": decision}


@admin_router.post("/admin/api/shops/{shop_id}/drafts/{draft_id}/approve")
def admin_approve_public_draft(
    shop_id: UUID,
    draft_id: UUID,
    request: Request,
    username: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> dict[str, str]:
    return _decide_public_draft(shop_id, draft_id, "approved", username, request, db)


@admin_router.post("/admin/api/shops/{shop_id}/drafts/{draft_id}/reject")
def admin_reject_public_draft(
    shop_id: UUID,
    draft_id: UUID,
    request: Request,
    username: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> dict[str, str]:
    return _decide_public_draft(shop_id, draft_id, "rejected", username, request, db)
