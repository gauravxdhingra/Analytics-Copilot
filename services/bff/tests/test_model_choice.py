"""Which model backs the copilot is decided from the environment, and never by accident."""

import pytest
from bff.model_choice import choose_model
from bff.scripted import ScriptedModel
from copilot_models import Message, ModelError, ModelErrorCode, ModelRequest, ModelRole
from copilot_models.adapters.gemini import GeminiClient
from copilot_models.adapters.groq import GroqClient


def _request() -> ModelRequest:
    return ModelRequest(
        role=ModelRole.ROUTER,
        prompt_version="t",
        system="s",
        messages=[Message(role="user", content="q")],
        timeout_seconds=1,
    )


async def _refuses(model: object) -> ModelErrorCode:
    with pytest.raises(ModelError) as info:
        await model.generate(_request())  # type: ignore[attr-defined]
    return info.value.code


def test_the_scripted_model_is_chosen_only_when_asked_for() -> None:
    assert isinstance(choose_model({"COPILOT_MODEL": "scripted"}), ScriptedModel)
    assert not isinstance(choose_model({"GROQ_API_KEY": "k"}), ScriptedModel)


def test_groq_is_chosen_when_named_and_keyed() -> None:
    env = {"MODEL_PROVIDER": "groq", "GROQ_API_KEY": "k", "GEMINI_API_KEY": "g"}
    assert isinstance(choose_model(env), GroqClient)


def test_gemini_is_chosen_when_named_and_keyed() -> None:
    env = {"MODEL_PROVIDER": "gemini", "GROQ_API_KEY": "k", "GEMINI_API_KEY": "g"}
    assert isinstance(choose_model(env), GeminiClient)


def test_with_no_choice_made_the_cheaper_configured_provider_wins() -> None:
    both = {"GROQ_API_KEY": "k", "GEMINI_API_KEY": "g"}
    assert isinstance(choose_model(both), GroqClient)
    assert isinstance(choose_model({"GEMINI_API_KEY": "g"}), GeminiClient)
    assert isinstance(choose_model({"GROQ_API_KEY": "k"}), GroqClient)


def test_blank_values_count_as_unset() -> None:
    # A template .env has empty lines such as MODEL_PROVIDER= and GEMINI_API_KEY= .
    env = {"MODEL_PROVIDER": "", "GEMINI_API_KEY": "", "GROQ_API_KEY": "k", "COPILOT_MODEL": ""}
    assert isinstance(choose_model(env), GroqClient)


async def test_nothing_configured_fails_closed_with_an_auth_error() -> None:
    assert await _refuses(choose_model({})) is ModelErrorCode.AUTH


async def test_a_named_provider_without_its_key_does_not_fall_back_to_another() -> None:
    # Asking for Groq must not quietly spend money on Gemini because a key was mistyped.
    env = {"MODEL_PROVIDER": "groq", "GEMINI_API_KEY": "g"}
    assert await _refuses(choose_model(env)) is ModelErrorCode.AUTH


async def test_an_unknown_provider_fails_closed() -> None:
    env = {"MODEL_PROVIDER": "nonsense", "GROQ_API_KEY": "k"}
    assert await _refuses(choose_model(env)) is ModelErrorCode.AUTH


def test_the_demo_uses_the_real_model_when_a_key_is_configured() -> None:
    assert isinstance(choose_model({"COPILOT_DEMO": "1", "GROQ_API_KEY": "k"}), GroqClient)


def test_the_demo_falls_back_to_the_canned_model_only_when_there_is_no_key() -> None:
    assert isinstance(choose_model({"COPILOT_DEMO": "1"}), ScriptedModel)
    assert isinstance(choose_model({"COPILOT_DEMO": "1", "GROQ_API_KEY": ""}), ScriptedModel)


def test_forcing_the_canned_model_beats_the_demo_setting() -> None:
    env = {"COPILOT_DEMO": "1", "COPILOT_MODEL": "scripted", "GROQ_API_KEY": "k"}
    assert isinstance(choose_model(env), ScriptedModel)


async def test_without_the_demo_setting_no_key_still_refuses() -> None:
    # Only the demo is allowed to answer with a canned model; a normal start never does.
    assert await _refuses(choose_model({})) is ModelErrorCode.AUTH
