"""Transaction history (proxied from Boostify for v2 users)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import Context, required_context
from app.models.user import AuthProvider
from app.services import boostify, generations_service, wallet

router = APIRouter(prefix="/api", tags=["payments"])


@router.get("/transactions")
async def transactions(ctx: Context = Depends(required_context)) -> list[dict]:
    """Generation history merged with cost.

    For Boostify users we pull the authoritative transaction list from Boostify.
    For magic-link users we synthesise it from the local generation history.
    """
    user, session = ctx

    if user.provider == AuthProvider.BOOSTIFY:
        token = await wallet.ensure_boostify_token(session)
        return await boostify.get_transactions(token)

    generations = await generations_service.list_for_user(user.id)
    return [
        {
            "payment_id": gen.payment_id,
            "type": gen.type.value,
            "prompt": gen.prompt,
            "amount": -gen.cost,
            "created_at": gen.created_at.isoformat(),
        }
        for gen in generations
        if gen.payment_id
    ]
