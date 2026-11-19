from copilot_models.adapters.gemini import GeminiClient
from copilot_models.adapters.groq import GroqClient
from copilot_models.selection import select_live_model


def test_the_named_provider_is_used() -> None:
    both = {"GROQ_API_KEY": "k", "GEMINI_API_KEY": "g"}
    assert isinstance(select_live_model({**both, "MODEL_PROVIDER": "groq"}), GroqClient)
    assert isinstance(select_live_model({**both, "MODEL_PROVIDER": "gemini"}), GeminiClient)


def test_with_no_name_the_cheaper_keyed_provider_wins() -> None:
    assert isinstance(select_live_model({"GROQ_API_KEY": "k", "GEMINI_API_KEY": "g"}), GroqClient)
    assert isinstance(select_live_model({"GEMINI_API_KEY": "g"}), GeminiClient)


def test_nothing_usable_is_none_never_a_silent_other_provider() -> None:
    assert select_live_model({}) is None
    assert select_live_model({"MODEL_PROVIDER": "groq", "GEMINI_API_KEY": "g"}) is None
    assert select_live_model({"MODEL_PROVIDER": "nonsense", "GROQ_API_KEY": "k"}) is None
    assert select_live_model({"MODEL_PROVIDER": "", "GROQ_API_KEY": ""}) is None
