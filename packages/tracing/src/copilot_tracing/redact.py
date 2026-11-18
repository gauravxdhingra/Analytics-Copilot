"""Masking of restricted values. Shared by the gateway and the decision store."""

import re

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")


def _mod97(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def redact_ibans(text: str) -> str:
    """Mask every valid IBAN, keeping the last four characters for human reference."""

    def mask(match: re.Match[str]) -> str:
        value = match.group(0)
        return f"[IBAN …{value[-4:]}]" if _mod97(value) else value

    return _IBAN.sub(mask, text)
