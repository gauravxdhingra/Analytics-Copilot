"""Runs against the real Gemini API. Skipped unless GEMINI_API_KEY is set.

Run with `GEMINI_API_KEY=... uv run pytest packages/models -m live`. Costs a few tokens.
"""

import os

import pytest
from copilot_models import Message, ModelRequest, ModelRole
from copilot_models.adapters.gemini import GeminiClient, GeminiSettings
from pydantic import BaseModel

pytestmark = pytest.mark.live


class Routed(BaseModel):
    intent: str


async def test_structured_output_round_trip_against_the_real_api() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")
    client = GeminiClient(GeminiSettings.from_env())
    request = ModelRequest(
        role=ModelRole.ROUTER,
        prompt_version="live-smoke@1",
        system="Classify the user's question as one of: insights, documents, other.",
        messages=[Message(role="user", content="How many booked transactions are there?")],
        output_schema=Routed.model_json_schema(),
        timeout_seconds=30.0,
    )
    response = await client.generate(request)
    assert response.parse(Routed).intent in {"insights", "documents", "other"}
    assert response.usage.input_tokens > 0
