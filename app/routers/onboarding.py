"""Onboarding answers (CH-12).

The quiz asks for gender and then six directions with ✕ / ♥. The screens live
in the app bundle, but the answers have to reach the server — otherwise the
promise made on the loading screen ("Personalizing your experience") is not
kept by anything.

Answers belong to whoever is signed in, guest included, and travel to the
account on sign-in like everything else.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m
from app.db.session import get_session as get_db_session
from app.deps import Context, required_context

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

# The six directions the quiz offers; also the top level of the catalogue.
# Порядок здесь — это порядок разделов на главной. AI Photo Studio первым:
# это основное обещание продукта, и отдавать первый экран чему-то другому
# значит рассказывать не с того места.
CATEGORIES = (
    "ai_photo_studio",
    "glow_up",
    "paparazzi_flash",
    "polaroid_reunion",
    "artistic_touch",
    "cartoon_me",
    "lifestyle_travel",
    "fantasy_mode",
    "pet_magic",
    "family_fun",
)


class OnboardingAnswers(BaseModel):
    gender: Optional[Literal["female", "male", "other"]] = None
    liked: list[str] = Field(default_factory=list)
    disliked: list[str] = Field(default_factory=list)
    # Which set of questions produced these answers. The directions will change,
    # and without this old answers become unreadable rather than merely old.
    version: str = Field(default="v1", max_length=32)


class OnboardingState(BaseModel):
    gender: Optional[str] = None
    liked: list[str] = Field(default_factory=list)
    disliked: list[str] = Field(default_factory=list)
    version: Optional[str] = None
    completed: bool = False


@router.post("", response_model=OnboardingState)
async def save_answers(
    body: OnboardingAnswers,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> OnboardingState:
    """Store the quiz result. Re-submitting overwrites: people redo onboarding
    after reinstalling, and the later answers are the truer ones."""
    user, _ = ctx
    liked = [c for c in body.liked if c in CATEGORIES]
    disliked = [c for c in body.disliked if c in CATEGORIES]

    values = {
        "user_id": user.id,
        "gender": body.gender,
        "liked_categories": liked,
        "disliked_categories": disliked,
        "onboarding_version": body.version,
        "completed_at": func.now(),
    }
    stmt = insert(m.UserPreferences).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[m.UserPreferences.user_id],
        set_={k: v for k, v in values.items() if k != "user_id"},
    )
    await db.execute(stmt)

    return OnboardingState(
        gender=body.gender, liked=liked, disliked=disliked, version=body.version, completed=True
    )


@router.get("", response_model=OnboardingState)
async def get_answers(
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> OnboardingState:
    user, _ = ctx
    row = await db.get(m.UserPreferences, user.id)
    if row is None:
        return OnboardingState()
    return OnboardingState(
        gender=row.gender,
        liked=list(row.liked_categories or []),
        disliked=list(row.disliked_categories or []),
        version=row.onboarding_version,
        completed=row.completed_at is not None,
    )
