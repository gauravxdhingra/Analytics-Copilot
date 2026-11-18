"""Gemini adapter (google-genai SDK). The only module allowed to import the provider SDK."""

import asyncio
import os
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from copilot_models.errors import ModelError, ModelErrorCode
from copilot_models.port import ModelRequest, ModelResponse, ModelRole, ToolCall, Usage

# [VERIFY] against https://ai.google.dev/gemini-api/docs/models when pinning: names and
# availability change, and Pro is preview-only at the time of writing.
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_FALLBACK = "gemini-3.6-flash"

_RETRYABLE = {ModelErrorCode.RATE_LIMITED, ModelErrorCode.UNAVAILABLE, ModelErrorCode.TIMEOUT}
_BLOCKED = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION", "IMAGE_SAFETY"}


@dataclass(frozen=True)
class GeminiSettings:
    api_key: str = field(repr=False)
    models: Mapping[ModelRole, str] = field(default_factory=dict)
    fallback_model: str | None = DEFAULT_FALLBACK
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 4.0
    # (input, output) USD per million tokens. Empty by default: we never guess a price.
    prices_per_million: Mapping[str, tuple[Decimal, Decimal]] = field(default_factory=dict)

    def model_for(self, role: ModelRole) -> str:
        return self.models.get(role, DEFAULT_MODEL)

    @classmethod
    def from_env(cls) -> "GeminiSettings":
        models = {
            role: os.environ[f"GEMINI_MODEL_{role.name}"]
            for role in ModelRole
            if f"GEMINI_MODEL_{role.name}" in os.environ
        }
        return cls(
            api_key=os.environ.get("GEMINI_API_KEY", ""),
            models=models,
            fallback_model=os.environ.get("GEMINI_FALLBACK_MODEL", DEFAULT_FALLBACK),
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


class GeminiClient:
    def __init__(
        self,
        settings: GeminiSettings,
        *,
        sdk_client: Any = None,  # the SDK client, or a test stand-in
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,  # backoff jitter
    ) -> None:
        if not settings.api_key:
            raise ModelError(ModelErrorCode.AUTH)  # fail closed: no key, no model
        self._settings = settings
        self._sdk = sdk_client or genai.Client(api_key=settings.api_key)
        self._sleep = sleep
        self._jitter = jitter

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
                await self._sleep(ceiling * (0.5 + self._jitter() / 2))
        raise AssertionError("unreachable")

    async def _call(self, model: str, request: ModelRequest) -> ModelResponse:
        started = time.monotonic()
        try:
            reply = await asyncio.wait_for(
                self._sdk.aio.models.generate_content(
                    model=model, contents=_contents(request), config=_config(request)
                ),
                timeout=request.timeout_seconds,
            )
        except TimeoutError:
            raise ModelError(ModelErrorCode.TIMEOUT) from None
        except errors.APIError as exc:
            raise ModelError(_map_status(int(exc.code))) from None
        except httpx.TimeoutException:
            raise ModelError(ModelErrorCode.TIMEOUT) from None
        except httpx.TransportError:
            raise ModelError(ModelErrorCode.UNAVAILABLE) from None
        return self._convert(reply, model, int((time.monotonic() - started) * 1000))

    def _convert(
        self, reply: types.GenerateContentResponse, model: str, latency_ms: int
    ) -> ModelResponse:
        candidates = reply.candidates or []
        if not candidates:
            raise ModelError(ModelErrorCode.INVALID_RESPONSE)
        candidate = candidates[0]
        finish = getattr(candidate.finish_reason, "value", str(candidate.finish_reason or "STOP"))
        if finish in _BLOCKED:
            raise ModelError(ModelErrorCode.INVALID_RESPONSE)
        parts = candidate.content.parts if candidate.content and candidate.content.parts else []
        text = "".join(p.text for p in parts if p.text and not p.thought) or None
        calls = [
            ToolCall(name=p.function_call.name or "", arguments=dict(p.function_call.args or {}))
            for p in parts
            if p.function_call
        ]
        if text is None and not calls:
            raise ModelError(ModelErrorCode.INVALID_RESPONSE)
        meta = reply.usage_metadata
        tokens_in = (meta.prompt_token_count or 0) if meta else 0
        tokens_out = (
            ((meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)) if meta else 0
        )
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


def _contents(request: ModelRequest) -> list[types.Content]:
    return [
        types.Content(
            role="user" if m.role == "user" else "model", parts=[types.Part(text=m.content)]
        )
        for m in request.messages
    ]


def _config(request: ModelRequest) -> types.GenerateContentConfig:
    tools = None
    if request.tools:
        tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=t.name, description=t.description, parameters_json_schema=t.parameters
                    )
                    for t in request.tools
                ]
            )
        ]
    return types.GenerateContentConfig(
        system_instruction=request.system,
        temperature=request.temperature,
        max_output_tokens=request.max_output_tokens,
        response_mime_type="application/json" if request.output_schema else None,
        response_json_schema=request.output_schema,
        tools=tools,
        # We execute tools ourselves, through the policy gateway. The SDK must never do it.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        http_options=types.HttpOptions(timeout=int(request.timeout_seconds * 1000)),
    )
