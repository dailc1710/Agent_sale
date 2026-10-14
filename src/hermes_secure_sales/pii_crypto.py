"""Authenticated encryption for customer-submitted contact details."""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class PIIEncryptionUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _fernet_for_key(key: str) -> Fernet:
    try:
        return Fernet(key.encode("ascii"))
    except (UnicodeEncodeError, ValueError):
        raise PIIEncryptionUnavailable("PII_ENCRYPTION_KEY phải là khóa Fernet hợp lệ") from None


def _fernet() -> Fernet:
    key = get_settings().pii_encryption_key.strip()
    if not key:
        raise PIIEncryptionUnavailable("Chưa cấu hình PII_ENCRYPTION_KEY")
    return _fernet_for_key(key)


def encrypt_pii(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_pii(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (UnicodeEncodeError, InvalidToken, UnicodeDecodeError):
        raise PIIEncryptionUnavailable(
            "Không thể giải mã dữ liệu; kiểm tra PII_ENCRYPTION_KEY"
        ) from None
