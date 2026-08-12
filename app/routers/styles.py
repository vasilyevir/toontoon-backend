"""Catalogue endpoints: the home showcase, a style card, and today's Shots.

Replaces ``/api/tiles``. The top level is the six directions from onboarding
(CH-12/CH-14) — the old Image / Postcard / Video described what came out, these
describe what the person came for.

The catalogue itself is empty until styles are written against the chosen
generation model (CH-19): an endpoint that honestly returns nothing is better
than one padded with placeholders nobody can generate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m
from app.db.repositories import styles as styles_repo
from app.db.session import get_session as get_db_session
from app.deps import Context, optional_context
from app.routers.onboarding import CATEGORIES

router = APIRouter(prefix="/api", tags=["catalog"])

# Titles are English: the interface ships in English (CH-12).
CATEGORY_TITLES = {
    "ai_photo_studio": "AI Photo Studio",
    "artistic_touch": "Artistic Touch",
    "cartoon_me": "Cartoon Me",
    "fantasy_mode": "Fantasy Mode",
    "pet_magic": "Pet Magic",
    "family_fun": "Family Fun",
}


class StyleOut(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    category: str
    operation: str
    # What the launch screen must ask for. The client builds the screen from
    # this, which is what lets a new operation ship without a new screen (CH-21).
    input_spec: dict
    cost: int
    examples: list[str] = []


class CategoryOut(BaseModel):
    id: str
    title: str
    styles: list[StyleOut]


def _style_out(row: m.Style) -> StyleOut:
    examples = (row.examples or {}).get("media_ids", []) if isinstance(row.examples, dict) else []
    return StyleOut(
        id=row.id,
        title=row.title,
        description=row.description,
        category=row.category,
        operation=row.operation,
        input_spec=row.input_spec or {},
        cost=row.cost,
        examples=[f"/api/media/{mid}" for mid in examples],
    )


async def _liked_categories(db: AsyncSession, ctx: Optional[Context]) -> list[str]:
    if ctx is None:
        return []
    prefs = await db.get(m.UserPreferences, ctx[0].id)
    return list(prefs.liked_categories or []) if prefs else []


@router.get("/styles", response_model=list[CategoryOut])
async def list_styles(
    ctx: Optional[Context] = Depends(optional_context),
    db: AsyncSession = Depends(get_db_session),
) -> list[CategoryOut]:
    """The whole catalogue, grouped by direction.

    Directions marked ♥ during onboarding come first; inside a direction the
    order is the one we set by hand.
    """
    liked = await _liked_categories(db, ctx)
    ordering = [c for c in CATEGORIES if c in liked] + [c for c in CATEGORIES if c not in liked]

    result: list[CategoryOut] = []
    for category in ordering:
        rows = await styles_repo.list_styles(db, category=category)
        result.append(
            CategoryOut(
                id=category,
                title=CATEGORY_TITLES.get(category, category),
                styles=[_style_out(r) for r in rows],
            )
        )
    return result


@router.get("/styles/home", response_model=list[StyleOut])
async def home_styles(
    ctx: Optional[Context] = Depends(optional_context),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(default=8, ge=1, le=24),
) -> list[StyleOut]:
    """The showcase: a few strong examples, not a grid of everything (CH-14)."""
    liked = await _liked_categories(db, ctx)
    rows = await styles_repo.list_styles(db, home_only=True, limit=limit * 3)
    ranked = styles_repo.rank_for(rows, liked)[:limit]
    return [_style_out(r) for r in ranked]


@router.get("/styles/{style_id}", response_model=StyleOut)
async def get_style(style_id: str, db: AsyncSession = Depends(get_db_session)) -> StyleOut:
    row = await styles_repo.get(db, style_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown style")
    return _style_out(row)


@router.get("/shots/daily", response_model=list[StyleOut])
async def daily_shots(
    db: AsyncSession = Depends(get_db_session),
    day: Optional[str] = Query(default=None, description="YYYY-MM-DD (UTC), по умолчанию сегодня"),
    count: int = Query(default=6, ge=1, le=12),
) -> list[StyleOut]:
    """Today's set. Same for everyone, reproducible for any past date."""
    target = (
        datetime.strptime(day, "%Y-%m-%d").date()
        if day
        else datetime.now(timezone.utc).date()
    )
    rows = await styles_repo.daily_shots(db, target, count=count)
    return [_style_out(r) for r in rows]
