"""The number check: every figure in the narrative must belong in the answer, with its currency."""

import pytest
from orchestrator.answer import AnswerTable
from orchestrator.narrative import numbers_are_grounded


def _table(
    columns: list[str], rows: list[list[object]], *, as_of: str | None = "2026-06-30T23:00:00+00:00"
) -> AnswerTable:
    return AnswerTable(
        columns=columns, rows=rows, row_count=len(rows), truncated=False, as_of=as_of
    )


BY_CURRENCY = _table(["currency", "total"], [["EUR", "1250000.00"], ["USD", "980000.00"]])


@pytest.mark.parametrize(
    "text",
    [
        "EUR outflows were 1,250,000.00 and USD outflows were 980,000.",
        "The total was 1250000 EUR, and 980000 USD.",
        "In USD the figure is 980,000.00; in EUR it is 1,250,000.",
    ],
)
def test_figures_with_their_own_currency_are_accepted(text: str) -> None:
    assert numbers_are_grounded(text, "Outflows by currency?", BY_CURRENCY)


@pytest.mark.parametrize(
    "text",
    [
        "USD outflows were 1,250,000.00 and EUR outflows were 980,000.",  # currencies swapped
        "The total in USD was 1250000.",
        "EUR 980000 left the account.",
    ],
)
def test_the_right_number_with_the_wrong_currency_is_rejected(text: str) -> None:
    assert not numbers_are_grounded(text, "Outflows by currency?", BY_CURRENCY)


def test_a_figure_with_no_currency_beside_it_is_judged_on_the_number_alone() -> None:
    assert numbers_are_grounded("The larger total was 1,250,000.", "q", BY_CURRENCY)
    assert not numbers_are_grounded("The larger total was 1,250,001.", "q", BY_CURRENCY)


def test_a_number_that_appears_for_two_currencies_may_be_paired_with_either() -> None:
    table = _table(["currency", "n"], [["EUR", 12], ["GBP", 12]])
    assert numbers_are_grounded("There were 12 in EUR and 12 in GBP.", "q", table)


def test_a_made_up_number_is_rejected() -> None:
    assert not numbers_are_grounded("The total was 999.", "q", BY_CURRENCY)


@pytest.mark.parametrize("date", ["2026-06-30", "2026-05-14"])
def test_a_date_that_is_in_the_data_the_question_or_the_snapshot_is_accepted(date: str) -> None:
    table = _table(["booking_date", "amount"], [["2026-05-14", "10.50"]])
    question = "What happened on 2026-05-14?"
    assert numbers_are_grounded(f"On {date} it was 10.50.", question, table)


def test_a_date_that_is_nowhere_in_the_answer_is_rejected() -> None:
    table = _table(["booking_date", "amount"], [["2026-05-14", "10.50"]])
    assert not numbers_are_grounded("On 2026-07-31 it was 10.50.", "What was the amount?", table)


def test_the_row_count_is_only_a_count_of_rows() -> None:
    table = _table(["n"], [["a"], ["b"]])  # two rows, and no cell holds the number 2
    assert numbers_are_grounded("The query returned 2 rows.", "q", table)
    assert not numbers_are_grounded("That is 2 million euros.", "q", table)


def test_a_number_echoed_from_the_question_is_fine_but_not_as_an_amount() -> None:
    table = _table(["n"], [[776]])
    assert numbers_are_grounded(
        "Of the 5 you asked about, there are 776.", "Show me 5 of them", table
    )
    assert not numbers_are_grounded(
        "There are 776, up 2026 percent.", "How many in June 2026?", table
    )


def test_a_year_from_the_question_is_fine_as_a_year() -> None:
    table = _table(["n"], [[776]])
    assert numbers_are_grounded("In June 2026 there were 776.", "How many in June 2026?", table)


def test_quantities_and_formatting_do_not_matter() -> None:
    table = _table(["amount"], [["-7924295.19"]])
    assert numbers_are_grounded("The outflow was -7,924,295.19.", "q", table)
    assert numbers_are_grounded("The outflow was -7924295.190.", "q", table)
