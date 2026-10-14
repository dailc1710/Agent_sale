"""Signed Google Forms submission bridge into the private consultation inbox."""

import json
from datetime import timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .dependencies import get_platform_db
from .models import GoogleFormSubmission, Shop
from .pii_crypto import PIIEncryptionUnavailable, encrypt_pii
from .schemas import GoogleFormSubmissionIn
from .security import verify_signed_payload


router = APIRouter()
MAX_BODY_BYTES = 16_384


def _integration_for_shop_slug(shop_slug: str) -> dict:
    try:
        configs = json.loads(get_settings().google_forms_webhook_configs_json)
        config = configs.get(shop_slug)
        form_id = config["form_id"]
        secret = config["secret"]
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=503, detail="Google Forms integration is not configured") from None

    if not isinstance(form_id, str) or not form_id or not isinstance(secret, str) or len(secret) < 32:
        raise HTTPException(status_code=503, detail="Google Forms integration is not configured")
    return {"form_id": form_id, "secret": secret}


@router.post("/integrations/google-forms/{shop_slug}", status_code=status.HTTP_202_ACCEPTED)
async def receive_google_form_submission(
    shop_slug: str,
    request: Request,
    timestamp: Annotated[str | None, Header(alias="X-Hermes-Timestamp")] = None,
    signature: Annotated[str | None, Header(alias="X-Hermes-Signature")] = None,
    db: Session = Depends(get_platform_db),
) -> dict[str, str]:
    config = _integration_for_shop_slug(shop_slug)
    raw_body = await request.body()
    if len(raw_body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Google Forms payload is too large")
    verify_signed_payload(config["secret"], timestamp, signature, raw_body)

    try:
        submission = GoogleFormSubmissionIn.model_validate_json(raw_body)
    except ValidationError:
        raise HTTPException(status_code=422, detail="Invalid Google Forms response") from None

    if submission.form_id != config["form_id"]:
        raise HTTPException(status_code=404, detail="Google Forms integration not found")
    if not submission.consent:
        raise HTTPException(status_code=422, detail="Cần xác nhận đồng ý xử lý thông tin trước khi đồng bộ")

    shop = db.scalar(
        select(Shop).where(
            Shop.slug == shop_slug,
            Shop.enabled.is_(True),
        )
    )
    if shop is None:
        raise HTTPException(status_code=404, detail="Google Forms integration not found")

    existing = db.scalar(
        select(GoogleFormSubmission.id).where(
            GoogleFormSubmission.tenant_id == shop.tenant_id,
            GoogleFormSubmission.response_id == submission.response_id,
        )
    )
    if existing is not None:
        return {"status": "already_imported"}

    try:
        row = GoogleFormSubmission(
            tenant_id=shop.tenant_id,
            shop_id=shop.id,
            response_id=submission.response_id,
            name_ciphertext=encrypt_pii(submission.name),
            phone_ciphertext=encrypt_pii(submission.phone),
            desired_time_ciphertext=encrypt_pii(submission.desired_time),
            note_ciphertext=encrypt_pii(submission.note) if submission.note else None,
            submitted_at=submission.submitted_at.astimezone(timezone.utc),
        )
        db.add(row)
        db.commit()
    except PIIEncryptionUnavailable as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(GoogleFormSubmission.id).where(
                GoogleFormSubmission.tenant_id == shop.tenant_id,
                GoogleFormSubmission.response_id == submission.response_id,
            )
        )
        if existing is not None:
            return {"status": "already_imported"}
        raise HTTPException(status_code=409, detail="Could not save Google Forms response") from None

    return {"status": "imported"}

