import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from hermes_secure_sales import security
from hermes_secure_sales.privacy import redact_pii


def test_redacts_contact_payment_identity_and_labeled_fields() -> None:
    source = (
        "Tôi tên là Nguyen Van An, email an@example.test, số 0901234567, "
        "thẻ 4111 1111 1111 1111; địa chỉ: 12 Main Street, Hanoi"
    )
    result = redact_pii(source)

    for raw_value in (
        "Nguyen Van An",
        "an@example.test",
        "0901234567",
        "4111 1111 1111 1111",
        "12 Main Street",
    ):
        assert raw_value not in result
    assert "[EMAIL_1]" in result
    assert "[PHONE_1]" in result or "[ID_1]" in result
    assert "[CARD_1]" in result
    assert "[NAME_1]" in result
    assert "[ADDRESS_1]" in result


def test_webhook_signature_is_bound_to_timestamp_and_exact_body(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    secret = "s" * 48
    timestamp = str(int(time.time()))
    body = b'{"event_id":"demo-1"}'
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    monkeypatch.setattr(security, "get_settings", lambda: SimpleNamespace(
        webhook_secrets_json=json.dumps({str(tenant_id): secret})
    ))

    security.verify_webhook(tenant_id, timestamp, signature, body)
    with pytest.raises(HTTPException) as error:
        security.verify_webhook(tenant_id, timestamp, signature, body + b" ")
    assert error.value.status_code == 401
