from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class PaymentStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    FAILED = "failed"  # returned by Boostyfi when a reserve expires (payment_expired)


class Payment(BaseModel):
    """Result of reserving funds for a generation (two-phase payment).

    For magic-link users this is backed by the local TEKI balance; for Boostify
    users it mirrors a Boostify payment.
    """

    payment_id: str
    status: PaymentStatus
    amount: int


class Balance(BaseModel):
    """Wallet balance shown in the UI."""

    available: int
    locked: int = 0
