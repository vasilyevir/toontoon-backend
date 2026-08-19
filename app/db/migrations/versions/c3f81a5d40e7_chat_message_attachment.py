"""Снимок как сообщение: у строки переписки появляется вложение.

Приложенная фотография была сообщением только на экране: в базе для неё не было
места, и после перезапуска в треде оставалась пустота там, где человек её видел.
Держать её в поле `content` ссылкой нельзя — это поле уезжает модели как текст
реплики, и она читала бы `/api/media/med_…` как то, что человек сказал.

Revision ID: c3f81a5d40e7
Revises: b7c1e4d92a30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3f81a5d40e7"
down_revision = "b7c1e4d92a30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("media_id", sa.String(length=40), nullable=True))
    op.create_foreign_key(
        "fk_chat_messages_media", "chat_messages", "media_assets", ["media_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_chat_messages_media", "chat_messages", type_="foreignkey")
    op.drop_column("chat_messages", "media_id")
