"""style favorites

Закладки в каталоге. Ключ составной — один стиль нельзя сохранить дважды, и
повторное «сохранить» с другого устройства не создаёт дубля, а тихо ничего не
меняет. Это же свойство делает слияние при входе безопасным: оно просто
досылает всё, что накопилось локально.

Revision ID: b7c1e4d92a30
Revises: a096378524b3
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b7c1e4d92a30'
down_revision: Union[str, None] = 'a096378524b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'style_favorites',
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('style_id', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'style_id'),
    )
    # Список закладок всегда читается целиком по одному человеку и в порядке
    # добавления — под это и индекс.
    op.create_index('ix_style_favorites_user', 'style_favorites',
                    ['user_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_style_favorites_user', table_name='style_favorites')
    op.drop_table('style_favorites')
