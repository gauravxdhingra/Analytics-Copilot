"""A canned model for demos and browser tests, selected only by COPILOT_MODEL=scripted.

It plans one fixed, still-verified query. The numbers and rows come from the real stack as the
signed-in user; only the language model is replaced. It answers by the request's role, never by
position in a script, so a run that stops early cannot put the next one out of step.
"""

from copilot_models import ModelError, ModelErrorCode, ModelRequest, ModelResponse, ModelRole
from orchestrator.testing import COUNT_PLAN, narrate, plan, route


class ScriptedModel:
    async def generate(self, request: ModelRequest) -> ModelResponse:
        if request.role is ModelRole.ROUTER:
            return route()
        if request.role is ModelRole.PLANNER:
            return plan(COUNT_PLAN)
        if request.role is ModelRole.NARRATIVE:
            return narrate("There are some booked transactions.")
        raise ModelError(ModelErrorCode.REPLAY_MISS, "no scripted answer for this role")
