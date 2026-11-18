"""One contract every adapter must meet, run against Fake, Replay and Gemini (stubbed SDK)."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from copilot_models import (
    FakeClient,
    Message,
    ModelClient,
    ModelError,
    ModelErrorCode,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ReplayClient,
    ToolCall,
    ToolSpec,
    Usage,
    request_hash,
)
from copilot_models.adapters.gemini_stub import gemini_with, text_response, tool_response
from pydantic import BaseModel

Primer = Callable[[ModelRequest, ModelResponse], ModelClient]


def _canned(text: str | None = "ok", tool_calls: list[ToolCall] | None = None) -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=tool_calls or [],
        usage=Usage(input_tokens=11, output_tokens=7, cost_usd=None),
        model="stub-model",
        latency_ms=0,
        finish_reason="stop",
    )


def _fake(request: ModelRequest, response: ModelResponse) -> ModelClient:
    return FakeClient([response])


def _replay_factory(tmp_path: Path) -> Primer:
    def make(request: ModelRequest, response: ModelResponse) -> ModelClient:
        # Written by hand here; the recording path has its own tests in test_replay.py.
        path = tmp_path / "cassette.json"
        entry = {
            "request": request.model_dump(mode="json"),
            "response": response.model_dump(mode="json"),
        }
        path.write_text(json.dumps({request_hash(request): entry}))
        return ReplayClient(path)

    return make


def _gemini(request: ModelRequest, response: ModelResponse) -> ModelClient:
    if response.tool_calls:
        reply = tool_response(response.tool_calls[0].name, response.tool_calls[0].arguments)
    else:
        reply = text_response(response.text or "", prompt_tokens=11, output_tokens=7)
    return gemini_with([reply])


@pytest.fixture(params=["fake", "replay", "gemini"])
def primer(request: pytest.FixtureRequest, tmp_path: Path) -> Primer:
    return {"fake": _fake, "replay": _replay_factory(tmp_path), "gemini": _gemini}[request.param]


async def test_returns_text_usage_and_model(primer: Primer, request_: ModelRequest) -> None:
    response = await primer(request_, _canned("hello")).generate(request_)
    assert response.text == "hello"
    assert response.usage.input_tokens == 11 and response.usage.output_tokens == 7
    assert response.model
    assert response.latency_ms >= 0


async def test_structured_output_parses_into_a_model(
    primer: Primer, request_: ModelRequest, schema: dict[str, Any]
) -> None:
    class Routed(BaseModel):
        intent: str

    asked = request_.model_copy(update={"output_schema": schema})
    response = await primer(asked, _canned('{"intent": "insights"}')).generate(asked)
    assert response.parse(Routed).intent == "insights"


async def test_malformed_structured_output_is_a_mapped_error(
    primer: Primer, request_: ModelRequest, schema: dict[str, Any]
) -> None:
    class Routed(BaseModel):
        intent: str

    asked = request_.model_copy(update={"output_schema": schema})
    response = await primer(asked, _canned('{"wrong": 1}')).generate(asked)
    with pytest.raises(ModelError) as info:
        response.parse(Routed)
    assert info.value.code is ModelErrorCode.INVALID_RESPONSE
    assert "wrong" not in str(info.value)  # the model's text is never echoed in errors


async def test_tool_calls_come_back_typed(primer: Primer, request_: ModelRequest) -> None:
    tool = ToolSpec(
        name="run_validated_select", description="Run SQL", parameters={"type": "object"}
    )
    asked = request_.model_copy(update={"tools": [tool]})
    call = ToolCall(name="run_validated_select", arguments={"purpose": "insights"})
    response = await primer(asked, _canned(None, [call])).generate(asked)
    assert response.tool_calls == [call]


async def test_the_request_is_not_mutated(primer: Primer, request_: ModelRequest) -> None:
    before = request_.model_dump()
    await primer(request_, _canned()).generate(request_)
    assert request_.model_dump() == before


def test_requests_validate_at_the_boundary() -> None:
    with pytest.raises(ValueError, match="messages"):
        ModelRequest(
            role=ModelRole.ROUTER, prompt_version="p@1", system="s", messages=[], timeout_seconds=1
        )
    with pytest.raises(ValueError, match="timeout"):
        ModelRequest(
            role=ModelRole.ROUTER,
            prompt_version="p@1",
            system="s",
            messages=[Message(role="user", content="x")],
            timeout_seconds=0,
        )
