"""IBAN check digits (ISO 13616 mod-97). Generated IBANs use fictional bank codes."""

_LENGTH = {"DE": 22, "GB": 22, "FR": 27, "NL": 18}


def _mod97(text: str) -> int:
    digits = "".join(str(int(ch, 36)) for ch in text)
    return int(digits) % 97


def make_iban(country: str, bban: str) -> str:
    check = 98 - _mod97(bban + country + "00")
    return f"{country}{check:02d}{bban}"


def is_valid_iban(iban: str) -> bool:
    iban = iban.replace(" ", "").upper()
    if len(iban) < 15 or not iban.isalnum() or not iban[:2].isalpha():
        return False
    expected = _LENGTH.get(iban[:2])
    if expected is not None and len(iban) != expected:
        return False
    return _mod97(iban[4:] + iban[:4]) == 1
