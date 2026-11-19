"""The Groq adapter, against a mocked HTTP transport: no network, no key."""

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
from copilot_models import ModelError, ModelErrorCode, ModelRequest, ModelRole, ToolSpec
from copilot_models.adapters.groq import DEFAULT_MODEL, GroqClient, GroqSettings

KEY = "gsk_test_key_value"


def _reply(
    text: str | None = "ok",
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    finish: str = "stop",
    usage: tuple[int, int] = (11, 7),
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
    }


class Wire:
    """Records requests and serves queued responses."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.queue = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def body(self, index: int = -1) -> dict[str, Any]:
        parsed: dict[str, Any] = json.loads(self.requests[index].content)
        return parsed


def _json(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _client(wire: Wire, **overrides: Any) -> GroqClient:
    sleeps: list[float] = []

    async def no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    settings = GroqSettings(api_key=KEY, base_delay_seconds=0.5, max_delay_seconds=1.0, **overrides)
    client = GroqClient(
        settings,
        http=httpx.AsyncClient(transport=httpx.MockTransport(wire)),
        sleep=no_sleep,
        jitter=lambda: 1.0,
    )
    client.sleeps = sleeps  # type: ignore[attr-defined]
    return client


# --- request mapping -------------------------------------------------------------------------


async def test_the_request_goes_to_the_chat_endpoint_with_the_key_in_a_header(
    request_: ModelRequest,
) -> None:
    wire = Wire(_json(_reply()))
    await _client(wire).generate(request_)
    sent = wire.requests[0]
    assert str(sent.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(sent.url)  # never in the URL, where logs would keep it


async def test_the_system_prompt_comes_first_and_the_cheap_model_is_the_default(
    request_: ModelRequest,
) -> None:
    wire = Wire(_json(_reply()))
    await _client(wire).generate(request_)
    body = wire.body()
    assert body["model"] == DEFAULT_MODEL == "openai/gpt-oss-20b"
    assert body["messages"][0] == {"role": "system", "content": request_.system}
    assert body["messages"][1] == {"role": "user", "content": "How many transactions?"}
    assert body["temperature"] == 0.0
    assert body["max_completion_tokens"] == request_.max_output_tokens


async def test_reasoning_is_kept_low_because_it_is_billed_as_output(request_: ModelRequest) -> None:
    wire = Wire(_json(_reply()))
    await _client(wire).generate(request_)
    assert wire.body()["reasoning_effort"] == "low"


async def test_a_schema_becomes_structured_output(
    request_: ModelRequest, schema: dict[str, Any]
) -> None:
    wire = Wire(_json(_reply('{"intent": "x"}')))
    await _client(wire).generate(request_.model_copy(update={"output_schema": schema}))
    fmt = wire.body()["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["schema"] == schema


async def test_no_schema_means_no_response_format(request_: ModelRequest) -> None:
    wire = Wire(_json(_reply()))
    await _client(wire).generate(request_)
    assert "response_format" not in wire.body()


async def test_the_role_picks_the_model(request_: ModelRequest) -> None:
    wire = Wire(_json(_reply()))
    client = _client(wire, models={ModelRole.ROUTER: "router-model"})
    await client.generate(request_)
    assert wire.body()["model"] == "router-model"


async def test_tools_are_described_but_never_run_by_the_adapter(request_: ModelRequest) -> None:
    tool = ToolSpec(name="t", description="d", parameters={"type": "object", "properties": {}})
    wire = Wire(_json(_reply()))
    await _client(wire).generate(request_.model_copy(update={"tools": [tool]}))
    sent = wire.body()["tools"][0]
    assert sent["type"] == "function" and sent["function"]["name"] == "t"


# --- response mapping ------------------------------------------------------------------------


async def test_text_usage_and_cost_come_from_the_response(request_: ModelRequest) -> None:
    wire = Wire(_json(_reply("hello", usage=(1_000_000, 1_000_000))))
    reply = await _client(wire).generate(request_)
    assert reply.text == "hello" and reply.finish_reason == "stop"
    assert reply.usage.input_tokens == 1_000_000 and reply.usage.output_tokens == 1_000_000
    # $0.075 in + $0.30 out per million tokens for the default model.
    assert reply.usage.cost_usd == Decimal("0.375")
    assert reply.model == DEFAULT_MODEL


async def test_an_unpriced_model_reports_no_cost_rather_than_a_guess(
    request_: ModelRequest,
) -> None:
    wire = Wire(_json(_reply()))
    reply = await _client(wire, default_model="some-other-model").generate(request_)
    assert reply.usage.cost_usd is None


async def test_tool_calls_are_parsed_from_their_json_arguments(request_: ModelRequest) -> None:
    call = {"id": "c1", "type": "function", "function": {"name": "t", "arguments": '{"a": 1}'}}
    wire = Wire(_json(_reply(None, tool_calls=[call])))
    reply = await _client(wire).generate(request_)
    assert reply.tool_calls[0].name == "t" and reply.tool_calls[0].arguments == {"a": 1}


async def test_unparseable_tool_arguments_are_an_invalid_response(request_: ModelRequest) -> None:
    call = {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{not json"}}
    wire = Wire(_json(_reply(None, tool_calls=[call])))
    with pytest.raises(ModelError) as info:
        await _client(wire).generate(request_)
    assert info.value.code is ModelErrorCode.INVALID_RESPONSE


@pytest.mark.parametrize("payload", [{"choices": []}, _reply(None), _reply("")])
async def test_an_empty_answer_is_an_invalid_response(
    request_: ModelRequest, payload: dict[str, Any]
) -> None:
    with pytest.raises(ModelError) as info:
        await _client(Wire(_json(payload))).generate(request_)
    assert info.value.code is ModelErrorCode.INVALID_RESPONSE


async def test_a_body_that_is_not_json_is_an_invalid_response(request_: ModelRequest) -> None:
    wire = Wire(httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(ModelError) as info:
        await _client(wire).generate(request_)
    assert info.value.code is ModelErrorCode.INVALID_RESPONSE


# --- errors, retries and fallback ------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, ModelErrorCode.AUTH),
        (403, ModelErrorCode.AUTH),
        (400, ModelErrorCode.BAD_REQUEST),
        (404, ModelErrorCode.BAD_REQUEST),
        (422, ModelErrorCode.BAD_REQUEST),
    ],
)
async def test_a_refusal_maps_to_a_code_and_is_not_retried(
    request_: ModelRequest, status: int, code: ModelErrorCode
) -> None:
    wire = Wire(_json({"error": {"message": f"secret provider detail {KEY}"}}, status))
    with pytest.raises(ModelError) as info:
        await _client(wire).generate(request_)
    assert info.value.code is code and len(wire.requests) == 1
    assert "secret" not in str(info.value) and KEY not in str(info.value)


async def test_rate_limits_are_retried_with_capped_backoff_then_succeed(
    request_: ModelRequest,
) -> None:
    wire = Wire(_json({}, 429), _json({}, 503), _json(_reply("finally")))
    client = _client(wire)
    reply = await client.generate(request_)
    assert reply.text == "finally" and len(wire.requests) == 3
    assert client.sleeps == [0.5, 1.0]  # type: ignore[attr-defined]


async def test_retries_are_capped_and_the_last_code_is_raised(request_: ModelRequest) -> None:
    wire = Wire(*[_json({}, 429) for _ in range(5)])
    with pytest.raises(ModelError) as info:
        await _client(wire, max_attempts=3).generate(request_)
    assert info.value.code is ModelErrorCode.RATE_LIMITED and len(wire.requests) == 3


async def test_a_fallback_model_is_tried_after_the_primary_keeps_failing(
    request_: ModelRequest,
) -> None:
    wire = Wire(_json({}, 503), _json({}, 503), _json(_reply("from fallback")))
    reply = await _client(wire, max_attempts=2, fallback_model="backup").generate(request_)
    assert reply.text == "from fallback" and wire.body()["model"] == "backup"


async def test_a_timeout_is_a_code(request_: ModelRequest) -> None:
    wire = Wire(*[httpx.ReadTimeout("slow") for _ in range(3)])
    with pytest.raises(ModelError) as info:
        await _client(wire).generate(request_)
    assert info.value.code is ModelErrorCode.TIMEOUT


async def test_a_connection_failure_is_unavailable(request_: ModelRequest) -> None:
    wire = Wire(*[httpx.ConnectError("no route") for _ in range(3)])
    with pytest.raises(ModelError) as info:
        await _client(wire).generate(request_)
    assert info.value.code is ModelErrorCode.UNAVAILABLE


# --- fail closed, secrets, settings ----------------------------------------------------------


def test_no_key_means_no_model() -> None:
    with pytest.raises(ModelError) as info:
        GroqClient(GroqSettings(api_key=""))
    assert info.value.code is ModelErrorCode.AUTH


def test_the_key_never_appears_in_the_settings_repr() -> None:
    assert KEY not in repr(GroqSettings(api_key=KEY))


def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GROQ_API_KEY", "GROQ_MODEL", "GROQ_FALLBACK_MODEL", "GROQ_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    for role in ModelRole:
        monkeypatch.delenv(f"GROQ_MODEL_{role.name}", raising=False)


def test_settings_read_the_environment_and_treat_blank_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODEL", "")  # a blank line in an .env template
    monkeypatch.setenv("GROQ_BASE_URL", "")
    settings = GroqSettings.from_env()
    assert settings.api_key == "k"
    assert settings.model_for(ModelRole.ROUTER) == DEFAULT_MODEL
    assert settings.base_url == "https://api.groq.com/openai/v1"
    assert settings.fallback_model is None  # never invent one


def test_settings_honour_a_chosen_model_and_per_role_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("GROQ_MODEL", "shared")
    monkeypatch.setenv("GROQ_MODEL_PLANNER", "planner-only")
    settings = GroqSettings.from_env()
    assert settings.model_for(ModelRole.ROUTER) == "shared"
    assert settings.model_for(ModelRole.PLANNER) == "planner-only"


# --- honouring the provider's own retry hint -------------------------------------------------


def _limited(retry_after: str | None) -> httpx.Response:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return httpx.Response(429, json={"error": {"message": "slow down"}}, headers=headers)


async def test_a_rate_limit_waits_as_long_as_the_provider_says(request_: ModelRequest) -> None:
    wire = Wire(_limited("7"), _json(_reply("later")))
    client = _client(wire)
    assert (await client.generate(request_)).text == "later"
    assert client.sleeps == [7.0]  # type: ignore[attr-defined]


async def test_the_provider_hint_is_capped(request_: ModelRequest) -> None:
    wire = Wire(_limited("600"), _json(_reply("later")))
    client = _client(wire, max_retry_after_seconds=30.0)
    await client.generate(request_)
    assert client.sleeps == [30.0]  # type: ignore[attr-defined]


@pytest.mark.parametrize("hint", [None, "soon", "-3", ""])
async def test_a_missing_or_unusable_hint_falls_back_to_the_backoff(
    request_: ModelRequest, hint: str | None
) -> None:
    wire = Wire(_limited(hint), _json(_reply("later")))
    client = _client(wire)
    await client.generate(request_)
    assert client.sleeps == [0.5]  # type: ignore[attr-defined]


async def test_the_hint_never_leaks_into_the_error_text(request_: ModelRequest) -> None:
    wire = Wire(*[_limited("9") for _ in range(3)])
    with pytest.raises(ModelError) as info:
        await _client(wire).generate(request_)
    assert info.value.code is ModelErrorCode.RATE_LIMITED and "9" not in str(info.value)


# --- structured output that did not come out right -------------------------------------------


def _bad_json(code: str = "json_validate_failed") -> httpx.Response:
    body = {
        "error": {
            "code": code,
            "type": "invalid_request_error",
            "message": f"secret provider detail {KEY}",
            "failed_generation": "SECRET MODEL OUTPUT the user asked about",
        }
    }
    return httpx.Response(400, json=body)


async def test_output_that_breaks_the_schema_is_an_invalid_response_not_a_bad_request(
    request_: ModelRequest,
) -> None:
    # The provider reports a truncated or non-conforming structured answer as a 400. That is a
    # bad response, which the orchestrator's repair loop can retry, not a bad request.
    wire = Wire(_bad_json())
    with pytest.raises(ModelError) as info:
        await _client(wire).generate(request_)
    assert info.value.code is ModelErrorCode.INVALID_RESPONSE
    assert len(wire.requests) == 1  # not retried by the adapter: the caller repairs
    assert "SECRET" not in str(info.value) and KEY not in str(info.value)


async def test_any_other_400_is_still_a_bad_request(request_: ModelRequest) -> None:
    with pytest.raises(ModelError) as info:
        await _client(Wire(_bad_json("model_not_found"))).generate(request_)
    assert info.value.code is ModelErrorCode.BAD_REQUEST


async def test_a_400_with_an_unreadable_body_is_a_bad_request(request_: ModelRequest) -> None:
    with pytest.raises(ModelError) as info:
        await _client(Wire(httpx.Response(400, text="<html>nope</html>"))).generate(request_)
    assert info.value.code is ModelErrorCode.BAD_REQUEST


# --- pacing: wait before sending when the next request would not fit -------------------------


class PacedClock:
    """A clock that only moves when the (fake) sleep is called."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _with_limits(text: str, remaining: int, reset: str) -> httpx.Response:
    headers = {"x-ratelimit-remaining-tokens": str(remaining), "x-ratelimit-reset-tokens": reset}
    return httpx.Response(200, json=_reply(text), headers=headers)


def _paced(wire: Wire, clock: PacedClock, **overrides: Any) -> GroqClient:
    settings = GroqSettings(api_key=KEY, **overrides)
    return GroqClient(
        settings,
        http=httpx.AsyncClient(transport=httpx.MockTransport(wire)),
        sleep=clock.sleep,
        jitter=lambda: 1.0,
        clock=clock,
    )


def _big(request_: ModelRequest) -> ModelRequest:
    return request_.model_copy(update={"system": "x" * 7000, "max_output_tokens": 2048})


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("7.66s", 7.66), ("2m59.56s", 179.56), ("250ms", 0.25), ("1h", 3600.0), ("1m", 60.0)],
)
def test_the_providers_duration_format_is_understood(text: str, seconds: float) -> None:
    from copilot_models.adapters.groq import parse_duration

    assert parse_duration(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", ["", "soon", "-3s", "5 parsecs"])
def test_an_unreadable_duration_is_none(text: str) -> None:
    from copilot_models.adapters.groq import parse_duration

    assert parse_duration(text) is None


async def test_it_waits_for_the_window_when_the_next_request_would_not_fit(
    request_: ModelRequest,
) -> None:
    clock = PacedClock()
    wire = Wire(_with_limits("one", remaining=300, reset="9s"), _json(_reply("two")))
    client = _paced(wire, clock)
    await client.generate(request_)
    await client.generate(_big(request_))  # needs about 2,500 tokens; only 300 are left
    assert clock.slept == [pytest.approx(9.0)]
    assert len(wire.requests) == 2  # it waited instead of sending a doomed request


async def test_it_does_not_wait_when_there_is_room(request_: ModelRequest) -> None:
    clock = PacedClock()
    wire = Wire(_with_limits("one", remaining=7000, reset="9s"), _json(_reply("two")))
    client = _paced(wire, clock)
    await client.generate(request_)
    await client.generate(request_)
    assert clock.slept == []


async def test_without_limit_headers_it_never_waits(request_: ModelRequest) -> None:
    clock = PacedClock()
    wire = Wire(_json(_reply("one")), _json(_reply("two")))
    client = _paced(wire, clock)
    await client.generate(request_)
    await client.generate(_big(request_))
    assert clock.slept == []


async def test_a_window_that_has_already_passed_costs_nothing(request_: ModelRequest) -> None:
    clock = PacedClock()
    wire = Wire(_with_limits("one", remaining=100, reset="5s"), _json(_reply("two")))
    client = _paced(wire, clock)
    await client.generate(request_)
    clock.now += 6  # time passes on its own
    await client.generate(_big(request_))
    assert clock.slept == []


async def test_the_wait_is_capped(request_: ModelRequest) -> None:
    clock = PacedClock()
    wire = Wire(_with_limits("one", remaining=10, reset="5m"), _json(_reply("two")))
    client = _paced(wire, clock, max_retry_after_seconds=20.0)
    await client.generate(request_)
    await client.generate(_big(request_))
    assert clock.slept == [20.0]


async def test_a_rate_limited_retry_does_not_wait_twice_for_the_same_window(
    request_: ModelRequest,
) -> None:
    clock = PacedClock()
    limited = httpx.Response(
        429,
        json={},
        headers={
            "retry-after": "9",
            "x-ratelimit-remaining-tokens": "0",
            "x-ratelimit-reset-tokens": "9s",
        },
    )
    wire = Wire(limited, _json(_reply("later")))
    await _paced(wire, clock).generate(request_)
    assert clock.slept == [9.0]  # once, from the retry hint, not again from the pacer
