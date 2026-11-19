"""The semantic catalogue: per-store meaning, grain, mandatory filters, money and joins."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Owner(_Strict):
    business: str
    steward: str


class ColumnDef(_Strict):
    name: str
    type: str  # as the warehouse reports it (information_schema data_type)
    description: str = ""
    sensitivity: Literal["public", "internal", "restricted"] = "internal"
    untrusted: bool = False  # free text from outside: wrapped as data, never as instructions


class RequiredFilter(_Strict):
    """A filter every query must apply unless the question explicitly asks otherwise."""

    column: str
    value: bool | str | int
    waived_by: Literal["history", "cancelled"] | None = None


class MoneyField(_Strict):
    column: str
    currency_column: str
    sign: str = ""
    # Filtering one of these to a single value pins the currency (an account holds one).
    pinned_by: list[str] = Field(default_factory=list)


class Relationship(_Strict):
    name: str
    to: str
    join_on: list[tuple[str, str]]  # (column here, column there)
    cardinality: Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]


class Store(_Strict):
    store: str  # schema.table
    definition: str
    owner: Owner
    grain: list[str]
    columns: list[ColumnDef]
    dates: dict[str, str] = Field(default_factory=dict)
    money: list[MoneyField] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    required_filters: list[RequiredFilter] = Field(default_factory=list)
    synonyms: dict[str, str] = Field(default_factory=dict)
    known_issues: list[str] = Field(default_factory=list)
    example_questions: list[str] = Field(default_factory=list)

    @property
    def schema_name(self) -> str:
        return self.store.split(".", 1)[0]

    @property
    def table(self) -> str:
        return self.store.split(".", 1)[1]

    def column(self, name: str) -> ColumnDef | None:
        return next((c for c in self.columns if c.name == name), None)

    def money_field(self, column: str) -> MoneyField | None:
        return next((m for m in self.money if m.column == column), None)


class Term(_Strict):
    definition: str
    maps_to: str = ""


class Glossary(_Strict):
    terms: dict[str, Term]

    def lookup(self, term: str) -> Term | None:
        return self.terms.get(term.strip().lower())
