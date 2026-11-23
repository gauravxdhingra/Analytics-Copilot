"""Groq adapter over its OpenAI-compatible HTTP API. A second implementation of the model port.

No provider SDK: one POST per call, so the surface is small and fully mocked in tests. The default
model is the cheapest chat model on the account at the time of writing.
"""

import asyncio
import json
import os
import random
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from copilot_models.errors import ModelError, ModelErrorCode
from copilot_models.port import ModelRequest, ModelResponse, ModelRole, ToolCall, Usage

# [VERIFY] against https://console.groq.com/docs/models when pinning: names and prices change.
# gpt-oss-20b is the cheapest chat model there: $0.075 in, $0.30 out per million tokens.
DEFAULT_MODEL = "openai/gpt-oss-20b"
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_PRICES: Mapping[str, tuple[Decimal, Decimal]] = {
    DEFAULT_MODEL: (Decimal("0.075"), Decimal("0.30")),
}

_RETRYABLE = {ModelErrorCode.RATE_LIMITED, ModelErrorCode.UNAVAILABLE, ModelErrorCode.TIMEOUT}


@dataclass(frozen=True)
class GroqSettings:
    api_key: str = field(repr=False)
    models: Mapping[ModelRole, str] = field(default_factory=dict)
    default_model: str = DEFAULT_MODEL
    fallback_model: str | None = None
    base_url: str = DEFAULT_BASE_URL
    trust_env: bool = True
    # Reasoning tokens are billed as output, so keep them low: this work is plain extraction
    # and planning over a small schema, not open-ended thinking.
    # None leaves the field out for OpenAI-compatible gateways that do not accept it.
    reasoning_effort: str | None = "low"
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 4.0
    # How long we will honour the provider's own Retry-After. Per-minute token limits take
    # several seconds to clear, so the short generic backoff alone just burns attempts.
    max_retry_after_seconds: float = 30.0
    # (input, output) USD per million tokens. A model not listed reports no cost: never a guess.
    prices_per_million: Mapping[str, tuple[Decimal, Decimal]] = field(
        default_factory=lambda: dict(DEFAULT_PRICES)
    )

    def model_for(self, role: ModelRole) -> str:
        return self.models.get(role, self.default_model)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GroqSettings":
        source = os.environ if env is None else env

        # A blank value (an empty line in an .env template) counts as not set.
        def given(name: str) -> str | None:
            return source.get(name) or None

        return cls(
            api_key=source.get("GROQ_API_KEY", ""),
            models={
                role: name
                for role in ModelRole
                if (name := given(f"GROQ_MODEL_{role.name}")) is not None
            },
            default_model=given("GROQ_MODEL") or DEFAULT_MODEL,
            fallback_model=given("GROQ_FALLBACK_MODEL"),
            base_url=given("GROQ_BASE_URL") or DEFAULT_BASE_URL,
        )


def _map_status(code: int) -> ModelErrorCode:
    if code == 429:
        return ModelErrorCode.RATE_LIMITED
    if code in (408, 504):
        return ModelErrorCode.TIMEOUT
    if code >= 500:
        return ModelErrorCode.UNAVAILABLE
    if code in (401, 403):
        return ModelErrorCode.AUTH
    return ModelErrorCode.BAD_REQUEST


def _provider_code(response: httpx.Response) -> str | None:
    """The provider's short, fixed error code. Its message and any generated text are never read."""
    try:
        code = response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None
    return code if isinstance(code, str) else None


def _retry_after(response: httpx.Response) -> float | None:
    """The provider's wait hint in seconds, if it sent a usable one."""
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return seconds if seconds > 0 else None


_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNIT = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(text: str) -> float | None:
    """The provider's reset format ("7.66s", "2m59.56s", "250ms") in seconds, or None."""
    text = text.strip()
    parts = _DURATION.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        return None
    return sum(float(n) * _UNIT[u] for n, u in parts)


class _Pacer:
    """Remembers how many tokens the provider says are left in the current window.

    Sending a request that cannot fit only earns a 429 and wastes a request, so wait for the
    window to reset first. With no headers there is nothing to go on, and it never waits.
    """

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._remaining: int | None = None
        self._resets_at: float | None = None

    def observe(self, headers: httpx.Headers) -> None:
        try:
            remaining = int(headers["x-ratelimit-remaining-tokens"])
        except (KeyError, ValueError):
            return
        reset = parse_duration(headers.get("x-ratelimit-reset-tokens", ""))
        self._remaining = remaining
        self._resets_at = self._clock() + reset if reset is not None else None

    def wait_for(self, needed: int) -> float:
        if self._remaining is None or self._resets_at is None or self._remaining >= needed:
            return 0.0
        return max(0.0, self._resets_at - self._clock())


def _estimate_tokens(request: ModelRequest) -> int:
    """A deliberately rough size of a request: prompt at about 3.5 characters a token, plus a
    typical reply. An underestimate only costs one 429, which the retry hint then handles."""
    prompt = len(request.system) + sum(len(m.content) for m in request.messages)
    return int(prompt / 3.5) + min(request.max_output_tokens, 800)


class GroqClient:
    def __init__(
        self,
        settings: GroqSettings,
        *,
        http: httpx.AsyncClient | None = None,  # a test stand-in, or a shared client
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not settings.api_key:
            raise ModelError(ModelErrorCode.AUTH)  # fail closed: no key, no model
        self._settings = settings
        self._http = http or httpx.AsyncClient(trust_env=settings.trust_env)
        self._sleep = sleep
        self._jitter = jitter
        self._pacer = _Pacer(clock)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        primary = self._settings.model_for(request.role)
        fallback = self._settings.fallback_model
        order = [primary] + ([fallback] if fallback and fallback != primary else [])
        failure: ModelError | None = None
        for model in order:
            try:
                return await self._with_retries(model, request)
            except ModelError as exc:
                if exc.code not in _RETRYABLE:
                    raise  # a bad request or bad key will not improve on another model
                failure = exc
        raise failure or ModelError(ModelErrorCode.UNAVAILABLE)

    async def _with_retries(self, model: str, request: ModelRequest) -> ModelResponse:
        # Capped retries with jitter. Safe because a model call is an idempotent read.
        attempts = self._settings.max_attempts
        for attempt in range(attempts):
            try:
                return await self._call(model, request)
            except ModelError as exc:
                if exc.code not in _RETRYABLE or attempt == attempts - 1:
                    raise
                ceiling = min(
                    self._settings.max_delay_seconds, self._settings.base_delay_seconds * 2**attempt
                )
                delay = ceiling * (0.5 + self._jitter() / 2)
                if exc.retry_after is not None:
                    delay = min(exc.retry_after, self._settings.max_retry_after_seconds)
                await self._sleep(delay)
        raise AssertionError("unreachable")

    async def _call(self, model: str, request: ModelRequest) -> ModelResponse:
        wait = min(
            self._pacer.wait_for(_estimate_tokens(request)), self._settings.max_retry_after_seconds
        )
        if wait > 0:
            await self._sleep(wait)  # let the window reset instead of sending a doomed request
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self._http.post(
                    f"{self._settings.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self._settings.api_key}"},
                    json=self._body(model, request),
                    timeout=request.timeout_seconds,
                ),
                timeout=request.timeout_seconds,
            )
        except (TimeoutError, httpx.TimeoutException):
            raise ModelError(ModelErrorCode.TIMEOUT) from None
        except httpx.TransportError:
            raise ModelError(ModelErrorCode.UNAVAILABLE) from None
        self._pacer.observe(response.headers)
        if response.status_code != 200:
            # The provider's message is never read: it can echo request content or the key.
            if response.status_code == 400 and _provider_code(response) == "json_validate_failed":
                # Structured output that was cut off or did not match the schema: a bad
                # response, which the caller can repair, rather than a bad request.
                raise ModelError(ModelErrorCode.INVALID_RESPONSE)
            raise ModelError(_map_status(response.status_code), retry_after=_retry_after(response))
        return self._convert(response, model, int((time.monotonic() - started) * 1000))

    def _body(self, model: str, request: ModelRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system},
                *({"role": m.role, "content": m.content} for m in request.messages),
            ],
            "temperature": request.temperature,
            "max_completion_tokens": request.max_output_tokens,
        }
        if self._settings.reasoning_effort:
            body["reasoning_effort"] = self._settings.reasoning_effort
        if request.output_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": request.output_schema},
            }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
        return body

    def _convert(self, response: httpx.Response, model: str, latency_ms: int) -> ModelResponse:
        try:
            payload = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
            usage = payload.get("usage") or {}
            text = message.get("content") or None
            calls = [
                ToolCall(
                    name=str(c["function"]["name"]),
                    arguments=dict(json.loads(c["function"].get("arguments") or "{}")),
                )
                for c in message.get("tool_calls") or []
            ]
            finish = str(choice.get("finish_reason") or "stop")
            tokens_in = int(usage.get("prompt_tokens") or 0)
            tokens_out = int(usage.get("completion_tokens") or 0)  # includes reasoning tokens
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError(ModelErrorCode.INVALID_RESPONSE) from None
        if text is None and not calls:
            raise ModelError(ModelErrorCode.INVALID_RESPONSE)
        return ModelResponse(
            text=text,
            tool_calls=calls,
            usage=Usage(
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                cost_usd=self._cost(model, tokens_in, tokens_out),
            ),
            model=model,
            latency_ms=latency_ms,
            finish_reason=finish.lower(),
        )

    def _cost(self, model: str, tokens_in: int, tokens_out: int) -> Decimal | None:
        price = self._settings.prices_per_million.get(model)
        if price is None:
            return None
        return (Decimal(tokens_in) * price[0] + Decimal(tokens_out) * price[1]) / Decimal(1_000_000)
