"""Choose a live model provider from the environment. Shared by the app and the eval harness.

MODEL_PROVIDER (groq, gemini or litellm) names one. With none named, whichever provider has a key
is used, with the gateway last. An incomplete named provider does not fall back, so nothing
spends money that was not asked for. Returns None when nothing is usable; the caller fails closed.
"""

import logging
from collections.abc import Mapping

from copilot_models.port import ModelClient

_log = logging.getLogger("copilot_models")


def select_live_model(env: Mapping[str, str]) -> ModelClient | None:
    def given(name: str) -> str:
        return env.get(name) or ""  # a blank line in an .env template counts as unset

    gateway = bool(given("LITELLM_API_KEY") and given("LITELLM_BASE_URL") and given("MODEL"))
    keyed = [
        name for name, var in (("groq", "GROQ_API_KEY"), ("gemini", "GEMINI_API_KEY")) if given(var)
    ] + (["litellm"] if gateway else [])
    provider = given("MODEL_PROVIDER").lower() or (keyed[0] if keyed else "")
    if provider == "litellm" and gateway:
        from copilot_models.adapters.groq import GroqClient
        from copilot_models.adapters.litellm import litellm_settings

        settings = litellm_settings(env)
        _log.info("model provider: litellm (%s)", settings.default_model)
        return GroqClient(settings)
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
