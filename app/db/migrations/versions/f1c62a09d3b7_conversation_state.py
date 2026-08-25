"""Состояние разговора: что человек уже сказал — записью, а не пересказом.

До этого «что мы поняли» жило в тексте последних двадцати реплик и разбиралось
заново каждый ход. Замер: первая фраза с техникой, палитрой и назначением
вылетала из окна после двадцати коротких реплик, и разговор снова спрашивал
то, что ему уже сказали.

Одна строка на человека — как и переписка. «Очистка» её не удаляет, а
обесценивает по `started_at`.

Revision ID: f1c62a09d3b7
Revises: e7b3d81c2f04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f1c62a09d3b7"
down_revision = "e7b3d81c2f04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_states",
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("intent", sa.String(length=32), nullable=True),
        sa.Column("slots", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("conversation_states")
