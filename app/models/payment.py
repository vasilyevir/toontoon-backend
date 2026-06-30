from __future__ import annotations

from enum import Enum
from typing import Optional

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
    """Wallet balance shown in the UI.

    For magic-link users ``available`` is the local TEKI balance.
    For Boostyfi users:
      - ``available``       = how much the user authorised to spend in Arteki
                              (= grant_remaining). This is what the UI shows and
                              what the pre-flight check uses.
      - ``locked``          = vesting/locked IMBA (informational only).
      - ``grant_cap``       = total spending cap the user set when connecting.
      - ``grant_expires_at``= ISO-8601 datetime when the grant expires (or None).
    """

    available: int
    locked: int = 0
    grant_cap: int = 0
    grant_expires_at: Optional[str] = None
