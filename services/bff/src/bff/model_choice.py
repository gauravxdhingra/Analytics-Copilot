"""Which model backs the copilot, decided from the environment and never by accident.

Order of precedence: COPILOT_MODEL=scripted (forces a canned model, for browser tests), then
MODEL_PROVIDER (groq or gemini), then whichever provider has a key, cheapest first. COPILOT_DEMO
(set by `make demo`) only adds one thing: with no key at all, answer with the canned model instead
of refusing, so the demo still runs. With a key it uses the real model. A provider that was named
but has no key does not fall back to another one: nothing spends money that was not asked for.
With nothing usable the copilot refuses to answer (fail closed).
"""

import logging
from collections.abc import Mapping

from copilot_models import ModelClient, ModelError, ModelErrorCode, ModelRequest, ModelResponse
from copilot_models.resilient import resilient
from copilot_models.selection import select_live_model

_log = logging.getLogger("bff")


class NoModel:
    """Every call fails with a mapped auth error: no key, no answer."""

    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise ModelError(ModelErrorCode.AUTH)


def choose_model(env: Mapping[str, str]) -> ModelClient:
    def given(name: str) -> str:
        return env.get(name) or ""

    if given("COPILOT_MODEL") == "scripted":
        from bff.scripted import ScriptedModel

        _log.warning("COPILOT_MODEL=scripted: answers use a canned model, not a real one")
        return ScriptedModel()

    live = select_live_model(env)
    if live is None and given("COPILOT_DEMO"):
        from bff.scripted import ScriptedModel

        _log.warning("demo without a model key: answers use a canned model, not a real one")
        return ScriptedModel()
    if live is None:
        _log.warning("no usable model provider: asking will be refused")
        return NoModel()
    return resilient(live)  # an outage fails fast instead of waiting out every timeout
