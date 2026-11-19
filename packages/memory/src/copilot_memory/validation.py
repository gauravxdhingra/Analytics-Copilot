"""What may be remembered. Memory is read back into prompts, so the way in is narrow."""

import re
import unicodedata

from copilot_memory.models import TEXT_LIMIT, MemoryFailure

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
_NAME = re.compile(r"^[A-Za-z0-9 _-]{1,60}$")


def validate_text(text: str) -> str:
    """Trim and check a piece of memory text, or raise INVALID_TEXT without echoing it."""
    cleaned = text.strip()
    if not cleaned or len(cleaned) > TEXT_LIMIT:
        raise MemoryFailure("INVALID_TEXT")
    if "<" in cleaned or ">" in cleaned:
        raise MemoryFailure("INVALID_TEXT")  # no tags: nothing can close a block it sits in
    if any(unicodedata.category(c) in {"Cc", "Cf"} for c in cleaned):
        raise MemoryFailure("INVALID_TEXT")  # control and hidden characters
    if _IBAN.search(cleaned):
        raise MemoryFailure("INVALID_TEXT")  # a restricted value
    if re.search(r"\d{12,}", re.sub(r"[\s-]", "", cleaned)):
        raise MemoryFailure("INVALID_TEXT")  # long digit runs look like account or card numbers
    return cleaned


def validate_name(name: str | None) -> str:
    if name is None or not _NAME.fullmatch(name.strip()) or not name.strip():
        raise MemoryFailure("INVALID_NAME")
    return name.strip()
