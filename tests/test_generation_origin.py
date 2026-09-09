"""Работа помнит, откуда заказана: из чата или с витрины.

Чат подхватывает при запуске незаконченные работы и без признака подхватывал
чужие — кадр с витрины показывался в чате «рисующимся». Признак лежит в
`request_params["from_chat"]` и отдаётся в обеих формах работы: в списке
истории и в незаконченных заказах стартового снимка.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.routers.app_meta import _pending
from app.routers.generations import _serialize


def _row(**params):
    return SimpleNamespace(
        id="gen_1", operation="image", status="running", prompt="", error=None,
        request_params=params, cost=15, style_id="golden_hour", share_id=None,
        result_media_id=None, source_media_id=None, mask_media_id=None,
        created_at=datetime(2026, 9, 9, tzinfo=timezone.utc), finished_at=None,
        provider_id=None, provider_model=None, user_id="usr_1", deleted_at=None,
    )


def test_serialize_exposes_origin() -> None:
    assert _serialize(_row(from_chat=False))["from_chat"] is False
    assert _serialize(_row(from_chat=True))["from_chat"] is True


def test_old_rows_have_no_origin() -> None:
    assert _serialize(_row())["from_chat"] is None
    assert _pending(_row())["from_chat"] is None


def test_pending_exposes_origin() -> None:
    assert _pending(_row(from_chat=False))["from_chat"] is False
