"""Чем разрешено разбирать присланные байты.

Роутер загрузки проверяет `content_type` — заголовок, который пишет клиент. Он
не ограничивает ничего: декодер Pillow выбирается по СОДЕРЖИМОМУ. Аудит показал,
что TIFF, BMP, TGA, PPM, SGI, DDS и ICNS спокойно разбирались, будучи объявлены
как `image/png`, и в досягаемости оказывался сорок один декодер — те самые, где
живут дыры Pillow (PSD, JPEG2000, FITS, `raw`).

Тест держит два утверждения сразу: список в роутере и список в разборе — это
один и тот же список, и всё остальное отвергается на входе.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_upload_formats.py -q
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app.routers.generate import _ALLOWED_IMAGE_TYPES
from app.storage import images


def собрать(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), (180, 40, 40)).save(buf, format=fmt)
    return buf.getvalue()


@pytest.mark.parametrize("fmt", ["TIFF", "BMP", "TGA", "PPM", "SGI", "DDS"])
def test_a_format_we_never_promised_is_refused(fmt: str):
    """Объявить `image/png` и прислать другое — и есть вся атака.

    Она бесплатна: заголовок пишет клиент. Единственное, что решает, каким
    кодом мы полезем в эти байты, — список форматов при чтении.
    """
    with pytest.raises(Exception):
        images.process(собрать(fmt))


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP", "GIF"])
def test_the_formats_we_did_promise_still_work(fmt: str):
    """Обратная сторона: обещанное обязано проходить.

    Без этой половины предыдущий тест проходил бы и на коде, который не
    принимает вообще ничего.
    """
    out = images.process(собрать(fmt))
    assert out.mime in {"image/png", "image/jpeg"}
    assert out.width == 24 and out.height == 24


def test_the_router_and_the_decoder_agree_on_the_list():
    """Два списка в разных файлах расходятся молча.

    Роутер называет типы (`image/png`), разбор — форматы (`PNG`). Разойдясь,
    они дадут либо отказ на том, что обещано, либо открытую дверь на том, что
    не обещано, — и увидим мы это не раньше жалобы.
    """
    из_роутера = {t.split("/", 1)[1].upper() for t in _ALLOWED_IMAGE_TYPES}
    из_разбора = {f.upper() for f in images.ALLOWED_FORMATS}
    assert из_роутера == из_разбора, (
        f"роутер пускает {sorted(из_роутера)}, а разбирается {sorted(из_разбора)}"
    )


def test_a_bomb_that_fits_the_byte_cap_is_refused_before_decoding():
    """PNG в 36 мегапикселей одного цвета весит килобайты, распакованный — сотни МБ.

    Потолок в байтах его пропускает, порог Pillow между 1× и 2× только
    предупреждает. Отказ обязан случиться по заголовку, до распаковки, —
    иначе память уже съедена к моменту проверки.
    """
    buf = io.BytesIO()
    Image.new("1", (6000, 6000)).save(buf, format="PNG")
    assert len(buf.getvalue()) < 100_000, "образец должен быть маленьким, иначе тест не про то"
    with pytest.raises(images.TooManyPixels):
        images.process(buf.getvalue())
    with pytest.raises(images.TooManyPixels):
        images.preview(buf.getvalue())
    assert images.aspect_of(buf.getvalue()) is None
