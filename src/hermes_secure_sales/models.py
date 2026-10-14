import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import Uuid


class Base(DeclarativeBase):
    pass


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class Shop(Base):
    __tablename__ = "shops"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_shops_tenant_id_unique"),
        UniqueConstraint("tenant_id", "id", name="uq_shops_tenant_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    welcome_message: Mapped[str] = mapped_column(
        Text,
        default="Xin chào! Tôi có thể chia sẻ thông tin dịch vụ đã được cơ sở cập nhật và hướng dẫn đặt lịch.",
        nullable=False,
    )
    theme_color: Mapped[str] = mapped_column(String(7), default="#159fd1", nullable=False)
    allowed_origins: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class PublicChatSession(Base):
    __tablename__ = "public_chat_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_public_chat_sessions_tenant_id"),
        UniqueConstraint("tenant_id", "conversation_id", name="uq_public_sessions_tenant_conversation"),
        ForeignKeyConstraint(["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    embed_origin: Mapped[str | None] = mapped_column(String(500), nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class PublicChatMessage(Base):
    __tablename__ = "public_chat_messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "session_id", "request_id", "direction", name="uq_public_message_request_direction"),
        ForeignKeyConstraint(["tenant_id", "session_id"], ["public_chat_sessions.tenant_id", "public_chat_sessions.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "crm_message_id"],
            ["messages.tenant_id", "messages.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("direction IN ('user', 'assistant')", name="ck_public_message_direction"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    crm_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class ConsultationRequest(Base):
    """Customer contact details kept separate from chat content and model context."""

    __tablename__ = "consultation_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "session_id", name="uq_consultation_tenant_session"),
        Index("ix_consultation_requests_tenant_shop_created", "tenant_id", "shop_id", "created_at"),
        ForeignKeyConstraint(
            ["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["public_chat_sessions.tenant_id", "public_chat_sessions.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    phone_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    desired_time_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    note_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class GoogleFormSubmission(Base):
    """Encrypted copy of a consented Google Forms response, deduplicated by response ID."""

    __tablename__ = "google_form_submissions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "response_id", name="uq_google_form_tenant_response"),
        Index("ix_google_form_submissions_tenant_shop_submitted", "tenant_id", "shop_id", "submitted_at"),
        ForeignKeyConstraint(
            ["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"], ondelete="CASCADE"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    response_id: Mapped[str] = mapped_column(String(256), nullable=False)
    name_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    phone_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    desired_time_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    note_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
        UniqueConstraint("tenant_id", "id", name="uq_products_tenant_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="VND", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_customers_tenant_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_conversations_tenant_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_messages_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class ReplyDraft(Base):
    __tablename__ = "reply_drafts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "message_id", name="uq_drafts_tenant_message"),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "message_id"],
            ["messages.tenant_id", "messages.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_drafts_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(20), default="webhook", server_default="webhook", nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index(
    "uq_reply_drafts_one_pending_per_conversation",
    ReplyDraft.tenant_id,
    ReplyDraft.conversation_id,
    unique=True,
    postgresql_where=(ReplyDraft.status == "pending") & (ReplyDraft.source == "chat"),
    sqlite_where=(ReplyDraft.status == "pending") & (ReplyDraft.source == "chat"),
)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    external_event_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

