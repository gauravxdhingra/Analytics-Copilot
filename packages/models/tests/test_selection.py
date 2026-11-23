from copilot_models.adapters.gemini import GeminiClient
from copilot_models.adapters.groq import GroqClient
from copilot_models.adapters.litellm import litellm_settings
from copilot_models.selection import select_live_model


def test_the_named_provider_is_used() -> None:
    both = {"GROQ_API_KEY": "k", "GEMINI_API_KEY": "g"}
    assert isinstance(select_live_model({**both, "MODEL_PROVIDER": "groq"}), GroqClient)
    assert isinstance(select_live_model({**both, "MODEL_PROVIDER": "gemini"}), GeminiClient)


def test_with_no_name_the_cheaper_keyed_provider_wins() -> None:
    assert isinstance(select_live_model({"GROQ_API_KEY": "k", "GEMINI_API_KEY": "g"}), GroqClient)
    assert isinstance(select_live_model({"GEMINI_API_KEY": "g"}), GeminiClient)


def test_litellm_gateway_is_selected_when_configured() -> None:
    gateway = {
        "LITELLM_API_KEY": "k",
        "LITELLM_BASE_URL": "https://gateway.example/",
        "MODEL": "some-model",
    }
    assert isinstance(select_live_model(gateway), GroqClient)
    assert isinstance(select_live_model({**gateway, "MODEL_PROVIDER": "litellm"}), GroqClient)


def test_litellm_requires_key_url_and_model_and_does_not_fall_back() -> None:
    assert select_live_model({"LITELLM_API_KEY": "k", "MODEL": "m"}) is None
    assert select_live_model({"LITELLM_API_KEY": "k", "LITELLM_BASE_URL": "url"}) is None
    assert (
        select_live_model(
            {"MODEL_PROVIDER": "litellm", "LITELLM_API_KEY": "k", "GROQ_API_KEY": "g"}
        )
        is None
    )


def test_litellm_settings_use_gateway_values_without_assuming_model_cost() -> None:
    settings = litellm_settings(
        {
            "LITELLM_API_KEY": "k",
            "LITELLM_BASE_URL": "https://gateway.example/",
            "MODEL": "some-model",
        }
    )
    assert settings.api_key == "k"
    assert settings.base_url == "https://gateway.example/"
    assert settings.default_model == "some-model"
    assert settings.reasoning_effort is None
    assert settings.trust_env is False
    assert settings.prices_per_million == {}


def test_nothing_usable_is_none_never_a_silent_other_provider() -> None:
    assert select_live_model({}) is None
    assert select_live_model({"MODEL_PROVIDER": "groq", "GEMINI_API_KEY": "g"}) is None
    assert select_live_model({"MODEL_PROVIDER": "nonsense", "GROQ_API_KEY": "k"}) is None
    assert select_live_model({"MODEL_PROVIDER": "", "GROQ_API_KEY": ""}) is None
