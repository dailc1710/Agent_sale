import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import UserContext, get_current_user, require_roles
from .dependencies import get_hermes_client, get_user_db, get_webhook_db
from .hermes import HermesClient, HermesError
from .models import Conversation, Customer, Message, Product, ReplyDraft, Tenant, WebhookEvent
from .privacy import redact_pii
from .secure_shop_admin import admin_router
from .customer_chat import router as public_router
from .google_forms import router as google_forms_router
from .schemas import (
    ChatHistoryOut,
    ChatMessageAccepted,
    ChatMessageIn,
    ChatTurnOut,
    DraftOut,
    ProductCreate,
    ProductOut,
    WebhookAccepted,
    WebhookMessage,
)
from .security import verify_webhook, webhook_event_key

app = FastAPI(
    title="Hermes Secure Sales Hub",
    version="0.1.0",
    description="Public tenant-scoped sales chatbot with anonymous sessions and PII redaction.",
)
PROVIDER_RE = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(public_router)
app.include_router(admin_router)
app.include_router(google_forms_router)


@app.get("/", include_in_schema=False)
def hub_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "home.html")


@app.get("/internal", include_in_schema=False)
def internal_chatbot_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _product_context(db: Session, tenant_id: UUID) -> list[dict[str, str]]:
    products = list(
        db.scalars(
            select(Product)
            .where(Product.tenant_id == tenant_id, Product.active.is_(True))
            .order_by(Product.name)
            .limit(30)
        )
    )
    return [
        {
            "sku": redact_pii(item.sku),
            "name": redact_pii(item.name),
            "description": redact_pii(item.description),
            "price": str(item.price),
            "currency": item.currency,
        }
        for item in products
    ]


def _conversation_history(
    db: Session,
    tenant_id: UUID,
    conversation_id: UUID,
    exclude_message_id: UUID | None = None,
) -> list[dict[str, str]]:
    messages = list(
        db.scalars(
            select(Message)
            .where(Message.tenant_id == tenant_id, Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.id)
            .limit(100)
        )
    )
    if not messages:
        return []
    message_ids = [message.id for message in messages]
    approved_drafts = db.scalars(
        select(ReplyDraft).where(
            ReplyDraft.tenant_id == tenant_id,
            ReplyDraft.message_id.in_(message_ids),
            ReplyDraft.status == "approved",
        )
    )
    drafts_by_message = {draft.message_id: draft for draft in approved_drafts}
    history: list[dict[str, str]] = []
    for message in messages:
        if message.id == exclude_message_id:
            continue
        role = "user" if message.direction == "inbound" else "assistant"
        history.append({"role": role, "content": redact_pii(message.body)})
        if message.direction == "inbound" and message.id in drafts_by_message:
            history.append(
                {"role": "assistant", "content": redact_pii(drafts_by_message[message.id].body)}
            )
    return history[-12:]


@app.get("/products", response_model=list[ProductOut])
def list_products(
    user: Annotated[UserContext, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_user_db)],
) -> list[Product]:
    return list(
        db.scalars(
            select(Product)
            .where(Product.tenant_id == user.tenant_id, Product.active.is_(True))
            .order_by(Product.name)
        )
    )


@app.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(
    data: ProductCreate,
    user: Annotated[UserContext, Depends(require_roles("tenant_admin"))],
    db: Annotated[Session, Depends(get_user_db)],
) -> Product:
    product = Product(tenant_id=user.tenant_id, **data.model_dump())
    db.add(product)
    try:
        db.commit()
        db.refresh(product)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Product SKU already exists") from None
    return product


@app.post("/chat/messages", response_model=ChatMessageAccepted, status_code=status.HTTP_202_ACCEPTED)
async def chat_message(
    data: ChatMessageIn,
    user: Annotated[UserContext, Depends(require_roles("tenant_admin", "sales_agent"))],
    db: Annotated[Session, Depends(get_user_db)],
    hermes: Annotated[HermesClient, Depends(get_hermes_client)],
) -> ChatMessageAccepted:
    if not user.can_access_customer(data.customer_id):
        raise HTTPException(status_code=404, detail="Customer not found")

    safe_message = redact_pii(data.message)
    message = db.scalar(
        select(Message).where(Message.id == data.request_id, Message.tenant_id == user.tenant_id)
    )
    if message is not None:
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.id == message.conversation_id,
                Conversation.tenant_id == user.tenant_id,
            )
        )
        if (
            conversation is None
            or conversation.customer_id != data.customer_id
            or (data.conversation_id is not None and conversation.id != data.conversation_id)
            or message.direction != "inbound"
            or message.body != safe_message
        ):
            raise HTTPException(status_code=409, detail="Chat request ID was already used")
    else:
        if data.conversation_id is not None:
            conversation = db.scalar(
                select(Conversation).where(
                    Conversation.id == data.conversation_id,
                    Conversation.tenant_id == user.tenant_id,
                )
            )
            if conversation is None or conversation.customer_id != data.customer_id:
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            customer = db.scalar(
                select(Customer).where(
                    Customer.id == data.customer_id,
                    Customer.tenant_id == user.tenant_id,
                )
            )
            if customer is None:
                db.add(Customer(id=data.customer_id, tenant_id=user.tenant_id))
                db.flush()
            conversation = Conversation(
                id=uuid4(),
                tenant_id=user.tenant_id,
                customer_id=data.customer_id,
            )
            db.add(conversation)
            db.flush()

    existing_draft = db.scalar(
        select(ReplyDraft).where(
            ReplyDraft.tenant_id == user.tenant_id,
            ReplyDraft.message_id == data.request_id,
        )
    )
    if existing_draft is not None:
        return ChatMessageAccepted(
            conversation_id=conversation.id,
            message_id=message.id,
            draft=existing_draft,
        )

    pending_draft = db.scalar(
        select(ReplyDraft.id)
        .where(
            ReplyDraft.tenant_id == user.tenant_id,
            ReplyDraft.conversation_id == conversation.id,
            ReplyDraft.status == "pending",
        )
        .limit(1)
    )
    if pending_draft is not None:
        db.rollback()
        raise HTTPException(status_code=409, detail="Review the pending reply before continuing this chat")

    if message is None:
        message = Message(
            id=data.request_id,
            tenant_id=user.tenant_id,
            conversation_id=conversation.id,
            direction="inbound",
            body=safe_message,
        )
        db.add(message)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Chat request ID was already used") from None

    try:
        model_reply = await hermes.generate(
            message.body,
            _product_context(db, user.tenant_id),
            _conversation_history(db, user.tenant_id, conversation.id, exclude_message_id=message.id),
        )
    except HermesError:
        raise HTTPException(status_code=502, detail="Reply generation is temporarily unavailable") from None

    draft = ReplyDraft(
        tenant_id=user.tenant_id,
        conversation_id=conversation.id,
        message_id=message.id,
        body=redact_pii(model_reply),
        source="chat",
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
                ReplyDraft.tenant_id == user.tenant_id,
                ReplyDraft.message_id == message.id,
            )
        )
    if draft is None:
        raise HTTPException(status_code=502, detail="Reply draft could not be saved")
    return ChatMessageAccepted(conversation_id=conversation.id, message_id=message.id, draft=draft)


@app.get("/chat/conversations/{conversation_id}", response_model=ChatHistoryOut)
def chat_history(
    conversation_id: UUID,
    user: Annotated[UserContext, Depends(require_roles("tenant_admin", "sales_agent", "reviewer", "viewer"))],
    db: Annotated[Session, Depends(get_user_db)],
) -> ChatHistoryOut:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == user.tenant_id,
        )
    )
    if conversation is None or not user.can_access_customer(conversation.customer_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = list(
        db.scalars(
            select(Message)
            .where(Message.tenant_id == user.tenant_id, Message.conversation_id == conversation.id)
            .order_by(Message.created_at, Message.id)
            .limit(100)
        )
    )
    drafts = list(
        db.scalars(
            select(ReplyDraft)
            .where(ReplyDraft.tenant_id == user.tenant_id, ReplyDraft.conversation_id == conversation.id)
            .order_by(ReplyDraft.created_at)
            .limit(100)
        )
    )
    drafts_by_message = {draft.message_id: draft for draft in drafts}
    turns: list[ChatTurnOut] = []
    for message in messages:
        turns.append(ChatTurnOut(role="customer", body=message.body))
        draft = drafts_by_message.get(message.id)
        if draft is not None:
            turns.append(
                ChatTurnOut(
                    role="assistant",
                    body=draft.body,
                    draft_id=draft.id,
                    draft_status=draft.status,
                )
            )
    return ChatHistoryOut(conversation_id=conversation.id, customer_id=conversation.customer_id, turns=turns)


@app.post(
    "/webhooks/{tenant_id}/{provider}/messages",
    response_model=WebhookAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_message(
    tenant_id: UUID,
    provider: str,
    request: Request,
    db: Annotated[Session, Depends(get_webhook_db)],
    hermes: Annotated[HermesClient, Depends(get_hermes_client)],
    x_hermes_timestamp: Annotated[str | None, Header()] = None,
    x_hermes_signature: Annotated[str | None, Header()] = None,
) -> WebhookAccepted:
    if not PROVIDER_RE.fullmatch(provider):
        raise HTTPException(status_code=404, detail="Unknown provider")
    raw_body = await request.body()
    if len(raw_body) > 64 * 1024:
        raise HTTPException(status_code=413, detail="Webhook payload is too large")
    verify_webhook(tenant_id, x_hermes_timestamp, x_hermes_signature, raw_body)
    try:
        event = WebhookMessage.model_validate_json(raw_body)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid webhook payload") from None
    event_key = webhook_event_key(tenant_id, event.event_id)

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    event_row = db.get(WebhookEvent, (tenant_id, event_key))
    if event_row is not None:
        message = db.get(Message, event_row.message_id) if event_row.message_id else None
    else:
        customer = db.scalar(
            select(Customer).where(
                Customer.id == event.customer_id,
                Customer.tenant_id == tenant_id,
            )
        )
        if customer is None:
            db.add(Customer(id=event.customer_id, tenant_id=tenant_id))
            db.flush()
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.id == event.conversation_id,
                Conversation.tenant_id == tenant_id,
            )
        )
        if conversation is not None and conversation.customer_id != event.customer_id:
            raise HTTPException(status_code=409, detail="Conversation customer does not match")
        if conversation is None:
            conversation = Conversation(
                id=event.conversation_id,
                tenant_id=tenant_id,
                customer_id=event.customer_id,
            )
            db.add(conversation)
            db.flush()
        message = Message(
            tenant_id=tenant_id,
            conversation_id=event.conversation_id,
            direction="inbound",
            body=redact_pii(event.message),
        )
        db.add(message)
        db.flush()
        event_row = WebhookEvent(
            tenant_id=tenant_id,
            external_event_id=event_key,
            message_id=message.id,
        )
        db.add(event_row)
        db.commit()

    if message is None:
        return WebhookAccepted(event_id=event_key)

    existing_draft = db.scalar(
        select(ReplyDraft).where(
            ReplyDraft.tenant_id == tenant_id,
            ReplyDraft.message_id == message.id,
        )
    )
    if existing_draft is not None:
        return WebhookAccepted(
            event_id=event_key,
            draft_id=existing_draft.id,
            draft_status=existing_draft.status,
        )

    try:
        model_reply = await hermes.generate(
            message.body,
            _product_context(db, tenant_id),
            _conversation_history(db, tenant_id, message.conversation_id, exclude_message_id=message.id),
        )
    except HermesError:
        # Event and redacted inbound message stay committed so the provider can retry safely.
        raise HTTPException(status_code=502, detail="Reply generation is temporarily unavailable") from None

    draft = ReplyDraft(
        tenant_id=tenant_id,
        conversation_id=message.conversation_id,
        message_id=message.id,
        body=redact_pii(model_reply),
        source="webhook",
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
                ReplyDraft.tenant_id == tenant_id,
                ReplyDraft.message_id == message.id,
            )
        )
    return WebhookAccepted(
        event_id=event_key,
        draft_id=draft.id if draft else None,
        draft_status=draft.status if draft else None,
    )


def _scoped_draft(db: Session, draft_id: UUID, user: UserContext) -> ReplyDraft:
    row = db.execute(
        select(ReplyDraft, Conversation.customer_id)
        .join(
            Conversation,
            (Conversation.tenant_id == ReplyDraft.tenant_id)
            & (Conversation.id == ReplyDraft.conversation_id),
        )
        .where(ReplyDraft.id == draft_id, ReplyDraft.tenant_id == user.tenant_id)
    )
    result = row.first()
    if result is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft, customer_id = result
    if not user.can_access_customer(customer_id):
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@app.get("/drafts", response_model=list[DraftOut])
def list_drafts(
    user: Annotated[UserContext, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_user_db)],
    state: str = "pending",
) -> list[ReplyDraft]:
    if state not in {"pending", "approved", "rejected", "all"}:
        raise HTTPException(status_code=422, detail="Invalid draft state")
    query = (
        select(ReplyDraft)
        .join(
            Conversation,
            (Conversation.tenant_id == ReplyDraft.tenant_id)
            & (Conversation.id == ReplyDraft.conversation_id),
        )
        .where(ReplyDraft.tenant_id == user.tenant_id)
        .order_by(ReplyDraft.created_at.desc())
    )
    if state != "all":
        query = query.where(ReplyDraft.status == state)
    if user.role != "tenant_admin":
        query = query.where(Conversation.customer_id.in_(user.customer_ids))
    return list(db.scalars(query.limit(100)))


@app.post("/drafts/{draft_id}/approve", response_model=DraftOut)
def approve_draft(
    draft_id: UUID,
    user: Annotated[UserContext, Depends(require_roles("tenant_admin", "reviewer"))],
    db: Annotated[Session, Depends(get_user_db)],
) -> ReplyDraft:
    draft = _scoped_draft(db, draft_id, user)
    if draft.status != "pending":
        raise HTTPException(status_code=409, detail="Draft is no longer pending")
    result = db.execute(
        update(ReplyDraft)
        .where(
            ReplyDraft.id == draft.id,
            ReplyDraft.tenant_id == user.tenant_id,
            ReplyDraft.status == "pending",
        )
        .values(status="approved", decided_by=user.subject, decided_at=datetime.now(timezone.utc))
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Draft is no longer pending")
    db.commit()
    db.refresh(draft)
    return draft


@app.post("/drafts/{draft_id}/reject", response_model=DraftOut)
def reject_draft(
    draft_id: UUID,
    user: Annotated[UserContext, Depends(require_roles("tenant_admin", "reviewer"))],
    db: Annotated[Session, Depends(get_user_db)],
    note: str = "",
) -> ReplyDraft:
    draft = _scoped_draft(db, draft_id, user)
    if draft.status != "pending":
        raise HTTPException(status_code=409, detail="Draft is no longer pending")
    result = db.execute(
        update(ReplyDraft)
        .where(
            ReplyDraft.id == draft.id,
            ReplyDraft.tenant_id == user.tenant_id,
            ReplyDraft.status == "pending",
        )
        .values(
            status="rejected",
            decided_by=user.subject,
            decision_note=redact_pii(note[:1000]),
            decided_at=datetime.now(timezone.utc),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Draft is no longer pending")
    db.commit()
    db.refresh(draft)
    return draft
