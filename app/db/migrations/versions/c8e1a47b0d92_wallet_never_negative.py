"""кошелёк не уходит в минус

Сетка под блокировкой строки, а не вместо неё.

Гонку в списании чинит `for_update` в репозитории кошелька: без него два
одновременных запроса читали один остаток и оба записывали результат своего
вычитания — на балансе в пятнадцать монет при цене пятнадцать проходили все
пять запросов. Это исправлено в коде.

Но исправлено оно в ОДНОМ месте, а списывать деньги может любой новый путь,
который завтра напишут. Блокировку там забудут — это вопрос времени, а не
аккуратности. Проверка в базе стоит под всеми путями сразу и не зависит от
того, помнил ли автор нового кода про блокировку.

Отрицательный остаток при этом не «маловероятен, но допустим»: он невозможен
по смыслу. Значит и запрещать его должна база, а не соглашение.

Revision ID: c8e1a47b0d92
Revises: a3f81d5e7c92
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "c8e1a47b0d92"
down_revision: Union[str, None] = "a3f81d5e7c92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Если в базе уже есть минус — он оттого же гонка, и накатывать проверку
    # поверх него нельзя молча. Пусть миграция упадёт: разобраться с чужими
    # деньгами вручную правильнее, чем округлить их до нуля автоматически.
    op.create_check_constraint(
        "ck_wallet_free_not_negative", "wallet_balances", "free_balance >= 0"
    )
    op.create_check_constraint(
        "ck_wallet_sub_not_negative", "wallet_balances", "sub_balance >= 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_wallet_sub_not_negative", "wallet_balances", type_="check")
    op.drop_constraint("ck_wallet_free_not_negative", "wallet_balances", type_="check")
