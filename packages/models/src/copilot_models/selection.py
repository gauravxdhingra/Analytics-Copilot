"""Choose a live model provider from the environment. Shared by the app and the eval harness.

MODEL_PROVIDER (groq or gemini) names one. With none named, whichever provider has a key is used,
cheapest first. A provider that was named but has no key does not fall back to another one: nothing
spends money that was not asked for. Returns None when nothing is usable; the caller fails closed.
"""

import logging
from collections.abc import Mapping

from copilot_models.port import ModelClient

_log = logging.getLogger("copilot_models")


def select_live_model(env: Mapping[str, str]) -> ModelClient | None:
    def given(name: str) -> str:
        return env.get(name) or ""  # a blank line in an .env template counts as unset

    keyed = [
        name for name, var in (("groq", "GROQ_API_KEY"), ("gemini", "GEMINI_API_KEY")) if given(var)
    ]
    provider = given("MODEL_PROVIDER").lower() or (keyed[0] if keyed else "")
    if provider == "groq" and given("GROQ_API_KEY"):
        from copilot_models.adapters.groq import GroqClient, GroqSettings

        groq = GroqSettings.from_env(env)
        _log.info("model provider: groq (%s)", groq.default_model)
        return GroqClient(groq)
    if provider == "gemini" and given("GEMINI_API_KEY"):
        from copilot_models.adapters.gemini import GeminiClient, GeminiSettings

        gemini = GeminiSettings.from_env(env)
        _log.info("model provider: gemini (%s)", gemini.default_model)
        return GeminiClient(gemini)
    _log.warning("no usable model provider (MODEL_PROVIDER=%r)", provider)
    return None
