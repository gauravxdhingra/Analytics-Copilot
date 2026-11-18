"""Provider-neutral model port with Fake, Replay and Gemini adapters (ADR 0002)."""

from copilot_models.errors import ModelError, ModelErrorCode
from copilot_models.fake import FakeClient
from copilot_models.port import (
    Message,
    ModelClient,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ToolCall,
    ToolSpec,
    Usage,
    request_hash,
)
from copilot_models.replay import RecordingClient, ReplayClient

__all__ = [
    "FakeClient",
    "Message",
    "ModelClient",
    "ModelError",
    "ModelErrorCode",
    "ModelRequest",
    "ModelResponse",
    "ModelRole",
    "RecordingClient",
    "ReplayClient",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "request_hash",
]
