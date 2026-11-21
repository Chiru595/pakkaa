"""Payment providers. This build ships only a simulated provider; every result it produces is labelled simulated."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CreatedLink:
    provider: str
    provider_ref: str
    public_token: str


@dataclass(frozen=True)
class PayoutResult:
    provider: str
    provider_ref: str
    status: str


class PaymentProvider(Protocol):
    name: str

    def create_link(self, amount_paise: int, reference: str, description: str) -> CreatedLink: ...

    def payout(self, amount_paise: int, idempotency_key: str, beneficiary_ref: str) -> PayoutResult: ...


class SimulatedProvider:
    """Behaves like a payment gateway without moving money: links are paid from Pakkaa's simulated pay page."""

    name = "simulated"

    def create_link(self, amount_paise: int, reference: str, description: str) -> CreatedLink:
        if amount_paise <= 0:
            raise ValueError("amount must be positive")
        return CreatedLink(self.name, f"sim_link_{secrets.token_hex(10)}", secrets.token_urlsafe(24))

    def payout(self, amount_paise: int, idempotency_key: str, beneficiary_ref: str) -> PayoutResult:
        if amount_paise <= 0:
            raise ValueError("amount must be positive")
        return PayoutResult(self.name, f"sim_payout_{secrets.token_hex(10)}", "paid")


_provider: PaymentProvider = SimulatedProvider()


def get_payment_provider() -> PaymentProvider:
    return _provider
