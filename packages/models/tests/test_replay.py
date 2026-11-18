import json
from pathlib import Path
from typing import Any

import pytest
from copilot_models import (
    FakeClient,
    Message,
    ModelError,
    ModelErrorCode,
    ModelRequest,
    ModelResponse,
    RecordingClient,
    ReplayClient,
    Usage,
    request_hash,
)


def _response(text: str) -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=[],
        usage=Usage(input_tokens=3, output_tokens=2, cost_usd=None),
        model="m",
        latency_ms=123,
        finish_reason="stop",
    )


def test_the_hash_ignores_the_timeout_but_not_the_content(request_: ModelRequest) -> None:
    base = request_hash(request_)
    assert request_hash(request_.model_copy(update={"timeout_seconds": 99.0})) == base
    changed: list[dict[str, Any]] = [
        {"system": "Different system prompt."},
        {"messages": [Message(role="user", content="Another question")]},
        {"temperature": 0.5},
        {"prompt_version": "router@2"},
        {"output_schema": {"type": "object"}},
        {"max_output_tokens": 10},
    ]
    for update in changed:
        assert request_hash(request_.model_copy(update=update)) != base, update
    assert base.startswith("sha256:")


async def test_record_then_replay_is_exact_and_free_of_latency(
    tmp_path: Path, request_: ModelRequest
) -> None:
    path = tmp_path / "c.json"
    await RecordingClient(FakeClient([_response("answer")]), path).generate(request_)
    replayed = await ReplayClient(path).generate(request_)
    assert replayed.text == "answer"
    assert replayed.usage.input_tokens == 3
    assert replayed.latency_ms == 0  # replay is deterministic, so no recorded timing


async def test_a_miss_fails_the_run_without_echoing_the_prompt(
    tmp_path: Path, request_: ModelRequest
) -> None:
    path = tmp_path / "c.json"
    await RecordingClient(FakeClient([_response("a")]), path).generate(request_)
    other = request_.model_copy(update={"system": "SECRET-PROMPT-TEXT"})
    with pytest.raises(ModelError) as info:
        await ReplayClient(path).generate(other)
    assert info.value.code is ModelErrorCode.REPLAY_MISS
    assert "SECRET-PROMPT-TEXT" not in str(info.value)
    assert request_hash(other)[:19] in str(info.value)


async def test_cassette_files_are_stable_for_clean_diffs(
    tmp_path: Path, request_: ModelRequest
) -> None:
    first, second = tmp_path / "1.json", tmp_path / "2.json"
    for path in (first, second):
        await RecordingClient(FakeClient([_response("a")]), path).generate(request_)
    assert first.read_text() == second.read_text()
    data = json.loads(first.read_text())
    assert list(data) == sorted(data)


async def test_unused_entries_are_reported_so_stale_cassettes_get_pruned(
    tmp_path: Path, request_: ModelRequest
) -> None:
    path = tmp_path / "c.json"
    other = request_.model_copy(update={"temperature": 0.9})
    recorder = RecordingClient(FakeClient([_response("a"), _response("b")]), path)
    await recorder.generate(request_)
    await recorder.generate(other)
    replay = ReplayClient(path)
    await replay.generate(request_)
    assert replay.unused() == [request_hash(other)]


async def test_recording_merges_with_an_existing_cassette(
    tmp_path: Path, request_: ModelRequest
) -> None:
    path = tmp_path / "c.json"
    other = request_.model_copy(update={"temperature": 0.9})
    await RecordingClient(FakeClient([_response("a")]), path).generate(request_)
    await RecordingClient(FakeClient([_response("b")]), path).generate(other)
    assert len(json.loads(path.read_text())) == 2


async def test_fake_records_requests_and_runs_dry_loudly(request_: ModelRequest) -> None:
    fake = FakeClient([_response("one")])
    await fake.generate(request_)
    assert fake.requests == [request_]
    with pytest.raises(ModelError) as info:
        await fake.generate(request_)
    assert info.value.code is ModelErrorCode.REPLAY_MISS


async def test_fake_can_script_an_error(request_: ModelRequest) -> None:
    fake = FakeClient([ModelError(ModelErrorCode.RATE_LIMITED)])
    with pytest.raises(ModelError) as info:
        await fake.generate(request_)
    assert info.value.code is ModelErrorCode.RATE_LIMITED
