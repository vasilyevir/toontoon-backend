"""ключ идемпотентности у заказа

Приложение повторяет запрос, не дождавшись ответа на плохой связи, — и до этого
ключа повтор был неотличим от нового заказа. Комментарий в самой ручке про это
уже был: «человек жал Try again и платил второй раз за то же самое». Половину
починили тем, что запрос стал возвращаться сразу, а кадр уехал в фон; вторую
половину закрывает ключ.

Уникальный индекс частичный: у старых заказов ключа нет и не будет, а `NULL` в
уникальном индексе Postgres не считает совпадением — но частичность делает это
явным, а не полагается на тонкость поведения.

Revision ID: e4b7c93af218
Revises: d9f3b21c6e07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4b7c93af218"
down_revision: Union[str, None] = "d9f3b21c6e07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("generations", sa.Column("idempotency_key", sa.String(64), nullable=True))
    op.create_index(
        "uq_generations_idempotency", "generations", ["user_id", "idempotency_key"],
        unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_generations_idempotency", table_name="generations")
    op.drop_column("generations", "idempotency_key")
