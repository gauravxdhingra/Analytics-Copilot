import pytest
from copilot_tracing.decisions import InMemoryDecisionStore, redact_for_store


def test_text_is_redacted_before_it_is_stored() -> None:
    text = "Refund to DE89370400440532013000 please. Invoice 8841."
    cleaned = redact_for_store(text)
    assert "DE89370400440532013000" not in cleaned and "[IBAN" in cleaned
    assert "Invoice 8841" in cleaned


def test_records_are_keyed_by_trace_and_span_and_never_overwritten() -> None:
    store = InMemoryDecisionStore()
    store.save("t1", "s1", "prompt", "Question about DE89370400440532013000")
    store.save("t1", "s1", "completion", "Answer")
    store.save("t1", "s2", "prompt", "Another")
    assert [r.kind for r in store.for_span("s1")] == ["prompt", "completion"]
    assert "DE89370400440532013000" not in store.for_span("s1")[0].content
    assert len(store.for_trace("t1")) == 3


def test_kinds_are_restricted() -> None:
    with pytest.raises(ValueError, match="kind"):
        InMemoryDecisionStore().save("t", "s", "secret", "x")


def test_oversized_content_is_truncated_with_a_marker() -> None:
    store = InMemoryDecisionStore(max_chars=50)
    store.save("t", "s", "completion", "x" * 500)
    stored = store.for_span("s")[0].content
    assert len(stored) <= 80 and "truncated" in stored
