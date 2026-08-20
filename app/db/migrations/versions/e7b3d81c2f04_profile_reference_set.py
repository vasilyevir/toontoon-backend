"""Опорный набор профиля: какие снимки идут в кадр.

Хранить пятнадцать и отдавать пятнадцать — разные решения. Все снимки нужны,
чтобы было из чего выбирать (и чтобы завтра хватило на обучение личной модели),
а в запрос уходит отобранное: набор, покрывающий человека — анфас, три четверти,
другое выражение, другой свет, — без повторов.

Revision ID: e7b3d81c2f04
Revises: d4a2c9b71e58
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e7b3d81c2f04"
down_revision = "d4a2c9b71e58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "person_profiles",
        sa.Column("reference_ids", postgresql.JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("person_profiles", "reference_ids")
