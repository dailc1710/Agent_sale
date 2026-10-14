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
from .hermes import HermesClient, HermesError
from .models import (
    Conversation,
    Customer,
    Message,
    PublicChatMessage,
    PublicChatSession,
    Product,
    ReplyDraft,
    Shop,
    Tenant,
    now_utc,
)
from .privacy import redact_pii
from .schemas import (
    AdminLoginIn,
    AdminProductCreate,
    AdminProductUpdate,
    AdminShopCreate,
    AdminShopUpdate,
    PublicMessageIn,
)


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


def _new_admin_cookie(username: str) -> str:
    payload = _b64(json.dumps({"sub": username, "exp": int(time.time()) + COOKIE_TTL_SECONDS}, separators=(",", ":")).encode())
    signature = hmac.new(_admin_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _admin_username(cookie: str | None) -> str | None:
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
        username = data["sub"]
        return username if username == get_settings().admin_username else None
    except (HTTPException, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def require_admin(
    cookie: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> str:
    username = _admin_username(cookie)
    if username is None:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập quản trị")
    return username


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
    expected = _origin(str(request.base_url).rstrip("/"))
    if supplied is not None and supplied != expected:
        raise HTTPException(status_code=403, detail="Nguồn yêu cầu quản trị không hợp lệ")


def _shop_payload(shop: Shop, request: Request, include_origins: bool = True) -> dict:
    base = str(request.base_url).rstrip("/")
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
    user_ok = hmac.compare_digest(data.username.encode(), settings.admin_username.encode())
    pass_ok = hmac.compare_digest(data.password.encode(), settings.admin_password.encode())
    if len(settings.admin_password) < 12:
        raise HTTPException(status_code=503, detail="Mật khẩu admin cần có ít nhất 12 ký tự")
    if not user_ok or not pass_ok:
        raise HTTPException(status_code=401, detail="Tên đăng nhập hoặc mật khẩu chưa đúng")
    response.set_cookie(
        COOKIE_NAME,
        _new_admin_cookie(settings.admin_username),
        max_age=COOKIE_TTL_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/admin",
    )
    return {"status": "ok"}


@admin_router.post("/admin/api/logout")
def admin_logout(
    request: Request,
    response: Response,
    _username: Annotated[str, Depends(require_admin)],
) -> dict[str, str]:
    _admin_csrf_check(request)
    response.delete_cookie(COOKIE_NAME, path="/admin", httponly=True, secure=request.url.scheme == "https", samesite="strict")
    return {"status": "ok"}


@admin_router.get("/admin/api/me")
def admin_me(username: Annotated[str, Depends(require_admin)]) -> dict[str, str]:
    return {"username": username}


@admin_router.get("/admin/api/shops")
def admin_list_shops(
    request: Request,
    _username: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_platform_db)],
) -> list[dict]:
    shops = list(db.scalars(select(Shop).order_by(Shop.display_name)))
    return [
        {**_shop_payload(shop, request), "products": [_product_payload(p) for p in _products(db, shop.tenant_id)]}
        for shop in shops
    ]


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
    if draft.status != "pending":
        raise HTTPException(status_code=409, detail="Bản nháp đã được xử lý")
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
