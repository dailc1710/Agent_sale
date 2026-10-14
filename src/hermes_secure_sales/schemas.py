from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="VND", min_length=3, max_length=3, pattern="^[A-Z]{3}$")


class ProductOut(ProductCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    active: bool


class WebhookMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=160)
    conversation_id: UUID
    customer_id: UUID
    message: str = Field(min_length=1, max_length=12000)


class DraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    message_id: UUID
    body: str
    source: str
    status: str
    created_at: datetime
    decided_by: str | None
    decision_note: str | None
    decided_at: datetime | None


class ChatMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    customer_id: UUID
    conversation_id: UUID | None = None
    message: str = Field(min_length=1, max_length=12000)


class ChatMessageAccepted(BaseModel):
    conversation_id: UUID
    message_id: UUID
    draft: DraftOut


class ChatTurnOut(BaseModel):
    role: str
    body: str
    draft_id: UUID | None = None
    draft_status: str | None = None


class ChatHistoryOut(BaseModel):
    conversation_id: UUID
    customer_id: UUID
    turns: list[ChatTurnOut]


class WebhookAccepted(BaseModel):
    status: str = "accepted"
    event_id: str
    draft_id: UUID | None = None
    draft_status: str | None = None


class AdminLoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class AdminShopCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=2, max_length=80, pattern="^[a-z0-9]+(?:-[a-z0-9]+)*$")
    welcome_message: str = Field(
        default="Xin chào! Tôi có thể chia sẻ thông tin dịch vụ đã được cơ sở cập nhật và hướng dẫn đặt lịch.",
        max_length=500,
    )
    theme_color: str = Field(default="#159fd1", pattern="^#[0-9a-fA-F]{6}$")
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)


class AdminShopUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    welcome_message: str = Field(max_length=500)
    theme_color: str = Field(pattern="^#[0-9a-fA-F]{6}$")
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool


class AdminProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="VND", min_length=3, max_length=3, pattern="^[A-Z]{3}$")


class AdminProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3, pattern="^[A-Z]{3}$")
    active: bool | None = None


class PublicSessionCreate(BaseModel):
    shop_slug: str = Field(min_length=2, max_length=80)


class PublicMessageIn(BaseModel):
    request_id: UUID
    message: str = Field(min_length=1, max_length=3000)


class ConsultationRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=8, max_length=32, pattern=r"^[0-9+() .-]+$")
    desired_time: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=1200)
    consent: bool

    @field_validator("name", "phone", "desired_time", "note")
    @classmethod
    def trim_form_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("phone")
    @classmethod
    def require_phone_digits(cls, value: str) -> str:
        if sum(character.isdigit() for character in value) < 8:
            raise ValueError("Số điện thoại chưa hợp lệ")
        return value


class GoogleFormSubmissionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_id: str = Field(min_length=1, max_length=256)
    form_id: str = Field(min_length=1, max_length=256)
    submitted_at: datetime
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=8, max_length=32, pattern=r"^[0-9+() .-]+$")
    desired_time: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=1200)
    consent: bool

    @field_validator("response_id", "form_id", "name", "phone", "desired_time", "note")
    @classmethod
    def trim_form_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("phone")
    @classmethod
    def require_phone_digits(cls, value: str) -> str:
        if sum(character.isdigit() for character in value) < 8:
            raise ValueError("Số điện thoại chưa hợp lệ")
        return value

    @field_validator("submitted_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Thời gian gửi cần kèm múi giờ")
        return value


class AdminStaffMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=3000)

