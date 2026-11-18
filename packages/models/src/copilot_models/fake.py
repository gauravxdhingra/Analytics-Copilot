from collections import deque

from copilot_models.errors import ModelError, ModelErrorCode
from copilot_models.port import ModelRequest, ModelResponse


class FakeClient:
    """Scripted responses for unit tests. Running out is a loud failure, not a default answer."""

    def __init__(self, script: list[ModelResponse | ModelError]) -> None:
        self._script = deque(script)
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._script:
            raise ModelError(ModelErrorCode.REPLAY_MISS, "fake script exhausted")
        step = self._script.popleft()
        if isinstance(step, ModelError):
            raise step
        return step
