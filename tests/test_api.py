import uuid
import hashlib
import hmac
import json
import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event as sqlalchemy_event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from cryptography.fernet import Fernet

from hermes_secure_sales import api
from hermes_secure_sales.auth import UserContext, get_current_user
from hermes_secure_sales.dependencies import (
    get_hermes_client,
    get_platform_db,
    get_public_session_db,
    get_public_shop_db,
    get_user_db,
    get_webhook_db,
)
from hermes_secure_sales.models import (
    Base,
    Conversation,
    ConsultationRequest,
    GoogleFormSubmission,
    Customer,
    Message,
    Product,
    PublicChatMessage,
    PublicChatSession,
    ReplyDraft,
    Shop,
    Tenant,
)
from hermes_secure_sales import secure_shop_admin
from hermes_secure_sales import hermes as hermes_module
from hermes_secure_sales.hermes import HermesClient
from hermes_secure_sales import pii_crypto
from hermes_secure_sales import google_forms


class FakeHermes:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.products: list[list[dict[str, str]]] = []
        self.histories: list[list[dict[str, str]]] = []

    async def generate(
        self,
        customer_message: str,
        products: list[dict[str, str]],
        history: list[dict[str, str]] | None = None,
    ) -> str:
        self.messages.append(customer_message)
        self.products.append(products)
        self.histories.append(history or [])
        return "Xin chào, email an@example.test, sản phẩm Demo có giá 100 VND."


def test_hermes_client_has_local_mock_mode_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hermes_module,
        "get_settings",
        lambda: type("Settings", (), {"hermes_api_key": ""})(),
    )
    coroutine = HermesClient().generate(
        "Tôi muốn tìm áo",
        [{"sku": "A1", "name": "Áo linen", "description": "Nhẹ mát", "price": "590000.00", "currency": "VND"}],
    )
    with pytest.raises(StopIteration) as result:
        coroutine.send(None)
    assert "Áo linen" in result.value.value
    assert "590000 VND" in result.value.value


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch):
    tenant_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sqlalchemy_event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    with TestSession() as db:
        db.add(Tenant(id=tenant_id, name="Demo tenant"))
        db.commit()
        db.add(
            Shop(
                tenant_id=tenant_id,
                slug="demo-shop",
                display_name="Demo Shop",
                welcome_message="Xin chào, bạn cần tư vấn gì?",
                theme_color="#0b8980",
                allowed_origins='["https://store.example.test"]',
                enabled=True,
            )
        )
        db.commit()
        db.add(
            Product(
                tenant_id=tenant_id,
                sku="DEMO-1",
                name="Demo",
                description="Contact support@example.test",
                price=Decimal("100.00"),
                currency="VND",
            )
        )
        db.commit()

    user_box = {
        "user": UserContext(
            subject="reviewer-1",
            tenant_id=tenant_id,
            role="reviewer",
            customer_ids=frozenset({customer_id}),
        )
    }
    fake_hermes = FakeHermes()

    def auth_override():
        return user_box["user"]

    def user_db_override():
        with TestSession() as db:
            yield db

    def webhook_db_override(tenant_id: uuid.UUID):
        with TestSession() as db:
            yield db

    def platform_db_override():
        with TestSession() as db:
            yield db

    def public_shop_db_override(slug: str):
        with TestSession() as db:
            yield db

    def public_session_db_override(authorization: str | None = None):
        with TestSession() as db:
            yield db

    api.app.dependency_overrides[get_current_user] = auth_override
    api.app.dependency_overrides[get_user_db] = user_db_override
    api.app.dependency_overrides[get_webhook_db] = webhook_db_override
    api.app.dependency_overrides[get_platform_db] = platform_db_override
    api.app.dependency_overrides[get_public_shop_db] = public_shop_db_override
    api.app.dependency_overrides[get_public_session_db] = public_session_db_override
    api.app.dependency_overrides[get_hermes_client] = lambda: fake_hermes
    monkeypatch.setattr(api, "verify_webhook", lambda *args: None)
    monkeypatch.setattr(api, "webhook_event_key", lambda _tenant_id, event_id: hashlib.sha256(event_id.encode()).hexdigest())

    with TestClient(api.app) as client:
        yield client, TestSession, tenant_id, customer_id, user_box, fake_hermes
    api.app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_webhook_redacts_before_model_persists_safe_draft_and_is_idempotent(hub) -> None:
    client, TestSession, tenant_id, customer_id, _users, hermes = hub
    conversation_id = uuid.uuid4()
    payload = {
        "event_id": "provider-event-001",
        "conversation_id": str(conversation_id),
        "customer_id": str(customer_id),
        "message": "Please contact me at person@example.test or 0901234567 about Demo.",
    }
    url = f"/webhooks/{tenant_id}/test-channel/messages"

    response = client.post(url, json=payload)
    assert response.status_code == 202
    result = response.json()
    assert result["draft_status"] == "pending"
    assert "person@example.test" not in hermes.messages[0]
    assert "0901234567" not in hermes.messages[0]
    assert "support@example.test" not in hermes.products[0][0]["description"]

    with TestSession() as db:
        stored_message = db.scalar(select(Message).where(Message.conversation_id == conversation_id))
        draft = db.get(ReplyDraft, uuid.UUID(result["draft_id"]))
        assert "person@example.test" not in stored_message.body
        assert "0901234567" not in stored_message.body
        assert "an@example.test" not in draft.body
        assert draft.status == "pending"
        assert draft.source == "webhook"

    repeated = client.post(url, json=payload)
    assert repeated.status_code == 202
    assert repeated.json()["draft_id"] == result["draft_id"]
    assert len(hermes.messages) == 1


def test_chatbot_page_is_available_without_external_assets(hub) -> None:
    client, *_ = hub
    response = client.get("/")
    assert response.status_code == 200
    assert "Tạo trang chat" in response.text
    internal = client.get("/internal")
    assert internal.status_code == 200
    assert 'lang="vi"' in internal.text
    assert "/static/app.js" in internal.text
    assert "fonts.googleapis.com" not in internal.text
    assert client.get("/static/app.js").status_code == 200


def test_public_chat_waits_for_shop_approval_and_redacts_pii(hub, monkeypatch: pytest.MonkeyPatch) -> None:
    client, TestSession, tenant_id, _customer_id, _users, hermes = hub
    assert client.get("/chat/demo-shop").status_code == 200
    chat_page = client.get("/chat/demo-shop")
    assert "/static/public-chat.js" in chat_page.text
    chat_script = client.get("/static/public-chat.js").text
    assert "item.role === 'assistant'" in chat_script
    assert "awaitingApproval" in chat_script
    assert client.get("/embed-demo").status_code == 200
    assert client.get("/embed/demo-shop", headers={"Referer": "http://testserver/embed-demo"}).status_code == 200

    info = client.get("/public/shops/demo-shop")
    assert info.status_code == 200
    assert info.json()["name"] == "Demo Shop"
    assert len(info.json()["products"]) == 1
    assert "tenant_id" not in info.json()

    created = client.post("/public/shops/demo-shop/sessions")
    assert created.status_code == 201
    token = created.json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/public/sessions/me", headers=headers).json() == {"messages": []}
    response = client.post(
        "/public/sessions/messages",
        headers=headers,
        json={
            "request_id": str(uuid.uuid4()),
            "message": "Tôi tên là Nguyen Van An, email person@example.test, gọi 0901234567.",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert "reply" not in response.json()
    assert response.json()["mode"] == "mock"
    assert "person@example.test" not in hermes.messages[0]
    assert "0901234567" not in hermes.messages[0]
    history = client.get("/public/sessions/me", headers=headers).json()["messages"]
    assert [item["role"] for item in history] == ["user"]
    assert history[0]["status"] == "pending"
    assert "an@example.test" not in str(history)

    second_request = str(uuid.uuid4())
    blocked = client.post(
        "/public/sessions/messages",
        headers=headers,
        json={"request_id": second_request, "message": "Còn sản phẩm nào phù hợp?"},
    )
    assert blocked.status_code == 409
    assert len(hermes.messages) == 1

    with TestSession() as db:
        session = db.scalar(select(PublicChatSession))
        records = list(db.scalars(select(PublicChatMessage).where(PublicChatMessage.session_id == session.id)))
        assert session.tenant_id == tenant_id
        assert session.token_hash != token
        assert len(records) == 1
        assert {row.direction for row in records} == {"user"}
        assert all("person@example.test" not in row.body and "0901234567" not in row.body for row in records)
        assert session.conversation_id is not None
        crm_message = db.get(Message, records[0].crm_message_id)
        assert crm_message is not None
        assert crm_message.direction == "inbound"
        assert "person@example.test" not in crm_message.body
        assert "0901234567" not in crm_message.body
        draft = db.scalar(select(ReplyDraft).where(ReplyDraft.source == "public_chat"))
        assert draft is not None
        assert draft.status == "pending"
        assert "an@example.test" not in draft.body
        shop_id = db.scalar(select(Shop.id).where(Shop.slug == "demo-shop"))

    monkeypatch.setattr(
        secure_shop_admin,
        "get_settings",
        lambda: type("Settings", (), {
            "admin_secret": "a" * 48,
            "jwt_secret": "",
            "admin_username": "owner",
            "admin_password": "local-password-for-test",
            "staff_username": "",
            "staff_password": "",
            "app_env": "development",
            "public_base_url": "http://chat.example.test",
            "public_session_hours": 8,
            "public_rate_limit_per_minute": 30,
            "hermes_api_key": "",
        })(),
    )
    blocked_origin = client.post(
        "/admin/api/login",
        headers={"Origin": "http://localhost:8766"},
        json={"username": "owner", "password": "local-password-for-test"},
    )
    assert blocked_origin.status_code == 403
    login = client.post(
        "/admin/api/login",
        headers={"Origin": "http://chat.example.test"},
        json={"username": "owner", "password": "local-password-for-test"},
    )
    assert login.status_code == 200
    dev_origin_login = client.post(
        "/admin/api/login",
        headers={"Origin": "http://testserver"},
        json={"username": "owner", "password": "local-password-for-test"},
    )
    assert dev_origin_login.status_code == 200
    other_shop = client.post(
        "/admin/api/shops",
        json={"name": "Shop tổng hợp khác", "slug": "other-demo-shop", "allowed_origins": []},
    )
    assert other_shop.status_code == 201
    other_shop_id = other_shop.json()["id"]
    assert client.get(f"/admin/api/shops/{other_shop_id}/drafts").json() == []

    drafts = client.get(f"/admin/api/shops/{shop_id}/drafts")
    assert drafts.status_code == 200
    assert len(drafts.json()) == 1
    draft_id = drafts.json()[0]["id"]
    assert "person@example.test" not in drafts.json()[0]["customer_message"]
    cross_shop_approval = client.post(f"/admin/api/shops/{other_shop_id}/drafts/{draft_id}/approve")
    assert cross_shop_approval.status_code == 404
    approved = client.post(f"/admin/api/shops/{shop_id}/drafts/{draft_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    history = client.get("/public/sessions/me", headers=headers).json()["messages"]
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[0]["status"] == "approved"
    assert history[1]["status"] == "approved"
    assert "an@example.test" not in str(history)

    second = client.post(
        "/public/sessions/messages",
        headers=headers,
        json={"request_id": second_request, "message": "Còn sản phẩm nào phù hợp?"},
    )
    assert second.status_code == 200
    assert second.json()["status"] == "pending"
    assert {item["role"] for item in hermes.histories[1]} == {"user", "assistant"}
    assert hermes.histories[1][-1]["role"] == "assistant"
    pending = client.get(f"/admin/api/shops/{shop_id}/drafts").json()
    assert len(pending) == 1
    rejected = client.post(f"/admin/api/shops/{shop_id}/drafts/{pending[0]['id']}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    history = client.get("/public/sessions/me", headers=headers).json()["messages"]
    assert [item["role"] for item in history] == ["user", "assistant", "user"]
    assert history[-1]["status"] == "rejected"
    assert client.get(f"/admin/api/shops/{shop_id}/drafts").json() == []
    assert client.get("/public/sessions/me").status_code == 401


def test_staff_inbox_is_shop_scoped_redacts_staff_reply_and_shows_it_to_customer(hub, monkeypatch: pytest.MonkeyPatch) -> None:
    client, TestSession, _tenant_id, _customer_id, _users, _hermes = hub
    assert client.get("/admin/api/shops").status_code == 401

    session_response = client.post("/public/shops/demo-shop/sessions")
    assert session_response.status_code == 201
    token = session_response.json()["session_token"]
    visitor_headers = {"Authorization": f"Bearer {token}"}
    request_id = str(uuid.uuid4())
    inbound = client.post(
        "/public/sessions/messages",
        headers=visitor_headers,
        json={"request_id": request_id, "message": "Tôi tên là Nguyen Van An, email person@example.test, gọi 0901234567."},
    )
    assert inbound.status_code == 200

    monkeypatch.setattr(
        secure_shop_admin,
        "get_settings",
        lambda: type("Settings", (), {
            "admin_secret": "a" * 48,
            "jwt_secret": "",
            "admin_username": "owner",
            "admin_password": "local-password-for-test",
            "staff_username": "",
            "staff_password": "",
            "app_env": "development",
            "public_base_url": "http://chat.example.test",
            "public_session_hours": 8,
            "public_rate_limit_per_minute": 30,
            "hermes_api_key": "",
        })(),
    )
    login = client.post("/admin/api/login", json={"username": "owner", "password": "local-password-for-test"})
    assert login.status_code == 200

    with TestSession() as db:
        session = db.scalar(select(PublicChatSession))
        conversation_id = str(session.conversation_id)
        shop_id = str(db.scalar(select(Shop.id).where(Shop.slug == "demo-shop")))
    empty_session = client.post("/public/shops/demo-shop/sessions")
    assert empty_session.status_code == 201
    inbox = client.get(f"/admin/api/shops/{shop_id}/conversations")
    assert inbox.status_code == 200
    assert len(inbox.json()) == 1
    assert inbox.json()[0]["has_pending_draft"] is True

    other_shop = client.post(
        "/admin/api/shops",
        json={"name": "Shop khác", "slug": "shop-khac", "allowed_origins": []},
    )
    assert other_shop.status_code == 201
    other_shop_id = other_shop.json()["id"]
    assert client.get(f"/admin/api/shops/{other_shop_id}/conversations").json() == []
    assert client.get(f"/admin/api/shops/{other_shop_id}/conversations/{conversation_id}").status_code == 404
    assert client.post(
        f"/admin/api/shops/{other_shop_id}/conversations/{conversation_id}/messages",
        json={"message": "Không được gửi sang shop khác"},
    ).status_code == 404

    transcript = client.get(f"/admin/api/shops/{shop_id}/conversations/{conversation_id}")
    assert transcript.status_code == 200
    assert transcript.json()["started_at"]
    customer_turn = transcript.json()["messages"][0]
    assert customer_turn["role"] == "user"
    assert customer_turn["draft"]["status"] == "pending"
    assert "person@example.test" not in str(transcript.json())
    assert "0901234567" not in str(transcript.json())

    sent = client.post(
        f"/admin/api/shops/{shop_id}/conversations/{conversation_id}/messages",
        json={"message": "Nhân viên sẽ gọi lại tại staff@example.test hoặc 0912345678."},
    )
    assert sent.status_code == 201, sent.text
    assert sent.json()["status"] == "sent"
    assert "staff@example.test" not in sent.json()["body"]
    assert "0912345678" not in sent.json()["body"]
    staff_transcript = client.get(f"/admin/api/shops/{shop_id}/conversations/{conversation_id}").json()
    assert staff_transcript["messages"][-1]["author"] == "Nhân viên · owner"

    history = client.get("/public/sessions/me", headers=visitor_headers).json()["messages"]
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[-1]["author"] == "Nhân viên"
    assert "staff@example.test" not in str(history)
    assert "0912345678" not in str(history)

    with TestSession() as db:
        pending_draft = db.scalar(select(ReplyDraft).where(ReplyDraft.source == "public_chat"))
        assert pending_draft.status == "rejected"
        assert pending_draft.decision_note == "Được thay bằng tin nhắn do nhân viên gửi"
        outbound = db.scalar(select(Message).where(Message.direction == "outbound"))
        assert outbound is not None
        assert outbound.author == "owner"
        assert "staff@example.test" not in outbound.body
        assert "0912345678" not in outbound.body


def test_consultation_form_encrypts_pii_stays_in_shop_and_never_calls_hermes(hub, monkeypatch: pytest.MonkeyPatch) -> None:
    client, TestSession, _tenant_id, _customer_id, _users, hermes = hub
    encryption_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(
        pii_crypto,
        "get_settings",
        lambda: type("Settings", (), {"pii_encryption_key": encryption_key})(),
    )

    created = client.post("/public/shops/demo-shop/sessions")
    assert created.status_code == 201
    headers = {"Authorization": f"Bearer {created.json()['session_token']}"}
    assert client.get("/public/sessions/consultation", headers=headers).json() == {"submitted": False}
    submission = {
        "name": "Khách Tổng hợp",
        "phone": "0901 234 567",
        "desired_time": "Chiều mai, khoảng 14:00",
        "note": "Cần tư vấn triệu chứng da khô.",
        "consent": True,
    }
    saved = client.post("/public/sessions/consultation", headers=headers, json=submission)
    assert saved.status_code == 201
    assert saved.json() == {"status": "submitted"}
    repeated = client.post("/public/sessions/consultation", headers=headers, json=submission)
    assert repeated.status_code == 201
    assert repeated.json() == {"status": "already_submitted"}
    assert client.get("/public/sessions/consultation", headers=headers).json() == {"submitted": True}
    assert hermes.messages == []

    with TestSession() as db:
        row = db.scalar(select(ConsultationRequest))
        assert row is not None
        assert "0901 234 567" not in row.phone_ciphertext
        assert "Khách Tổng hợp" not in row.name_ciphertext
        shop_id = db.scalar(select(Shop.id).where(Shop.slug == "demo-shop"))

    settings = type("Settings", (), {
        "admin_secret": "a" * 48,
        "jwt_secret": "",
        "admin_username": "owner",
        "admin_password": "local-password-for-test",
        "staff_username": "agent",
        "staff_password": "staff-password-for-test",
        "app_env": "development",
        "public_base_url": "http://chat.example.test",
        "public_session_hours": 8,
        "public_rate_limit_per_minute": 30,
        "hermes_api_key": "",
    })
    monkeypatch.setattr(secure_shop_admin, "get_settings", lambda: settings)
    assert client.get(f"/admin/api/shops/{shop_id}/consultation-requests").status_code == 401
    assert client.post("/admin/api/login", json={"username": "owner", "password": "local-password-for-test"}).status_code == 200
    other_shop = client.post(
        "/admin/api/shops",
        json={"name": "Shop tổng hợp khác", "slug": "consultation-other-shop", "allowed_origins": []},
    )
    assert other_shop.status_code == 201
    other_shop_id = other_shop.json()["id"]

    staff_login = client.post("/admin/api/login", json={"username": "agent", "password": "staff-password-for-test"})
    assert staff_login.status_code == 200
    requests = client.get(f"/admin/api/shops/{shop_id}/consultation-requests")
    assert requests.status_code == 200
    assert requests.json()[0]["name"] == "Khách Tổng hợp"
    assert requests.json()[0]["phone"] == "0901 234 567"
    assert requests.json()[0]["desired_time"] == "Chiều mai, khoảng 14:00"
    assert requests.json()[0]["note"] == "Cần tư vấn triệu chứng da khô."
    assert client.get(f"/admin/api/shops/{other_shop_id}/consultation-requests").json() == []


def test_google_forms_bridge_requires_signature_and_consent_encrypts_and_deduplicates(hub, monkeypatch: pytest.MonkeyPatch) -> None:
    client, TestSession, tenant_id, _customer_id, _users, hermes = hub
    form_id = "1FAIpQLS-example-form"
    secret = "forms-webhook-test-secret-" + ("x" * 40)
    with TestSession() as db:
        shop_id = db.scalar(select(Shop.id).where(Shop.slug == "demo-shop"))

    monkeypatch.setattr(
        google_forms,
        "get_settings",
        lambda: type("Settings", (), {
            "google_forms_webhook_configs_json": json.dumps({
                "demo-shop": {"form_id": form_id, "secret": secret}
            })
        })(),
    )
    encryption_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(
        pii_crypto,
        "get_settings",
        lambda: type("Settings", (), {"pii_encryption_key": encryption_key})(),
    )

    payload = {
        "response_id": "google-response-001",
        "form_id": form_id,
        "submitted_at": "2026-10-14T08:30:00.000Z",
        "name": "Khách Tổng hợp",
        "phone": "0901 234 567",
        "desired_time": "Chiều mai, khoảng 14:00",
        "note": "Ghi chú thử nghiệm nhạy cảm",
        "consent": True,
    }

    def signed_request(body_payload: dict, supplied_signature: str | None = None):
        body = json.dumps(body_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        return client.post(
            "/integrations/google-forms/demo-shop",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hermes-Timestamp": timestamp,
                "X-Hermes-Signature": supplied_signature or "sha256=" + digest,
            },
        )

    assert signed_request(payload, "sha256=" + ("0" * 64)).status_code == 401
    without_consent = {**payload, "response_id": "google-response-no-consent", "consent": False}
    assert signed_request(without_consent).status_code == 422

    imported = signed_request(payload)
    assert imported.status_code == 202
    assert imported.json() == {"status": "imported"}
    assert signed_request(payload).json() == {"status": "already_imported"}
    assert hermes.messages == []

    with TestSession() as db:
        rows = list(db.scalars(select(GoogleFormSubmission)))
        assert len(rows) == 1
        row = rows[0]
        assert row.tenant_id == tenant_id
        assert row.shop_id == shop_id
        assert row.response_id == "google-response-001"
        assert "Khách Tổng hợp" not in row.name_ciphertext
        assert "0901 234 567" not in row.phone_ciphertext
        assert "Ghi chú thử nghiệm nhạy cảm" not in row.note_ciphertext

    settings = type("Settings", (), {
        "admin_secret": "a" * 48,
        "jwt_secret": "",
        "admin_username": "owner",
        "admin_password": "local-password-for-test",
        "staff_username": "agent",
        "staff_password": "staff-password-for-test",
        "app_env": "development",
        "public_base_url": "http://chat.example.test",
        "public_session_hours": 8,
        "public_rate_limit_per_minute": 30,
        "hermes_api_key": "",
    })
    monkeypatch.setattr(secure_shop_admin, "get_settings", lambda: settings)
    assert client.post("/admin/api/login", json={"username": "agent", "password": "staff-password-for-test"}).status_code == 200
    listed = client.get(f"/admin/api/shops/{shop_id}/consultation-requests")
    assert listed.status_code == 200
    assert listed.json()[0]["source"] == "google_forms"
    assert listed.json()[0]["name"] == "Khách Tổng hợp"
    assert listed.json()[0]["phone"] == "0901 234 567"
    assert listed.json()[0]["note"] == "Ghi chú thử nghiệm nhạy cảm"

def test_public_embed_checks_origin_and_session_remains_bound_to_it(hub) -> None:
    client, _TestSession, _tenant_id, _customer_id, _users, _hermes = hub
    denied_page = client.get("/embed/demo-shop", headers={"Referer": "https://evil.example/"})
    assert denied_page.status_code == 403
    denied_session = client.post(
        "/public/shops/demo-shop/sessions",
        headers={"X-Embed-Origin": "https://evil.example"},
    )
    assert denied_session.status_code == 403

    allowed = client.post(
        "/public/shops/demo-shop/sessions",
        headers={"X-Embed-Origin": "https://store.example.test"},
    )
    assert allowed.status_code == 201
    token = allowed.json()["session_token"]
    assert client.get("/public/sessions/me", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert client.get(
        "/public/sessions/me",
        headers={"Authorization": f"Bearer {token}", "X-Embed-Origin": "https://store.example.test"},
    ).status_code == 200


def test_admin_cookie_protects_dashboard_data_and_can_create_a_shop(hub, monkeypatch: pytest.MonkeyPatch) -> None:
    client, TestSession, _tenant_id, _customer_id, _users, _hermes = hub
    monkeypatch.setattr(
        secure_shop_admin,
        "get_settings",
        lambda: type("Settings", (), {
            "admin_secret": "a" * 48,
            "jwt_secret": "",
            "admin_username": "owner",
            "admin_password": "local-password-for-test",
            "staff_username": "",
            "staff_password": "",
            "app_env": "development",
            "public_base_url": "http://chat.example.test",
            "public_session_hours": 8,
            "public_rate_limit_per_minute": 30,
            "hermes_api_key": "",
        })(),
    )
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/api/shops").status_code == 401
    login = client.post("/admin/api/login", json={"username": "owner", "password": "local-password-for-test"})
    assert login.status_code == 200
    assert "httponly" in login.headers["set-cookie"].lower()
    created = client.post(
        "/admin/api/shops",
        json={"name": "Cửa hàng mới", "slug": "cua-hang-moi", "allowed_origins": ["https://store.example.test"]},
    )
    assert created.status_code == 201, created.text
    shop_id = created.json()["id"]
    shops = client.get("/admin/api/shops")
    assert shops.status_code == 200
    new_shop = next(shop for shop in shops.json() if shop["id"] == shop_id)
    assert new_shop["chat_url"] == "http://chat.example.test/chat/cua-hang-moi"
    assert len(new_shop["products"]) == 2
    public_info = client.get("/public/shops/cua-hang-moi")
    assert public_info.status_code == 200
    assert public_info.json()["theme_color"] == "#0b8980"

    product = new_shop["products"][0]
    updated_product = client.patch(
        f"/admin/api/shops/{shop_id}/products/{product['id']}",
        json={"name": "Áo mẫu đã sửa", "active": False},
    )
    assert updated_product.status_code == 200
    assert updated_product.json()["name"] == "Áo mẫu đã sửa"
    assert updated_product.json()["active"] is False

    updated_shop = client.put(
        f"/admin/api/shops/{shop_id}",
        json={
            "name": "Cửa hàng mới",
            "welcome_message": "Chào bạn!",
            "theme_color": "#2255aa",
            "allowed_origins": ["https://store.example.test"],
            "enabled": False,
        },
    )
    assert updated_shop.status_code == 200
    assert client.get("/public/shops/cua-hang-moi").status_code == 404


def test_reviewer_cannot_approve_draft_for_out_of_scope_customer(hub) -> None:
    client, TestSession, tenant_id, customer_id, users, _hermes = hub
    other_customer = uuid.uuid4()
    conversation_id = uuid.uuid4()
    with TestSession() as db:
        db.add(Customer(id=other_customer, tenant_id=tenant_id))
        db.flush()
        db.add(Conversation(id=conversation_id, tenant_id=tenant_id, customer_id=other_customer))
        message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            direction="inbound",
            body="Safe test message",
        )
        db.add(message)
        db.flush()
        draft = ReplyDraft(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message_id=message.id,
            body="Pending test reply",
        )
        db.add(draft)
        db.commit()
        draft_id = draft.id

    denied = client.post(f"/drafts/{draft_id}/approve")
    assert denied.status_code == 404
    with TestSession() as db:
        assert db.get(ReplyDraft, draft_id).status == "pending"

    users["user"] = UserContext(
        subject="reviewer-1",
        tenant_id=tenant_id,
        role="reviewer",
        customer_ids=frozenset({customer_id, other_customer}),
    )
    approved = client.post(f"/drafts/{draft_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    # Approval records the decision only; no outbound channel client exists.


def test_chatbot_redacts_pii_requires_review_and_uses_only_approved_history(hub) -> None:
    client, TestSession, tenant_id, customer_id, users, hermes = hub
    users["user"] = UserContext(
        subject="sales-1",
        tenant_id=tenant_id,
        role="sales_agent",
        customer_ids=frozenset({customer_id}),
    )
    request_id = uuid.uuid4()
    payload = {
        "request_id": str(request_id),
        "customer_id": str(customer_id),
        "message": "Tôi tên là Nguyen Van A, email person@example.test, gọi 0901234567.",
    }
    response = client.post("/chat/messages", json=payload)
    assert response.status_code == 202
    first = response.json()
    conversation_id = first["conversation_id"]
    draft_id = first["draft"]["id"]
    assert first["draft"]["status"] == "pending"
    assert "person@example.test" not in hermes.messages[0]
    assert "0901234567" not in hermes.messages[0]
    assert "Nguyen Van A" not in hermes.messages[0]
    assert hermes.histories[0] == []

    retry = client.post("/chat/messages", json=payload)
    assert retry.status_code == 202
    assert retry.json()["draft"]["id"] == draft_id
    assert len(hermes.messages) == 1

    blocked = client.post(
        "/chat/messages",
        json={
            "request_id": str(uuid.uuid4()),
            "customer_id": str(customer_id),
            "conversation_id": conversation_id,
            "message": "Tôi muốn biết thêm về sản phẩm Demo.",
        },
    )
    assert blocked.status_code == 409
    assert len(hermes.messages) == 1

    users["user"] = UserContext(
        subject="reviewer-1",
        tenant_id=tenant_id,
        role="reviewer",
        customer_ids=frozenset({customer_id}),
    )
    approved = client.post(f"/drafts/{draft_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    users["user"] = UserContext(
        subject="sales-1",
        tenant_id=tenant_id,
        role="sales_agent",
        customer_ids=frozenset({customer_id}),
    )
    second = client.post(
        "/chat/messages",
        json={
            "request_id": str(uuid.uuid4()),
            "customer_id": str(customer_id),
            "conversation_id": conversation_id,
            "message": "Còn thông tin nào khác?",
        },
    )
    assert second.status_code == 202
    assert hermes.histories[1] == [
        {"role": "user", "content": "Tôi tên là [NAME_1], email [EMAIL_1], gọi [ID_1]."},
        {"role": "assistant", "content": "Xin chào, email [EMAIL_1], sản phẩm Demo có giá 100 VND."},
    ]
    assert "person@example.test" not in str(hermes.histories[1])
    with TestSession() as db:
        messages = list(db.scalars(select(Message).where(Message.conversation_id == uuid.UUID(conversation_id))))
        assert all("person@example.test" not in message.body for message in messages)


def test_chatbot_enforces_customer_scope_and_role(hub) -> None:
    client, _TestSession, tenant_id, customer_id, users, _hermes = hub
    payload = {
        "request_id": str(uuid.uuid4()),
        "customer_id": str(customer_id),
        "message": "Câu hỏi dữ liệu giả.",
    }
    denied_role = client.post("/chat/messages", json=payload)
    assert denied_role.status_code == 403

    users["user"] = UserContext(
        subject="sales-1",
        tenant_id=tenant_id,
        role="sales_agent",
        customer_ids=frozenset(),
    )
    denied_scope = client.post("/chat/messages", json=payload)
    assert denied_scope.status_code == 404


def test_chat_history_is_tenant_scoped_even_for_admin(hub) -> None:
    client, TestSession, tenant_id, _customer_id, users, _hermes = hub
    other_tenant = uuid.uuid4()
    other_customer = uuid.uuid4()
    conversation_id = uuid.uuid4()
    with TestSession() as db:
        db.add(Tenant(id=other_tenant, name="Other tenant"))
        db.commit()
        db.add(Customer(id=other_customer, tenant_id=other_tenant))
        db.flush()
        db.add(
            Conversation(
                id=conversation_id,
                tenant_id=other_tenant,
                customer_id=other_customer,
            )
        )
        db.commit()

    users["user"] = UserContext(
        subject="admin-1",
        tenant_id=tenant_id,
        role="tenant_admin",
        customer_ids=frozenset(),
    )
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 404


def test_database_allows_only_one_pending_chat_draft_per_conversation(hub) -> None:
    _client, TestSession, tenant_id, customer_id, _users, _hermes = hub
    conversation_id = uuid.uuid4()
    with TestSession() as db:
        db.add(Customer(id=customer_id, tenant_id=tenant_id))
        db.flush()
        conversation = Conversation(
            id=conversation_id,
            tenant_id=tenant_id,
            customer_id=customer_id,
        )
        db.add(conversation)
        db.flush()
        first_message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            direction="inbound",
            body="Synthetic question one",
        )
        second_message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            direction="inbound",
            body="Synthetic question two",
        )
        db.add_all([first_message, second_message])
        db.flush()
        db.add(
            ReplyDraft(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                message_id=first_message.id,
                body="Synthetic reply one",
                source="chat",
            )
        )
        db.commit()
        db.add(
            ReplyDraft(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                message_id=second_message.id,
                body="Synthetic reply two",
                source="chat",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        drafts = list(
            db.scalars(
                select(ReplyDraft).where(
                    ReplyDraft.tenant_id == tenant_id,
                    ReplyDraft.conversation_id == conversation_id,
                    ReplyDraft.status == "pending",
                    ReplyDraft.source == "chat",
                )
            )
        )
        assert len(drafts) == 1


def test_webhook_events_keep_independent_pending_drafts(hub) -> None:
    client, TestSession, tenant_id, customer_id, _users, _hermes = hub
    conversation_id = uuid.uuid4()
    payload = {
        "event_id": "provider-event-one",
        "conversation_id": str(conversation_id),
        "customer_id": str(customer_id),
        "message": "Synthetic question one",
    }
    url = f"/webhooks/{tenant_id}/test-channel/messages"
    first = client.post(url, json=payload)
    assert first.status_code == 202

    payload["event_id"] = "provider-event-two"
    payload["message"] = "Synthetic question two"
    second = client.post(url, json=payload)
    assert second.status_code == 202
    assert second.json()["draft_status"] == "pending"
    assert second.json()["draft_id"] != first.json()["draft_id"]

    with TestSession() as db:
        drafts = list(db.scalars(select(ReplyDraft).where(ReplyDraft.conversation_id == conversation_id)))
        assert len(drafts) == 2
        assert {draft.source for draft in drafts} == {"webhook"}

