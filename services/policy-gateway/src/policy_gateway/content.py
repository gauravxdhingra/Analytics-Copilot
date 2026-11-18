"""Post-processing of tool output: strip hidden content, redact IBANs, wrap untrusted text."""

import re

from copilot_tracing.redact import redact_ibans

_HIDDEN = re.compile("[​‌‍⁠⁡⁢⁣﻿]|<!--.*?-->", re.DOTALL)
_OPEN_TAG = re.compile(r"<\s*untrusted", re.IGNORECASE)
_CLOSE_TAG = re.compile(r"<\s*/\s*untrusted", re.IGNORECASE)


def strip_hidden(text: str) -> tuple[str, bool]:
    """Remove zero-width characters and HTML comments. Returns (clean text, anything removed)."""
    cleaned = _HIDDEN.sub("", text)
    return cleaned, cleaned != text


def wrap_untrusted(text: str, source: str) -> str:
    """Delimit text a model must treat as data. Tags inside the text are neutralised first, so
    content cannot close the block early or open a fake one."""
    safe = _CLOSE_TAG.sub("&lt;/untrusted", _OPEN_TAG.sub("&lt;untrusted", text))
    return f'<untrusted source="{source}">{safe}</untrusted>'


__all__ = ["redact_ibans", "strip_hidden", "wrap_untrusted"]
