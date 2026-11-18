from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CurrencyMismatchError(ValueError):
    """Raised when amounts in different currencies are combined."""


class Money(BaseModel):
    """An exact amount in one ISO 4217 currency. Never summed across currencies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("amount", mode="before")
    @classmethod
    def _no_binary_floats(cls, value: Any) -> Any:  # raw input, validated next
        # A float has already lost precision before we see it, so refuse it outright.
        if isinstance(value, float | bool):
            raise ValueError("amount must be a Decimal, int or numeric string, not float or bool")
        return value

    @field_validator("amount")
    @classmethod
    def _finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("amount must be finite")
        return value

    def __add__(self, other: "Money") -> "Money":
        self._require_same_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._require_same_currency(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def _require_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(f"cannot combine {self.currency} with {other.currency}")
