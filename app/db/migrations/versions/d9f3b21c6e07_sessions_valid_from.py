"""смена пароля отзывает выданные сессии

Сессии живут в Redis по тридцать дней и переживали смену пароля. То есть
человек, у которого увели аккаунт, менял пароль — а укравший оставался внутри
ещё на месяц. Ровно то, чего смена пароля не должна допускать.

Отметкой времени, а не списком сессий. Список пришлось бы вести при каждом
входе и выходе, он разъезжался бы с TTL Redis, и «выйти везде» зависело бы от
того, ничего ли мы не забыли туда добавить. Отметка разъехаться не может: она
сравнивается с временем выдачи, записанным в самой сессии.

Пустая колонка означает «ничего не отзывали» — так и есть у всех, кто пароль
не менял.

Revision ID: d9f3b21c6e07
Revises: c8e1a47b0d92
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d9f3b21c6e07"
down_revision: Union[str, None] = "c8e1a47b0d92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("sessions_valid_from", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "sessions_valid_from")
