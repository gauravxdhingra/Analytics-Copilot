import asyncio
from typing import Any

import pytest
from copilot_models import Message, ModelError, ModelErrorCode, ModelRequest, ModelRole, ToolSpec
from copilot_models.adapters.gemini import GeminiSettings
from copilot_models.adapters.gemini_stub import (
    StubSdk,
    empty_response,
    gemini_with,
    hang,
    text_response,
    tool_response,
)
from google.genai import errors


def _sdk(client: Any) -> StubSdk:
    sdk = client._sdk
    assert isinstance(sdk, StubSdk)
    return sdk


def _api_error(code: int) -> errors.APIError:
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"message": "secret provider detail", "status": "X"}})


SETTINGS = GeminiSettings(
    api_key="test-key",
    models={ModelRole.ROUTER: "primary-model", ModelRole.PLANNER: "planner-model"},
    fallback_model="fallback-model",
    max_attempts=3,
    base_delay_seconds=0.5,
    max_delay_seconds=1.5,
)


async def test_request_mapping(request_: ModelRequest, schema: dict[str, Any]) -> None:
    tool = ToolSpec(name="t", description="d", parameters={"type": "object", "properties": {}})
    asked = request_.model_copy(
        update={
            "output_schema": schema,
            "tools": [tool],
            "temperature": 0.1,
            "max_output_tokens": 256,
            "messages": [
                Message(role="user", content="q1"),
                Message(role="assistant", content="a1"),
                Message(role="user", content="q2"),
            ],
        }
    )
    client = gemini_with([text_response("{}")], settings=SETTINGS)
    await client.generate(asked)
    call = _sdk(client).models.calls[0]
    assert call["model"] == "primary-model"
    assert [c.role for c in call["contents"]] == ["user", "model", "user"]
    config = call["config"]
    assert config.system_instruction == "Classify the question."
    assert config.temperature == pytest.approx(0.1)
    assert config.max_output_tokens == 256
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == schema
    assert config.tools[0].function_declarations[0].name == "t"
    assert config.automatic_function_calling.disable is True  # we run tools, never the SDK


async def test_model_is_chosen_by_role(request_: ModelRequest) -> None:
    client = gemini_with([text_response("x")], settings=SETTINGS)
    await client.generate(request_.model_copy(update={"role": ModelRole.PLANNER}))
    assert _sdk(client).models.calls[0]["model"] == "planner-model"


async def test_usage_counts_reasoning_tokens_as_output(request_: ModelRequest) -> None:
    reply = text_response("x", prompt_tokens=10, output_tokens=4)
    assert reply.usage_metadata is not None
    reply.usage_metadata.thoughts_token_count = 6
    response = await gemini_with([reply], settings=SETTINGS).generate(request_)
    assert (response.usage.input_tokens, response.usage.output_tokens) == (10, 10)


async def test_cost_is_computed_only_from_configured_prices(request_: ModelRequest) -> None:
    from decimal import Decimal

    priced = GeminiSettings(
        api_key="k",
        models={ModelRole.ROUTER: "primary-model"},
        prices_per_million={"primary-model": (Decimal("1.00"), Decimal("2.00"))},
    )
    reply = text_response("x", prompt_tokens=1_000_000, output_tokens=500_000)
    cost = (await gemini_with([reply], settings=priced).generate(request_)).usage.cost_usd
    assert cost == Decimal("2.00")
    unpriced = await gemini_with([text_response("x")], settings=SETTINGS).generate(request_)
    assert unpriced.usage.cost_usd is None  # never guess a price


@pytest.mark.parametrize("code", [429, 500, 503])
async def test_transient_errors_are_retried_with_capped_jittered_backoff(
    code: int, request_: ModelRequest
) -> None:
    sleeps: list[float] = []
    client = gemini_with(
        [_api_error(code), _api_error(code), text_response("recovered")],
        settings=SETTINGS,
        sleeps=sleeps,
    )
    response = await client.generate(request_)
    assert response.text == "recovered"
    assert len(_sdk(client).models.calls) == 3
    assert sleeps == [0.5, 1.0]  # jitter fixed at 1.0 in the stub; doubles up to the cap


async def test_backoff_never_exceeds_the_cap(request_: ModelRequest) -> None:
    settings = GeminiSettings(
        api_key="k",
        models={ModelRole.ROUTER: "m"},
        max_attempts=5,
        base_delay_seconds=1.0,
        max_delay_seconds=2.0,
        fallback_model=None,
    )
    sleeps: list[float] = []
    client = gemini_with([_api_error(503)] * 5, settings=settings, sleeps=sleeps)
    with pytest.raises(ModelError):
        await client.generate(request_)
    assert max(sleeps) <= 2.0 and len(sleeps) == 4


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (429, ModelErrorCode.RATE_LIMITED),
        (503, ModelErrorCode.UNAVAILABLE),
        (504, ModelErrorCode.TIMEOUT),
    ],
)
async def test_exhausted_retries_map_to_a_code(
    code: int, expected: ModelErrorCode, request_: ModelRequest
) -> None:
    settings = GeminiSettings(
        api_key="k", models={ModelRole.ROUTER: "m"}, max_attempts=2, fallback_model=None
    )
    client = gemini_with([_api_error(code)] * 2, settings=settings)
    with pytest.raises(ModelError) as info:
        await client.generate(request_)
    assert info.value.code is expected
    assert "secret provider detail" not in str(info.value)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (400, ModelErrorCode.BAD_REQUEST),
        (401, ModelErrorCode.AUTH),
        (403, ModelErrorCode.AUTH),
        (404, ModelErrorCode.BAD_REQUEST),
    ],
)
async def test_client_errors_are_not_retried(
    code: int, expected: ModelErrorCode, request_: ModelRequest
) -> None:
    client = gemini_with([_api_error(code), text_response("never")], settings=SETTINGS)
    with pytest.raises(ModelError) as info:
        await client.generate(request_)
    assert info.value.code is expected
    assert len(_sdk(client).models.calls) == 1


async def test_a_hung_call_times_out_and_is_mapped(request_: ModelRequest) -> None:
    settings = GeminiSettings(
        api_key="k", models={ModelRole.ROUTER: "m"}, max_attempts=1, fallback_model=None
    )
    fast = request_.model_copy(update={"timeout_seconds": 0.05})
    client = gemini_with([hang], settings=settings)
    with pytest.raises(ModelError) as info:
        await asyncio.wait_for(client.generate(fast), timeout=2)
    assert info.value.code is ModelErrorCode.TIMEOUT


async def test_the_fallback_model_is_used_after_the_primary_is_exhausted(
    request_: ModelRequest,
) -> None:
    settings = GeminiSettings(
        api_key="k",
        models={ModelRole.ROUTER: "primary-model"},
        fallback_model="fallback-model",
        max_attempts=2,
    )
    client = gemini_with(
        [_api_error(503), _api_error(503), text_response("from fallback")], settings=settings
    )
    response = await client.generate(request_)
    assert response.text == "from fallback"
    assert [c["model"] for c in _sdk(client).models.calls] == [
        "primary-model",
        "primary-model",
        "fallback-model",
    ]
    assert response.model == "fallback-model"


async def test_both_models_failing_is_one_mapped_error(request_: ModelRequest) -> None:
    settings = GeminiSettings(
        api_key="k", models={ModelRole.ROUTER: "p"}, fallback_model="f", max_attempts=1
    )
    client = gemini_with([_api_error(503), _api_error(503)], settings=settings)
    with pytest.raises(ModelError) as info:
        await client.generate(request_)
    assert info.value.code is ModelErrorCode.UNAVAILABLE


async def test_a_bad_request_does_not_fall_back(request_: ModelRequest) -> None:
    client = gemini_with([_api_error(400), text_response("never")], settings=SETTINGS)
    with pytest.raises(ModelError):
        await client.generate(request_)
    assert len(_sdk(client).models.calls) == 1


@pytest.mark.parametrize("reply", [empty_response(), text_response("hi", finish="SAFETY")])
async def test_empty_or_blocked_output_is_an_invalid_response(
    reply: Any, request_: ModelRequest
) -> None:
    with pytest.raises(ModelError) as info:
        await gemini_with([reply], settings=SETTINGS).generate(request_)
    assert info.value.code is ModelErrorCode.INVALID_RESPONSE


async def test_tool_call_arguments_are_plain_json(request_: ModelRequest) -> None:
    reply = tool_response("run_validated_select", {"purpose": "p", "n": 3})
    response = await gemini_with([reply], settings=SETTINGS).generate(request_)
    assert response.tool_calls[0].arguments == {"purpose": "p", "n": 3}


def test_the_api_key_is_never_shown() -> None:
    assert "test-key" not in repr(GeminiSettings(api_key="test-key"))


def test_missing_key_fails_closed_at_construction() -> None:
    from copilot_models.adapters.gemini import GeminiClient

    with pytest.raises(ModelError) as info:
        GeminiClient(GeminiSettings(api_key=""))
    assert info.value.code is ModelErrorCode.AUTH


def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_FALLBACK_MODEL", "GEMINI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    for role in ModelRole:
        monkeypatch.delenv(f"GEMINI_MODEL_{role.name}", raising=False)


def test_one_model_name_applies_to_every_role(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL", "my-model")
    settings = GeminiSettings.from_env()
    assert {settings.model_for(role) for role in ModelRole} == {"my-model"}


def test_a_role_specific_name_beats_the_shared_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("GEMINI_MODEL", "shared")
    monkeypatch.setenv("GEMINI_MODEL_PLANNER", "planner-only")
    settings = GeminiSettings.from_env()
    assert settings.model_for(ModelRole.PLANNER) == "planner-only"
    assert settings.model_for(ModelRole.ROUTER) == "shared"


def test_an_empty_value_means_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    # An .env template often has blank lines such as GEMINI_MODEL= ; they must not become "".
    _clean(monkeypatch)
    monkeypatch.setenv("GEMINI_MODEL", "")
    monkeypatch.setenv("GEMINI_BASE_URL", "")
    settings = GeminiSettings.from_env()
    assert settings.model_for(ModelRole.ROUTER) != ""
    assert settings.base_url is None


def test_a_base_url_is_read_when_given(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("GEMINI_BASE_URL", "https://proxy.example/gemini")
    assert GeminiSettings.from_env().base_url == "https://proxy.example/gemini"


def test_the_key_never_appears_in_the_settings_repr() -> None:
    assert "secret-value" not in repr(GeminiSettings(api_key="secret-value"))
