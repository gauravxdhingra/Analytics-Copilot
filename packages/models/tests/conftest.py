from typing import Any

import pytest
from copilot_models import Message, ModelRequest, ModelRole


@pytest.fixture
def request_() -> ModelRequest:
    return ModelRequest(
        role=ModelRole.ROUTER,
        prompt_version="router@1",
        system="Classify the question.",
        messages=[Message(role="user", content="How many transactions?")],
        timeout_seconds=2.0,
    )


@pytest.fixture
def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"intent": {"type": "string"}},
        "required": ["intent"],
    }
