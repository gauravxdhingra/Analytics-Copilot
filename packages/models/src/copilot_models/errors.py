from enum import StrEnum


class ModelErrorCode(StrEnum):
    """Mapped codes. Provider messages and model output never leave the adapter."""

    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    AUTH = "auth"
    BAD_REQUEST = "bad_request"
    INVALID_RESPONSE = "invalid_response"  # empty, blocked, or not matching the schema
    REPLAY_MISS = "replay_miss"


class ModelError(Exception):
    def __init__(self, code: ModelErrorCode, detail: str = "") -> None:
        # `detail` is for non-sensitive identifiers only (for example a request hash).
        super().__init__(f"{code.value}: {detail}" if detail else code.value)
        self.code = code
