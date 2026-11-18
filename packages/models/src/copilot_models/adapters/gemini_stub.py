"""A stand-in for the google-genai client, built from the SDK's own response types."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from google.genai import types

from copilot_models.adapters.gemini import GeminiClient, GeminiSettings


def text_response(
    text: str, *, prompt_tokens: int = 1, output_tokens: int = 1, finish: str = "STOP"
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)]),
                finish_reason=types.FinishReason(finish),
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens, candidates_token_count=output_tokens
        ),
    )


def tool_response(name: str, args: dict[str, Any]) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
                ),
                finish_reason=types.FinishReason("STOP"),
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1, candidates_token_count=1
        ),
    )


def empty_response() -> types.GenerateContentResponse:
    return types.GenerateContentResponse(candidates=[])


Step = types.GenerateContentResponse | Exception | Callable[[], Awaitable[Any]]


class _Models:
    def __init__(self, steps: list[Step]) -> None:
        self.steps = list(steps)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> types.GenerateContentResponse:
        self.calls.append(kwargs)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return await step()  # type: ignore[no-any-return]
        return step


class _Aio:
    def __init__(self, models: _Models) -> None:
        self.models = models


class StubSdk:
    def __init__(self, steps: list[Step]) -> None:
        self.models = _Models(steps)
        self.aio = _Aio(self.models)


async def hang() -> types.GenerateContentResponse:
    await asyncio.sleep(10)
    raise AssertionError("unreachable")


def gemini_with(
    steps: list[Step],
    *,
    settings: GeminiSettings | None = None,
    sleeps: list[float] | None = None,
) -> GeminiClient:
    recorded = sleeps if sleeps is not None else []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    return GeminiClient(
        settings or GeminiSettings(api_key="test-key"),
        sdk_client=StubSdk(steps),
        sleep=fake_sleep,
        jitter=lambda: 1.0,
    )
