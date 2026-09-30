"""Вес витрины: сколько байтов стоит первый экран.

TSK-38 Андрея (30 сентября 2026): верхние фотографии главной грузятся
медленно. Сервер отдавал их из памяти за миллисекунды — дело было в весе:
двенадцать примеров первого экрана весили 1,2 МБ, и на мобильной сети это
секунды, в которые карточки стоят пустыми.

Здесь проверяется то, что от этого сделано: примеры пережимаются один раз при
чтении, а вместе с каталогом едет крошка — размытая подложка в полкилобайта,
которой карточка заполняется сразу.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_examples_weight.py -q
"""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from app.routers import styles


def кадр(width: int = 576, height: int = 1024, quality: int = 95) -> bytes:
    """Пример витрины, каким его делает генератор: крупный и не ужатый."""
    image = Image.new("RGB", (width, height))
    # Шум, а не заливка: одноцветный кадр сжимается в килобайт, и на нём
    # проверять вес бессмысленно.
    image.putdata([((x * 7) % 256, (y * 5) % 256, (x + y) % 256)
                   for y in range(height) for x in range(width)])
    out = io.BytesIO()
    image.save(out, "JPEG", quality=quality)
    return out.getvalue()


@pytest.fixture(autouse=True)
def чистая_витрина(monkeypatch):
    monkeypatch.setattr(styles, "_examples", styles.OrderedDict())
    monkeypatch.setattr(styles, "_examples_size", 0)
    monkeypatch.setattr(styles, "_blurs", {})


class Хранилище:
    def __init__(self, data: bytes):
        self.data = data
        self.чтений = 0

    async def get(self, key):
        self.чтений += 1
        return self.data


# ── Вес ──────────────────────────────────────────────────────────────────────

async def test_пример_пережимается_и_остаётся_того_же_размера(monkeypatch):
    исходник = кадр()
    monkeypatch.setattr(styles, "get_storage", lambda: Хранилище(исходник))

    отданное = await styles._example_bytes("catalog/x/1-abc.jpg")

    assert len(отданное) < len(исходник) / 1.5, "пережатие должно быть заметным"
    # Размер кадра не трогаем: карточка на главной во всю ширину, и уменьшать
    # его значит показывать мыло.
    assert Image.open(io.BytesIO(отданное)).size == (576, 1024)


async def test_пережимается_один_раз(monkeypatch):
    """Второе чтение берёт готовое из памяти, а не жмёт заново."""
    хранилище = Хранилище(кадр())
    monkeypatch.setattr(styles, "get_storage", lambda: хранилище)

    первое = await styles._example_bytes("catalog/x/1-abc.jpg")
    второе = await styles._example_bytes("catalog/x/1-abc.jpg")

    assert первое is второе and хранилище.чтений == 1


async def test_уже_ужатый_пример_не_раздувается(monkeypatch):
    """Пережатие бывает хуже исходника — тогда отдаём исходник."""
    исходник = кадр(quality=40)
    monkeypatch.setattr(styles, "get_storage", lambda: Хранилище(исходник))

    assert await styles._example_bytes("catalog/x/1-abc.jpg") == исходник


async def test_битая_картинка_витрину_не_роняет(monkeypatch):
    """Не JPEG — отдаём как есть: картинка важнее её веса."""
    monkeypatch.setattr(styles, "get_storage", lambda: Хранилище(b"not an image"))

    assert await styles._example_bytes("catalog/x/1-abc.jpg") == b"not an image"
    assert styles._blurs == {}


# ── Крошка ───────────────────────────────────────────────────────────────────

async def test_крошка_снимается_и_весит_меньше_килобайта(monkeypatch):
    monkeypatch.setattr(styles, "get_storage", lambda: Хранилище(кадр()))

    await styles._example_bytes("catalog/x/1-abc.jpg")

    крошка = styles._blurs["catalog/x/1-abc.jpg"]
    assert крошка.startswith("data:image/jpeg;base64,")
    сырые = base64.b64decode(крошка.split(",", 1)[1])
    assert len(сырые) < 1024, "крошка едет с каталогом — ей нельзя быть тяжёлой"
    assert Image.open(io.BytesIO(сырые)).size == (18, 32)


def test_крошка_попадает_в_каталог_только_прогретая():
    """Не прогрет — не обещаем: клиент нарисует подложку как раньше."""
    row = ПримерСтиля()

    assert styles._style_out(row).blur is None

    styles._blurs["catalog/x/1-abc.jpg"] = "data:image/jpeg;base64,AAA"
    assert styles._style_out(row).blur == "data:image/jpeg;base64,AAA"


def test_крошку_можно_не_прикладывать():
    """Нижним карточкам каталога она не нужна: их увидят не скоро."""
    styles._blurs["catalog/x/1-abc.jpg"] = "data:image/jpeg;base64,AAA"

    assert styles._style_out(ПримерСтиля(), blur=False).blur is None


class ПримерСтиля:
    """Строка стиля — ровно то, что читает `_style_out`."""
    id = "gallery_noir"
    title = "Gallery Noir"
    description = None
    category = "black_and_white"
    operation = "image"
    input_spec: dict = {}
    prompt_template: dict = {}
    cost = 10
    examples = {"keys": ["catalog/x/1-abc.jpg"]}
