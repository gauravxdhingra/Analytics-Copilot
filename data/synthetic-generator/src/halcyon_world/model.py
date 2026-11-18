from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from copilot_schemas import Money
from pydantic import AwareDatetime, BaseModel, ConfigDict


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Entity(_Row):
    entity_id: str
    name: str
    country: str
    currency: str
    timezone: str
    approval_limit: Money


class Bank(_Row):
    bank_id: str
    name: str  # fictional institutions only
    bic: str


class Account(_Row):
    account_id: str
    entity_id: str
    bank_id: str
    iban: str
    currency: str
    opened_on: date
    opening_balance: Money


class Counterparty(_Row):
    counterparty_id: str
    name: str
    country: str


class Ssi(_Row):
    """Standing settlement instruction; one counterparty can have several."""

    ssi_id: str
    counterparty_id: str
    currency: str
    iban: str
    effective_from: date
    effective_to: date | None  # half-open: effective_from <= d < effective_to


class FxRate(_Row):
    rate_date: date
    base: str
    quote: str
    rate: Decimal


class Transaction(_Row):
    transaction_id: str
    version: int
    is_current: bool
    entity_id: str
    account_id: str
    counterparty_id: str
    booking_date: date
    value_date: date
    amount: Money  # signed: negative is an outflow
    status: Literal["booked", "cancelled"]
    reference: str
    remittance_text: str
    beneficiary_iban: str | None  # set on outflows
    loaded_at: AwareDatetime


class Payment(_Row):
    payment_id: str
    transaction_id: str
    transaction_version: int
    entity_id: str
    maker: str
    checker: str
    releaser: str
    created_at: datetime


class DailyBalance(_Row):
    account_id: str
    balance_date: date
    closing: Money


class Document(_Row):
    document_id: str
    title: str
    kind: Literal["policy", "runbook", "bank_guide", "email", "attachment"]
    body: str
    tainted: bool  # true for anything that arrives from outside and must stay inside <untrusted>


class Label(_Row):
    """Ground truth for an injected fault or trap. Evals read these; the system never does."""

    kind: str
    ref_table: Literal["transaction", "payment", "counterparty", "document"]
    ref_id: str
    expected: str
