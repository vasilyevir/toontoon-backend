#!/usr/bin/env python
"""Собрать каталог стилей из папки `content/styles`.

    PYTHONPATH=. .venv/bin/python scripts/import_styles.py          # что будет сделано
    PYTHONPATH=. .venv/bin/python scripts/import_styles.py --apply  # записать

Источник — файлы, а не база: правка промпта живёт в git рядом с кодом, который
её исполняет, и видна в ревью. База получает копию при импорте.

Сухой прогон по умолчанию сознательно: скрипт переписывает витрину, которую
видят все, и «случайно запустил» здесь стоит дороже лишней команды.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy.dialects.postgresql import insert

from app.db import models as m
from app.db.session import connect, disconnect, session_scope
from app.routers.onboarding import CATEGORIES
from app.storage import get_storage
from app.storage.images import process

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content" / "styles"

# Примеры показываются карточкой в 130–170 pt. Полноразмерный кадр в каталоге
# не нужен никому: он утяжеляет и трафик, и память клиента, а видно его ровно
# так же (та же ошибка уже была в онбординге приложения).
EXAMPLE_MAX_PX = 1024

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(slots=True)
class StyleSource:
    id: str
    category: str
    folder: Path
    meta: dict
    prompt: str
    examples: list[Path]
    sources: list[Path]

    @property
    def warnings(self) -> list[str]:
        out = []
        if not self.examples:
            out.append("нет ни одного example-* — в каталоге карточка будет пустой")
        if self.examples and not self.sources:
            out.append("нет source-* — прогон не повторить и честность примера не проверить")
        if not self.prompt.strip():
            out.append("промпт пустой")
        return out


def _read_prompt(path: Path) -> str:
    """Взять из prompt.md то, что уйдёт в модель.

    Всё до разделителя `---` — заметки для человека: чем занят этот стиль, чего
    в промпт писать нельзя. Модель получает только хвост.
    """
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if "\n---\n" in text:
        text = text.split("\n---\n", 1)[1]
    return text.strip()


def collect() -> tuple[list[StyleSource], list[str]]:
    styles: list[StyleSource] = []
    problems: list[str] = []

    if not CONTENT.exists():
        return [], [f"нет папки {CONTENT.relative_to(ROOT)}"]

    for category_dir in sorted(p for p in CONTENT.iterdir() if p.is_dir()):
        category = category_dir.name
        if category not in CATEGORIES:
            problems.append(f"{category}/ — неизвестное направление, пропущено")
            continue

        for folder in sorted(p for p in category_dir.iterdir() if p.is_dir()):
            meta_path = folder / "style.json"
            if not meta_path.exists():
                problems.append(f"{category}/{folder.name} — нет style.json, пропущено")
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except ValueError as exc:
                problems.append(f"{category}/{folder.name} — style.json не читается: {exc}")
                continue

            def _images(prefix: str) -> list[Path]:
                return sorted(
                    p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
                    and p.stem.startswith(prefix)
                )

            styles.append(StyleSource(
                id=folder.name,
                category=category,
                folder=folder,
                meta=meta,
                prompt=_read_prompt(folder / "prompt.md"),
                examples=_images("example"),
                sources=_images("source"),
            ))

    return styles, problems


def _fit(data: bytes) -> bytes:
    """Ужать пример под размер карточки и отдать JPEG.

    `process()` снимает метаданные, но размер не трогает — он рассчитан на
    пользовательские загрузки, где оригинал нужен целиком. Здесь наоборот:
    кадр показывается карточкой, и полноразмерный только утяжеляет.
    """
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(data)) as img:
        img = img.convert("RGB")
        img.thumbnail((EXAMPLE_MAX_PX, EXAMPLE_MAX_PX), Image.LANCZOS)
        out = BytesIO()
        img.save(out, format="JPEG", quality=88, optimize=True)
    return out.getvalue()


async def _store_examples(style: StyleSource) -> list[str]:
    """Положить примеры в хранилище, вернуть ключи в порядке файлов."""
    storage = get_storage()
    keys: list[str] = []
    for index, path in enumerate(style.examples, start=1):
        # Сначала снимаем метаданные (в кадрах бывает геометка съёмки), потом
        # ужимаем под карточку.
        cleaned = process(path.read_bytes(), make_thumbnail=False)
        data = _fit(cleaned.data)
        # Отпечаток содержимого в имени: адрес примера обязан меняться вместе
        # с картинкой. Пока ключ был просто «1.jpg», новый кадр ложился по
        # старому адресу, а клиент держал в кэше прежний — на витрине сутки
        # висело то, что мы уже заменили.
        digest = hashlib.sha256(data).hexdigest()[:10]
        key = f"catalog/{style.category}/{style.id}/{index}-{digest}.jpg"
        await storage.put(key, data, content_type="image/jpeg")
        keys.append(key)
    return keys


async def apply(styles: list[StyleSource]) -> None:
    async with session_scope() as session:
        for style in styles:
            keys = await _store_examples(style)
            meta = style.meta
            values = dict(
                id=style.id,
                title=meta.get("title") or style.id,
                description=meta.get("description"),
                category=style.category,
                # Раздел про фотографию — это всегда редактирование снимка:
                # человек приходит со своим лицом, а не за картинкой с нуля.
                operation="image_to_image",
                input_spec={"needs_photo": True, "prompt_editable": False},
                cost=int(meta.get("cost", 1)),
                # `anchor` — необязательный: по умолчанию фото-путь берёт
                # реалистичный якорь, но «Cartoon Me» и «Artistic Touch»
                # обещают ровно обратное, и им нужен свой.
                prompt_template={
                    "text": style.prompt,
                    **({"anchor": meta["anchor"]} if meta.get("anchor") else {}),
                    # «person» или «pet»: от этого зависит, что именно просят
                    # сохранить — лицо и телосложение или окрас и морду.
                    **({"subject": meta["subject"]} if meta.get("subject") else {}),
                    # Кем исполнять этот стиль. Необязательно: по умолчанию
                    # решает приоритет в реестре.
                    **({"provider": meta["provider"]} if meta.get("provider") else {}),
                    # Форма кадра этого стиля. Необязательно: без неё берётся
                    # `9:16` — вертикаль под экран телефона.
                    #
                    # Понадобилось, когда четыре карточки в «AI Photo Studio»
                    # показывали квадратный пример, а делали вертикальный кадр:
                    # карточка обещала одно, кадр приходил другой. Форма — часть
                    # обещания, как и палитра, и решать её должен стиль, а не
                    # умолчание, до которого никто не дотягивался.
                    **({"aspect": meta["aspect"]} if meta.get("aspect") else {}),
                },
                examples={"keys": keys},
                sort_order=int(meta.get("sort_order", 100)),
                is_home=bool(meta.get("is_home", False)),
                is_shot=bool(meta.get("is_shot", False)),
                is_active=bool(meta.get("is_active", True)),
            )
            stmt = insert(m.Style).values(**values)
            # Повторный импорт — это способ обновить витрину, поэтому
            # перезаписываем всё, кроме счётчика использований.
            stmt = stmt.on_conflict_do_update(
                index_elements=[m.Style.id],
                set_={k: v for k, v in values.items() if k != "id"},
            )
            await session.execute(stmt)


def report(styles: list[StyleSource], problems: list[str]) -> None:
    for line in problems:
        print(f"  ! {line}")
    for style in styles:
        flags = []
        if style.meta.get("is_home"):
            flags.append("главная")
        if style.meta.get("is_shot"):
            flags.append("shots")
        print(f"  {style.category}/{style.id}: примеров {len(style.examples)}, "
              f"исходников {len(style.sources)}, промпт {len(style.prompt)} симв."
              + (f" → {', '.join(flags)}" if flags else " → нигде не показывается"))
        for warning in style.warnings:
            print(f"      ! {warning}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="записать в базу и хранилище")
    args = parser.parse_args()

    styles, problems = collect()
    report(styles, problems)

    if not styles:
        print("\nНечего импортировать.")
        return 1

    if not args.apply:
        print(f"\nСухой прогон. Стилей: {len(styles)}. Записать — с флагом --apply.")
        return 0

    await connect()
    try:
        await apply(styles)
    finally:
        await disconnect()
    print(f"\nЗаписано стилей: {len(styles)}.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
