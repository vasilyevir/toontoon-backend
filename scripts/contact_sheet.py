#!/usr/bin/env python
"""Собрать контактный лист по результатам прогона — одно лицо, все кандидаты.

    PYTHONPATH=. .venv/bin/python scripts/contact_sheet.py --style golden_hour

Сравнение делается глазами, а глазами сравнивают рядом. Разложенные по папкам
кадры для этого не годятся: между двумя вкладками память не удерживает форму
причёски, а именно там кроется главный дефект. Лист кладёт исходник первым и
подписывает каждый кадр моделью, временем и ценой.

Один лист — одно лицо: так вопрос «тот же это человек или нет» остаётся
единственным, на который надо ответить.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "docs" / "research" / "runs"
SHEETS = RUNS / "_sheets"

CELL = 460
LABEL_H = 34
COLUMNS = 4
BG = (16, 16, 18)
FG = (238, 238, 238)


def cells_for(style: str, face: str) -> list[tuple[str, Path]]:
    """Все кадры этого лица в этом стиле: (подпись, файл). Исходник первым."""
    found: list[tuple[str, Path]] = []
    source: Path | None = None

    for folder in sorted(RUNS.glob(f"{style}-*")):
        model = folder.name[len(style) + 1:].replace("--", "/")
        for path in sorted(folder.glob(f"{face}--*usd.jpg")):
            # Имя несёт замер: <лицо>--[провайдер--]<секунды>s--<цена>usd.jpg.
            # Кусок с провайдером остался от прогонов до перехода на витрину —
            # берём два последних поля, они на месте в обоих вариантах.
            seconds, price = path.stem.split("--")[-2:]
            found.append((f"{model}  {seconds}  ${price[:-3]}", path))
        if source is None:
            candidate = folder / f"{face}--source.jpg"
            source = candidate if candidate.exists() else None

    if source is not None:
        found.insert(0, ("ИСХОДНИК", source))
    return found


def build(style: str, face: str) -> Path | None:
    cells = cells_for(style, face)
    if not cells:
        return None

    rows = (len(cells) + COLUMNS - 1) // COLUMNS
    sheet = Image.new("RGB", (COLUMNS * CELL, rows * (CELL + LABEL_H)), BG)
    draw = ImageDraw.Draw(sheet)

    for index, (label, path) in enumerate(cells):
        column, row = index % COLUMNS, index // COLUMNS
        x, y = column * CELL, row * (CELL + LABEL_H)

        picture = Image.open(path).convert("RGB")
        # Вписываем целиком, а не обрезаем: обрезка съедает волосы по краям
        # кадра, а именно они здесь и есть предмет спора.
        picture.thumbnail((CELL, CELL), Image.LANCZOS)
        sheet.paste(picture, (x + (CELL - picture.width) // 2,
                              y + (CELL - picture.height) // 2))
        draw.text((x + 8, y + CELL + 9), label[:64], fill=FG)

    SHEETS.mkdir(parents=True, exist_ok=True)
    out = SHEETS / f"{style}--{face}.jpg"
    sheet.save(out, quality=88)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--style", required=True)
    parser.add_argument("--faces", default="",
                        help="через запятую; по умолчанию все найденные")
    args = parser.parse_args()

    faces = [f.strip() for f in args.faces.split(",") if f.strip()]
    if not faces:
        faces = sorted({p.name.split("--")[0]
                        for folder in RUNS.glob(f"{args.style}-*")
                        for p in folder.glob("*usd.jpg")})

    for face in faces:
        out = build(args.style, face)
        print(f"  {out.relative_to(ROOT)}" if out else f"  {face}: нет кадров")
    return 0


if __name__ == "__main__":
    sys.exit(main())
