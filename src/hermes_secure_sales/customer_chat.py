import hashlib
import secrets
from datetime import timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .hub_admin import (
    STATIC_DIR,
    _allowed_origins,
    _check_embed_origin,
    _client_key,
    _mock_status,
    _origin,
    _products,
    _public_product_payload,
    _referer_origin,
    _session_from_authorization,
    _shop_or_404,
    rate_limiter,
)
from .config import get_settings
from .db import enter_tenant_scope
from .dependencies import get_hermes_client, get_public_session_db, get_public_shop_db
from .hermes import HermesClient, HermesError, api_config_fingerprint
from .models import (
    ConsultationRequest,
    Conversation,
    Customer,
    Message,
    PublicChatMessage,
    PublicChatSession,
    ReplyDraft,
    Shop,
    ShopKnowledge,
    now_utc,
)
from .pii_crypto import PIIEncryptionUnavailable, encrypt_pii
from .privacy import redact_pii
from .schemas import ConsultationRequestIn, PublicMessageIn


router = APIRouter()


@router.get("/chat/{slug}", include_in_schema=False)
def public_chat_page(slug: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "public-chat.html")


@router.get("/embed-demo", include_in_schema=False)
def embed_demo_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "embed-demo.html")


@router.get("/embed/{slug}", include_in_schema=False)
def embedded_chat_page(
    slug: str,
    request: Request,
    db: Session = Depends(get_public_shop_db),
) -> FileResponse:
    shop = _shop_or_404(db, slug, active_only=True)
    referer_origin = _referer_origin(request.headers.get("referer"))
    app_origin = _origin(str(request.base_url).rstrip("/"))
    if referer_origin is None or (
        referer_origin != app_origin and referer_origin not in _allowed_origins(shop)
    ):
        raise HTTPException(status_code=403, detail="Domain này chưa được shop cho phép nhúng chatbot")
    return FileResponse(STATIC_DIR / "public-chat.html")


@router.get("/public/shops/{slug}")
def public_shop_info(
    slug: str,
    request: Request,
    x_embed_origin: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_public_shop_db),
) -> dict:
    shop = _shop_or_404(db, slug, active_only=True)
    app_origin = _origin(str(request.base_url).rstrip("/"))
    embed_origin = _check_embed_origin(shop, x_embed_origin, app_origin) if x_embed_origin else None
    enter_tenant_scope(db, str(shop.tenant_id))
    knowledge = db.scalar(
        select(ShopKnowledge).where(
            ShopKnowledge.tenant_id == shop.tenant_id,
            ShopKnowledge.shop_id == shop.id,
        )
    )
    settings = get_settings()
    assistant_ready = bool(
        settings.hermes_api_key.strip()
        and settings.hermes_api_url.strip()
        and settings.hermes_model.strip()
        and knowledge is not None
        and knowledge.content.strip()
        and knowledge.api_fingerprint == api_config_fingerprint(settings)
    )
    return {
        "slug": shop.slug,
        "name": shop.display_name,
        "welcome_message": shop.welcome_message,
        "theme_color": shop.theme_color,
        "mode": _mock_status(),
        "assistant_ready": assistant_ready,
        "setup_message": (
            ""
            if assistant_ready
            else "Trợ lý đang được quản trị viên kết nối API và cập nhật nguồn website. Vui lòng quay lại sau."
        ),
        "products": [_public_product_payload(item) for item in _products(db, shop.tenant_id, True)[:30]],
        "embed_origin": embed_origin,
    }


@router.post("/public/shops/{slug}/sessions", status_code=status.HTTP_201_CREATED)
def create_public_session(
    slug: str,
    request: Request,
    x_embed_origin: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_public_shop_db),
) -> dict:
    settings = get_settings()
    rate_limiter.check(f"session:{_client_key(request)}", settings.public_rate_limit_per_minute)
    shop = _shop_or_404(db, slug, active_only=True)
    app_origin = _origin(str(request.base_url).rstrip("/"))
    embed_origin = _check_embed_origin(shop, x_embed_origin, app_origin) if x_embed_origin else None
    enter_tenant_scope(db, str(shop.tenant_id))

    expired_sessions = list(
        db.scalars(
            select(PublicChatSession).where(
                PublicChatSession.tenant_id == shop.tenant_id,
                PublicChatSession.shop_id == shop.id,
                PublicChatSession.expires_at <= now_utc(),
            )
        )
    )
    expired_ids = [item.id for item in expired_sessions]
    expired_conversation_ids = [item.conversation_id for item in expired_sessions if item.conversation_id]
    expired_customer_ids = list(
        db.scalars(
            select(Conversation.customer_id).where(
                Conversation.tenant_id == shop.tenant_id,
                Conversation.id.in_(expired_conversation_ids),
            )
        )
    ) if expired_conversation_ids else []
    if expired_ids:
        db.execute(
            delete(PublicChatMessage).where(
                PublicChatMessage.tenant_id == shop.tenant_id,
                PublicChatMessage.session_id.in_(expired_ids),
            )
        )
        db.execute(delete(PublicChatSession).where(PublicChatSession.id.in_(expired_ids)))
    if expired_conversation_ids:
        db.execute(
            delete(Conversation).where(
                Conversation.tenant_id == shop.tenant_id,
                Conversation.id.in_(expired_conversation_ids),
            )
        )
    if expired_customer_ids:
        db.execute(
            delete(Customer).where(
                Customer.tenant_id == shop.tenant_id,
                Customer.id.in_(expired_customer_ids),
            )
        )

    token = secrets.token_urlsafe(32)
    customer = Customer(id=uuid4(), tenant_id=shop.tenant_id)
    conversation = Conversation(id=uuid4(), tenant_id=shop.tenant_id, customer_id=customer.id)
    db.add(customer)
    db.flush()
    db.add(conversation)
    db.flush()
    chat_session = PublicChatSession(
        tenant_id=shop.tenant_id,
        shop_id=shop.id,
        conversation_id=conversation.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        embed_origin=embed_origin,
        expires_at=now_utc() + timedelta(hours=max(1, min(settings.public_session_hours, 24))),
    )
    db.add(chat_session)
    db.commit()
    db.refresh(chat_session)
    return {"session_token": token, "expires_at": chat_session.expires_at.isoformat(), "mode": _mock_status()}


@router.get("/public/sessions/me")
def public_session_history(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_embed_origin: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_public_session_db),
) -> dict:
    rate_limiter.check(f"history:{_client_key(request)}", get_settings().public_rate_limit_per_minute)
    chat_session = _session_from_authorization(
        db, authorization, x_embed_origin, _origin(str(request.base_url).rstrip("/"))
    )
    messages = list(
        db.scalars(
            select(PublicChatMessage)
            .where(
                PublicChatMessage.tenant_id == chat_session.tenant_id,
                PublicChatMessage.session_id == chat_session.id,
            )
            .order_by(PublicChatMessage.created_at, PublicChatMessage.id)
            .limit(300)
        )
    )
    crm_message_ids = [
        item.crm_message_id for item in messages
        if item.direction == "user" and item.crm_message_id
    ]
    drafts = list(
        db.scalars(
            select(ReplyDraft).where(
                ReplyDraft.tenant_id == chat_session.tenant_id,
                ReplyDraft.message_id.in_(crm_message_ids),
                ReplyDraft.source == "public_chat",
            )
        )
    ) if crm_message_ids else []
    drafts_by_message = {item.message_id: item for item in drafts}
    history: list[dict] = []
    for item in messages:
        if item.direction == "assistant":
            history.append(
                {
                    "role": "assistant",
                    "body": item.body,
                    "status": "sent",
                    "author": "Nhân viên",
                    "created_at": item.created_at.isoformat(),
                }
            )
            continue
        draft = drafts_by_message.get(item.crm_message_id)
        history.append(
            {
                "role": "user",
                "body": item.body,
                "status": draft.status if draft else "unavailable",
                "created_at": item.created_at.isoformat(),
            }
        )
        if draft is not None and draft.status == "approved":
            history.append(
                {
                    "role": "assistant",
                    "body": draft.body,
                    "status": "approved",
                    "author": "Hermes",
                    "created_at": draft.decided_at.isoformat() if draft.decided_at else draft.created_at.isoformat(),
                }
            )
    return {"messages": history}


@router.get("/public/sessions/consultation")
def public_consultation_status(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_embed_origin: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_public_session_db),
) -> dict[str, bool]:
    rate_limiter.check(f"consultation-status:{_client_key(request)}", get_settings().public_rate_limit_per_minute)
    chat_session = _session_from_authorization(
        db, authorization, x_embed_origin, _origin(str(request.base_url).rstrip("/"))
    )
    submitted = db.scalar(
        select(ConsultationRequest.id).where(
            ConsultationRequest.tenant_id == chat_session.tenant_id,
            ConsultationRequest.session_id == chat_session.id,
        )
    ) is not None
    return {"submitted": submitted}


@router.post("/public/sessions/consultation", status_code=status.HTTP_201_CREATED)
def submit_public_consultation(
    data: ConsultationRequestIn,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_embed_origin: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_public_session_db),
) -> dict[str, str]:
    settings = get_settings()
    rate_limiter.check(f"consultation-ip:{_client_key(request)}", settings.public_rate_limit_per_minute)
    chat_session = _session_from_authorization(
        db, authorization, x_embed_origin, _origin(str(request.base_url).rstrip("/"))
    )
    rate_limiter.check(f"consultation-session:{chat_session.id}", settings.public_rate_limit_per_minute)
    if not data.consent:
        raise HTTPException(status_code=422, detail="Cần đồng ý để shop liên hệ theo thông tin đã cung cấp.")

    existing = db.scalar(
        select(ConsultationRequest.id).where(
            ConsultationRequest.tenant_id == chat_session.tenant_id,
            ConsultationRequest.session_id == chat_session.id,
        )
    )
    if existing is not None:
        return {"status": "already_submitted"}

    phone = " ".join(data.phone.split())
    try:
        row = ConsultationRequest(
            tenant_id=chat_session.tenant_id,
            shop_id=chat_session.shop_id,
            session_id=chat_session.id,
            name_ciphertext=encrypt_pii(data.name.strip()),
            phone_ciphertext=encrypt_pii(phone),
            desired_time_ciphertext=encrypt_pii(data.desired_time.strip()),
            note_ciphertext=encrypt_pii(data.note.strip()) if data.note.strip() else None,
        )
    except PIIEncryptionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # A duplicate concurrent submit is safe and stays attached to the same session.
        return {"status": "already_submitted"}
    except PIIEncryptionUnavailable as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return {"status": "submitted"}


def _approved_public_history(
    db: Session,
    chat_session: PublicChatSession,
    exclude_request_id,
) -> list[dict[str, str]]:
    rows = list(
        db.scalars(
            select(PublicChatMessage)
            .where(
                PublicChatMessage.tenant_id == chat_session.tenant_id,
                PublicChatMessage.session_id == chat_session.id,
                PublicChatMessage.request_id != exclude_request_id,
            )
            .order_by(PublicChatMessage.created_at, PublicChatMessage.id)
            .limit(100)
        )
    )
    message_ids = [
        item.crm_message_id for item in rows
        if item.direction == "user" and item.crm_message_id
    ]
    drafts = list(
        db.scalars(
            select(ReplyDraft).where(
                ReplyDraft.tenant_id == chat_session.tenant_id,
                ReplyDraft.message_id.in_(message_ids),
                ReplyDraft.source == "public_chat",
                ReplyDraft.status == "approved",
            )
        )
    ) if message_ids else []
    drafts_by_message = {item.message_id: item for item in drafts}
    history: list[dict[str, str]] = []
    for item in rows:
        if item.direction == "assistant":
            history.append({"role": "assistant", "content": redact_pii(item.body)})
            continue
        history.append({"role": "user", "content": redact_pii(item.body)})
        approved = drafts_by_message.get(item.crm_message_id)
        if approved is not None:
            history.append({"role": "assistant", "content": redact_pii(approved.body)})
    return history[-12:]


@router.post("/public/sessions/messages")
async def public_chat_message(
    data: PublicMessageIn,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_embed_origin: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_public_session_db),
    hermes: HermesClient = Depends(get_hermes_client),
) -> dict:
    settings = get_settings()
    rate_limiter.check(f"message-ip:{_client_key(request)}", settings.public_rate_limit_per_minute)
    chat_session = _session_from_authorization(
        db, authorization, x_embed_origin, _origin(str(request.base_url).rstrip("/"))
    )
    rate_limiter.check(f"message-session:{chat_session.id}", settings.public_rate_limit_per_minute)
    knowledge = db.scalar(
        select(ShopKnowledge).where(
            ShopKnowledge.tenant_id == chat_session.tenant_id,
            ShopKnowledge.shop_id == chat_session.shop_id,
        )
    )
    if (
        not settings.hermes_api_key.strip()
        or not settings.hermes_api_url.strip()
        or not settings.hermes_model.strip()
        or knowledge is None
        or not knowledge.content.strip()
        or knowledge.api_fingerprint != api_config_fingerprint(settings)
    ):
        raise HTTPException(
            status_code=503,
            detail="Trợ lý đang được quản trị viên kết nối API và cập nhật nguồn website. Vui lòng quay lại sau.",
        )

    safe_message = redact_pii(data.message)
    user_message = db.scalar(
        select(PublicChatMessage).where(
            PublicChatMessage.tenant_id == chat_session.tenant_id,
            PublicChatMessage.session_id == chat_session.id,
            PublicChatMessage.request_id == data.request_id,
            PublicChatMessage.direction == "user",
        )
    )
    if user_message is not None and user_message.body != safe_message:
        raise HTTPException(status_code=409, detail="Mã yêu cầu chat đã được dùng")

    crm_message = (
        db.get(Message, user_message.crm_message_id)
        if user_message is not None and user_message.crm_message_id
        else None
    )
    if crm_message is not None:
        existing_draft = db.scalar(
            select(ReplyDraft).where(
                ReplyDraft.tenant_id == chat_session.tenant_id,
                ReplyDraft.message_id == crm_message.id,
                ReplyDraft.source == "public_chat",
            )
        )
        if existing_draft is not None:
            return {"status": existing_draft.status, "mode": _mock_status()}

    if user_message is None:
        turn_count = db.scalar(
            select(func.count()).select_from(PublicChatMessage).where(
                PublicChatMessage.tenant_id == chat_session.tenant_id,
                PublicChatMessage.session_id == chat_session.id,
                PublicChatMessage.direction == "user",
            )
        ) or 0
        if turn_count >= 50:
            raise HTTPException(status_code=429, detail="Phiên chat đã đạt giới hạn. Hãy bắt đầu phiên mới.")

    if user_message is None and chat_session.conversation_id is not None:
        pending_draft = db.scalar(
            select(ReplyDraft.id)
            .where(
                ReplyDraft.tenant_id == chat_session.tenant_id,
                ReplyDraft.conversation_id == chat_session.conversation_id,
                ReplyDraft.source == "public_chat",
                ReplyDraft.status == "pending",
            )
            .limit(1)
        )
        if pending_draft is not None:
            raise HTTPException(status_code=409, detail="Hãy chờ shop duyệt câu trả lời trước khi gửi tin nhắn tiếp theo.")

    if chat_session.conversation_id is None:
        customer = Customer(id=uuid4(), tenant_id=chat_session.tenant_id)
        conversation = Conversation(id=uuid4(), tenant_id=chat_session.tenant_id, customer_id=customer.id)
        db.add(customer)
        db.flush()
        db.add(conversation)
        db.flush()
        chat_session.conversation_id = conversation.id
    if crm_message is None:
        crm_message = Message(
            tenant_id=chat_session.tenant_id,
            conversation_id=chat_session.conversation_id,
            direction="inbound",
            body=safe_message,
        )
        db.add(crm_message)
        db.flush()
    if user_message is None:
        user_message = PublicChatMessage(
            tenant_id=chat_session.tenant_id,
            session_id=chat_session.id,
            request_id=data.request_id,
            crm_message_id=crm_message.id,
            direction="user",
            body=safe_message,
        )
        db.add(user_message)
    else:
        user_message.crm_message_id = crm_message.id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        user_message = db.scalar(
            select(PublicChatMessage).where(
                PublicChatMessage.tenant_id == chat_session.tenant_id,
                PublicChatMessage.session_id == chat_session.id,
                PublicChatMessage.request_id == data.request_id,
                PublicChatMessage.direction == "user",
            )
        )
        if user_message is None or user_message.body != safe_message or user_message.crm_message_id is None:
            raise HTTPException(status_code=409, detail="Mã yêu cầu chat đã được dùng") from None
        crm_message = db.get(Message, user_message.crm_message_id)
        if crm_message is None:
            raise HTTPException(status_code=409, detail="Không thể khôi phục phiên chat") from None

    products = _products(db, chat_session.tenant_id, active_only=True)[:30]
    product_context = [
        {
            "sku": redact_pii(item.sku),
            "name": redact_pii(item.name),
            "description": redact_pii(item.description),
            "price": str(item.price),
            "currency": item.currency,
        }
        for item in products
    ]
    history = _approved_public_history(db, chat_session, data.request_id)
    try:
        reply = redact_pii(
            await hermes.generate(
                safe_message,
                product_context,
                history,
                website_context=redact_pii(knowledge.content),
            )
        )
    except HermesError:
        raise HTTPException(status_code=502, detail="Chatbot tạm thời chưa trả lời được. Vui lòng thử lại.") from None

    draft = ReplyDraft(
        tenant_id=chat_session.tenant_id,
        conversation_id=chat_session.conversation_id,
        message_id=crm_message.id,
        body=reply,
        source="public_chat",
        status="pending",
    )
    db.add(draft)
    try:
        db.commit()
        db.refresh(draft)
    except IntegrityError:
        db.rollback()
        draft = db.scalar(
            select(ReplyDraft).where(
                ReplyDraft.tenant_id == chat_session.tenant_id,
                ReplyDraft.message_id == crm_message.id,
                ReplyDraft.source == "public_chat",
            )
        )
    if draft is None:
        raise HTTPException(status_code=502, detail="Không thể lưu bản nháp phản hồi")
    return {"status": draft.status, "mode": _mock_status()}
