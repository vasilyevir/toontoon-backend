"""Профили: набор снимков, по которым человек появляется в кадре.

Чтобы не прикладывать своё лицо каждый раз. Профилей может быть несколько —
себя, партнёра, ребёнка, питомца, — и в разговоре достаточно сказать, про кого
речь.

Снимки хранятся списком идентификаторов, а не отдельной таблицей связи: порядок
здесь значим (модели связывают референсы с упоминаниями по очереди), а список
короткий и всегда читается целиком. Таблица связи дала бы соединение на каждый
запрос ради данных, которые и так помещаются в одну строку.

Revision ID: d4a2c9b71e58
Revises: c3f81a5d40e7
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d4a2c9b71e58"
down_revision = "c3f81a5d40e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "person_profiles",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("user_id", sa.String(length=40),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        # person | pet — от этого зависит требование сохранить внешность:
        # «тот же возраст и пол» на кошке инструкция ни о чём.
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="person"),
        sa.Column("media_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        # Кого подставлять, когда человек не сказал, про кого речь.
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_person_profiles_user", "person_profiles", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_person_profiles_user", table_name="person_profiles")
    op.drop_table("person_profiles")
