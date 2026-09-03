"""Пул лиц для съёмки примеров — только отобранные, и никогда питомцы.

Раньше `shoot_examples.py` собирал лица из всех `source-1.jpg` каталога, и в
пул попадали пожилые люди и шесть фотографий кошек и собак из `pet_magic`.
Скрипт не смотрит, кто на снимке: кошка в роли исходника для «Train Station»
ничем не отличалась бы от остальных. В тот раз повезло с порядком.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_faces_pool.py -q
"""
from __future__ import annotations

import hashlib
import io
import pathlib
import re

from PIL import Image

ЛИЦА = pathlib.Path("content/faces")
СТИЛИ = pathlib.Path("content/styles")


def _md5(p: pathlib.Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def test_пул_существует_и_не_пуст():
    файлы = [p for p in ЛИЦА.glob("*.jpg")]
    assert len(файлы) >= 8, "меньше восьми лиц — соседи в ленте начнут совпадать"


def test_каждое_лицо_это_картинка_и_названо_по_правилу():
    for p in ЛИЦА.glob("*.jpg"):
        Image.open(io.BytesIO(p.read_bytes())).verify()
        assert re.fullmatch(r"[wm]\d\d_[a-z_]+\.jpg", p.name), f"имя не по правилу: {p.name}"


def test_женских_не_меньше_чем_мужских():
    """Правило: женские в приоритете, мужскими разбавляем."""
    w = len(list(ЛИЦА.glob("w*.jpg"))); m = len(list(ЛИЦА.glob("m*.jpg")))
    assert w >= m and w > 0


def test_питомцев_в_пуле_нет():
    питомцы = {_md5(p) for p in (СТИЛИ / "pet_magic").glob("*/source-1.*")}
    assert питомцы, "в pet_magic нет исходников — проверка бессмысленна"
    for p in ЛИЦА.glob("*.jpg"):
        assert _md5(p) not in питомцы, f"{p.name} — это фото питомца"


def test_скрипт_берёт_лица_только_из_пула():
    src = pathlib.Path("scripts/shoot_examples.py").read_text()
    assert 'ROOT.parent / "faces"' in src
    assert 'glob("*/*/source-1.*")' not in src, "скрипт снова собирает пул из всего каталога"
