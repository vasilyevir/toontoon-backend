"""Unified wallet over the two auth providers.

* magic-link users: balance is the local ``teki_balance`` field; the two-phase
  payment is emulated (reserve = decrement, confirm = no-op, cancel = refund).
* Boostify users: balance is read live from Boostify; payments go through the
  real ``create / confirm / cancel`` endpoints.

Edge case — payment_expired on confirm (Boostify only):
  Video generation takes 4–5 minutes; Boostify's reserve TTL is 5 minutes.
  If the video completes just after the TTL, ``confirm`` returns
  ``payment_expired``. In that case we immediately create a new payment and
  confirm it (re-charge). If the new reserve also fails (e.g. user spent their
  balance in the meantime) we log a warning and skip — the user gets the
  already-rendered video for free. This is a rare edge case and the acceptable
  trade-off avoids blocking the pipeline.

  ``cancel`` with ``payment_expired`` is treated as a no-op: Boostify already
  returned the cap, nothing more to do.

Routers only ever talk to this module, so they don't care which provider backs
the wallet.
"""
from __future__ import annotations

import logging
import time

from app.models.payment import Balance, Payment, PaymentStatus
from app.models.session import Session
from app.models.user import AuthProvider, User
from app.services import auth_service, boostify

logger = logging.getLogger("arteki.wallet")


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
        return await boostify.create_payment(token, amount=amount, reason=reason)

    # Magic-link: decrement immediately; refund on cancel.
    user.teki_balance -= amount
    await auth_service.save_user(user)
    return Payment(payment_id=f"local_{user.id}_{int(time.time()*1000)}", status=PaymentStatus.PENDING, amount=amount)


async def confirm(user: User, session: Session, payment: Payment) -> None:
    """Phase 2a — generation succeeded; charge the user.

    For Boostify users: if the 5-minute reserve TTL elapsed while the video was
    rendering, ``payment_expired`` is raised by Boostify. We immediately create
    a fresh reserve + confirm (re-charge). If the user no longer has enough
    balance we log a warning and skip — they get the video without being charged
    (rare edge case, acceptable trade-off).
    """
    if user.provider == AuthProvider.BOOSTIFY:
        token = await ensure_boostify_token(session)
        try:
            await boostify.confirm_payment(token, payment.payment_id)
        except boostify.PaymentExpiredError:
            logger.warning(
                "Payment %s expired before confirm (video took > 5 min). "
                "Attempting immediate re-charge for user %s, amount=%d.",
                payment.payment_id, user.id, payment.amount,
            )
            # Re-read balance; the cap was already returned by Boostify.
            try:
                balance = await get_balance(user, session)
                if balance.available >= payment.amount:
                    # Infer the original reason from the amount.
                    reason = "arteki:video_generate" if payment.amount >= 2 else "arteki:image_generate"
                    fresh = await boostify.create_payment(token, amount=payment.amount, reason=reason)
                    await boostify.confirm_payment(token, fresh.payment_id)
                    logger.info("Re-charge successful for user %s, new payment %s", user.id, fresh.payment_id)
                else:
                    logger.warning(
                        "Re-charge skipped: user %s has only %d TEKI (need %d). "
                        "Video delivered without charge.",
                        user.id, balance.available, payment.amount,
                    )
            except Exception:
                logger.exception(
                    "Re-charge attempt failed for user %s payment %s",
                    user.id, payment.payment_id,
                )
    # Magic-link: balance already decremented in reserve(); nothing to do.


async def cancel(user: User, session: Session, payment: Payment) -> None:
    """Phase 2b — generation failed; release the reserved funds.

    For Boostify users: if the reserve already auto-expired, Boostify returns
    ``payment_expired`` / ``payment_not_pending`` — the cap was already returned
    automatically, so there is nothing for us to do.
    """
    if user.provider == AuthProvider.BOOSTIFY:
        token = await ensure_boostify_token(session)
        try:
            await boostify.cancel_payment(token, payment.payment_id)
        except boostify.PaymentExpiredError:
            # Already auto-cancelled by Boostify; cap was returned. No-op.
            logger.debug("Cancel no-op: payment %s already expired for user %s", payment.payment_id, user.id)
        return
    # Magic-link: refund.
    user.teki_balance += payment.amount
    await auth_service.save_user(user)
