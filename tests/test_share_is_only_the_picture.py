"""Публичная ссылка на работу показывает картинку — и ничего сверх неё.

В промпте по замыслу стоят имена из профилей: дети, партнёры. Внутренняя
сериализация их отдаёт владельцу, и это правильно; по ссылке без сессии
их не должно быть, как и `id` работы, цены и текста отказа.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_share_is_only_the_picture.py -q
"""
from datetime import datetime, timezone
from dataclasses import replace
from types import SimpleNamespace

from app.routers.generations import _serialize, _serialize_public

from dataclasses import make_dataclass

_Работа = make_dataclass('_Работа', ['id', 'operation', 'status', 'prompt', 'result_media_id', 'cost', 'style_id', 'share_id', 'error', 'request_params', 'created_at'])
РАБОТА = _Работа(
    id="gen_1", operation="image_to_image", status="done",
    prompt="Маша (дочь) и Иван на пляже", result_media_id="med_9", cost=20,
    style_id="beach_sunset", share_id="shr_1", error=None,
    request_params={"type": "image", "answers": {"кто": "Маша"}},
    created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
)


def test_public_view_has_no_prompt_id_or_cost():
    публично = _serialize_public(РАБОТА)
    assert set(публично) == {"type", "result_url", "thumbnail_url", "style_id", "created_at"}
    assert "Маша" not in repr(публично)


def test_owner_view_still_has_everything_for_a_free_form_work():
    """Обратная сторона: владельцу его собственная просьба нужна — по ней он
    вспоминает, что просил. Это работа без стиля из каталога."""
    своё = _serialize(replace(РАБОТА, style_id=None))
    assert своё["prompt"] == РАБОТА.prompt and своё["id"] == "gen_1"


def test_owner_view_hides_the_catalogue_style_text():
    """У работы по стилю `row.prompt` — текст стиля, написанный руками и
    уходящий в модель. Это продукт, а не просьба человека; владельцу
    показываются его собственные слова, если они были."""
    assert _serialize(РАБОТА)["prompt"] is None
    со_словами = replace(РАБОТА, request_params={**РАБОТА.request_params, "refine_note": "ярче"})
    assert _serialize(со_словами)["prompt"] == "ярче"
