"""Unified wallet over the two auth providers.

* magic-link users: balance is the local ``teki_balance`` field; the two-phase
  payment is emulated (reserve = decrement, confirm = no-op, cancel = refund).
* Boostify users: balance is read live from Boostify; payments go through the
  real ``create / confirm / cancel`` endpoints.

Routers only ever talk to this module, so they don't care which provider backs
the wallet.
"""
from __future__ import annotations

import time

from app.models.payment import Balance, Payment, PaymentStatus
from app.models.session import Session
from app.models.user import AuthProvider, User
from app.services import auth_service, boostify


class InsufficientFunds(Exception):
    pass


async def ensure_boostify_token(session: Session) -> str:
    """Return a valid Boostify access token, refreshing silently if expired."""
    now = time.time()
    if session.boostify_access_expires_at and session.boostify_access_expires_at <= now:
        if not session.boostify_refresh_token:
            raise RuntimeError("Boostify session has no refresh token")
        tokens = await boostify.refresh(session.boostify_refresh_token)
        session.boostify_access_token = tokens["access_token"]
        session.boostify_refresh_token = tokens.get("refresh_token", session.boostify_refresh_token)
        session.boostify_access_expires_at = now + tokens.get("expires_in", 3600)
        await auth_service.update_session(session)
    if not session.boostify_access_token:
        raise RuntimeError("Boostify session has no access token")
    return session.boostify_access_token


async def get_balance(user: User, session: Session) -> Balance:
    if user.provider == AuthProvider.BOOSTIFY:
        token = await ensure_boostify_token(session)
        return await boostify.get_balance(token)
    return Balance(available=user.teki_balance, locked=0)


async def reserve(user: User, session: Session, *, amount: int, reason: str) -> Payment:
    """Phase 1 — reserve funds before generating. Raises InsufficientFunds."""
    balance = await get_balance(user, session)
    if balance.available < amount:
        raise InsufficientFunds()

    if user.provider == AuthProvider.BOOSTIFY:
        token = await ensure_boostify_token(session)
        return await boostify.create_payment(token, user_id=user.boostify_user_id or user.id, amount=amount, reason=reason)

    # Magic-link: decrement immediately; refund on cancel.
    user.teki_balance -= amount
    await auth_service.save_user(user)
    return Payment(payment_id=f"local_{user.id}_{int(time.time()*1000)}", status=PaymentStatus.PENDING, amount=amount)


async def confirm(user: User, session: Session, payment: Payment) -> None:
    """Phase 2a — generation succeeded."""
    if user.provider == AuthProvider.BOOSTIFY:
        token = await ensure_boostify_token(session)
        await boostify.confirm_payment(token, payment.payment_id)
    # Magic-link: balance already decremented in reserve(); nothing to do.


async def cancel(user: User, session: Session, payment: Payment) -> None:
    """Phase 2b — generation failed; release the reserved funds."""
    if user.provider == AuthProvider.BOOSTIFY:
        token = await ensure_boostify_token(session)
        await boostify.cancel_payment(token, payment.payment_id)
        return
    # Magic-link: refund.
    user.teki_balance += payment.amount
    await auth_service.save_user(user)
