"""Во что кадр обошёлся нам, и был ли повтор.

`cost` в этой таблице — TOONTOON, списанные с человека. Что мы заплатили
исполнителю, не записывалось нигде, а повтор («приехала фотография там, где
просили рисунок») живёт внутри той же работы: человек платит один раз, мы —
дважды. Вопрос «сколько стоит упрямство модели» нельзя было даже задать.

Revision ID: a3f81d5e7c92
Revises: f1c62a09d3b7
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3f81d5e7c92"
down_revision = "f1c62a09d3b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generations", sa.Column("provider_cost_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("generations", "provider_cost_usd")
