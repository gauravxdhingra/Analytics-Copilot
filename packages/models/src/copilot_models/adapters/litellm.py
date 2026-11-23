"""LiteLLM gateway settings for the OpenAI-compatible chat-completions client."""

import os
from collections.abc import Mapping

from copilot_models.adapters.groq import GroqSettings


def litellm_settings(env: Mapping[str, str] | None = None) -> GroqSettings:
    source = os.environ if env is None else env
    return GroqSettings(
        api_key=source.get("LITELLM_API_KEY") or "",
        default_model=source.get("MODEL") or "",
        base_url=source.get("LITELLM_BASE_URL") or "",
        trust_env=False,
        reasoning_effort=None,
        prices_per_million={},
    )
