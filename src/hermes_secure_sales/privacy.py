import re
from collections import defaultdict


EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")
ID_RE = re.compile(r"(?<!\d)(?:\d[ -]?){8,11}\d(?!\d)")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
NAME_LABEL_RE = re.compile(
    r"(?i)(\b(?:my name is|full name|name|tôi tên là|tên tôi là|em tên là|anh tên là|chị tên là|họ tên)\s*[:=]?\s*)([^\n,;.!?]{1,100})"
)
ADDRESS_LABEL_RE = re.compile(
    r"(?i)(\b(?:address|địa chỉ(?: giao hàng)?)\s*[:=]?\s*)([^\n,;]{1,200})"
)


def redact_pii(text: str) -> str:
    """Replace common contact/payment identifiers; mappings are never persisted."""
    counts: defaultdict[str, int] = defaultdict(int)

    def replace(kind: str, match: re.Match[str]) -> str:
        counts[kind] += 1
        return f"[{kind}_{counts[kind]}]"

    redacted = EMAIL_RE.sub(lambda match: replace("EMAIL", match), text)
    redacted = CARD_RE.sub(lambda match: replace("CARD", match), redacted)
    redacted = ID_RE.sub(lambda match: replace("ID", match), redacted)
    redacted = IP_RE.sub(lambda match: replace("IP", match), redacted)

    def replace_labeled(kind: str, match: re.Match[str]) -> str:
        counts[kind] += 1
        return f"{match.group(1)}[{kind}_{counts[kind]}]"

    redacted = NAME_LABEL_RE.sub(lambda match: replace_labeled("NAME", match), redacted)
    redacted = ADDRESS_LABEL_RE.sub(lambda match: replace_labeled("ADDRESS", match), redacted)

    def phone_replacement(match: re.Match[str]) -> str:
        digits = sum(character.isdigit() for character in match.group())
        if digits < 8 or digits > 15:
            return match.group()
        return replace("PHONE", match)

    return PHONE_RE.sub(phone_replacement, redacted)

