import json

import pytest
from bff.scripted import ScriptedModel
from copilot_models import Message, ModelError, ModelRequest, ModelRole


def _request(role: ModelRole) -> ModelRequest:
    return ModelRequest(
        role=role,
        prompt_version="t",
        system="s",
        messages=[Message(role="user", content="q")],
        timeout_seconds=1,
    )


async def _says(role: ModelRole) -> dict[str, object]:
    reply = await ScriptedModel().generate(_request(role))
    parsed: dict[str, object] = json.loads(reply.text or "")
    return parsed


async def test_each_role_gets_its_own_answer() -> None:
    assert (await _says(ModelRole.ROUTER))["intent"] == "insights"
    assert "plan" in await _says(ModelRole.PLANNER)
    assert "text" in await _says(ModelRole.NARRATIVE)


async def test_a_failed_run_cannot_put_the_next_one_out_of_step() -> None:
    # A fixed sequence breaks when a run stops early. Answering by role does not.
    model = ScriptedModel()
    await model.generate(_request(ModelRole.ROUTER))
    await model.generate(_request(ModelRole.PLANNER))  # the run then dies before narrating
    assert (await model.generate(_request(ModelRole.ROUTER))).text == (
        await model.generate(_request(ModelRole.ROUTER))
    ).text


async def test_a_role_with_no_script_is_refused_not_guessed() -> None:
    with pytest.raises(ModelError):
        await ScriptedModel().generate(_request(ModelRole.EXTRACTOR))
