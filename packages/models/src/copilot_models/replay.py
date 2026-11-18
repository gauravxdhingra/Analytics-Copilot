"""Record real model calls once, then replay them in CI and evals so runs are free and exact."""

import json
import os
from pathlib import Path
from typing import Any

from copilot_models.errors import ModelError, ModelErrorCode
from copilot_models.port import ModelClient, ModelRequest, ModelResponse, request_hash


def _read(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text())) if path.exists() else {}


class RecordingClient:
    """Wraps a live client and appends each exchange to a cassette file."""

    def __init__(self, inner: ModelClient, path: Path) -> None:
        self._inner = inner
        self._path = path

    async def generate(self, request: ModelRequest) -> ModelResponse:
        response = await self._inner.generate(request)
        entries = _read(self._path)
        entries[request_hash(request)] = {
            "request": request.model_dump(mode="json"),
            "response": response.model_dump(mode="json"),
        }
        temporary = self._path.with_suffix(".tmp")
        # Sorted keys and a fixed layout keep cassette diffs reviewable.
        temporary.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, self._path)
        return response


class ReplayClient:
    """Serves recorded responses. A request with no recording fails the run."""

    def __init__(self, path: Path) -> None:
        self._entries = _read(path)
        self._used: set[str] = set()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        key = request_hash(request)
        entry = self._entries.get(key)
        if entry is None:
            # The hash identifies the request without exposing its text.
            raise ModelError(ModelErrorCode.REPLAY_MISS, key[:19])
        self._used.add(key)
        recorded = ModelResponse.model_validate(entry["response"])
        return recorded.model_copy(update={"latency_ms": 0})  # replay is deterministic

    def unused(self) -> list[str]:
        """Recordings nothing asked for: stale entries to prune."""
        return sorted(set(self._entries) - self._used)
