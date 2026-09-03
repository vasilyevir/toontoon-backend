"""политика контента: вердикт зрения на снимке и флаг проверенного публичного лица

`media_assets.screening` — что увидело зрение на снимке (обнажёнка, ребёнок,
публичное лицо), чтобы платить за проверку один раз на файл.
`users.verified_public_figure` — поддержка проверила, что публичный человек
это он сам; ставится только руками.

Revision ID: a9c4e1f27b30
Revises: f2a6c19d84b3
Create Date: 2026-09-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a9c4e1f27b30"
down_revision = "f2a6c19d84b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_assets", sa.Column("screening", postgresql.JSONB(), nullable=True))
    op.add_column("users", sa.Column("verified_public_figure", sa.Boolean(),
                                     nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "verified_public_figure")
    op.drop_column("media_assets", "screening")
