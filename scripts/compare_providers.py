#!/usr/bin/env python
"""Прогнать один и тот же стиль через несколько моделей и сложить рядом.

    PYTHONPATH=. .venv/bin/python scripts/compare_providers.py \
        --styles golden_hour,anime_look --tier cheap --sources 3 --budget 3
    PYTHONPATH=. .venv/bin/python scripts/compare_providers.py --styles anime_look \
        --providers openrouter:openai/gpt-image-2,openrouter:black-forest-labs/flux.2-pro

Смысл в том, чтобы сравнение было замером, а не впечатлением: один промпт,
одни исходники, результаты в одной папке с ценой и временем в имени файла.
Сравнивать разные промпты на разных лицах бессмысленно — так получается
вкусовщина.

Кандидат записывается как `openrouter:google/gemini-3-pro-image` — строка
реестра и конкретная модель витрины через двоеточие. Вызов идёт прямо в адаптер — мимо реестра и мимо фолбэка. Для замера
это принципиально: тихая подмена исполнителя, когда один вендор отказал, а
картинку молча сделал другой, превращает сравнение в фикцию.

База данных не нужна: стиль читается из `content/styles/` — того же каталога,
из которого импорт наполняет базу. Текст промпта поэтому ровно тот, что уедет
в продакшен, а замер не зависит от поднятого Postgres.

Результаты кладутся в `docs/research/runs/<стиль>-<кандидат>/`, рядом с
исходником, из которого сделаны. Папка не попадает в git: картинки тяжёлые,
а выводы всё равно переносятся в PROVIDERS.md руками.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.services import content_gen
from app.services.generation import registry
from app.services.generation.operations import GenerationRequest, Operation

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content" / "styles"
RUNS = ROOT / "docs" / "research" / "runs"

# Прикидка цены за кадр для тех, кто не возвращает факт. У витрины цена
# приходит в ответе (`usage.cost`), и тогда в имя файла попадает она —
# точнее прайса, умноженного на догадку о числе токенов.
PRICE_PER_IMAGE = {
    "openai_images": 0.04,
    "pollinations": 0.0,
}

# Шорт-лист по ценовым сегментам, чтобы прогон запускался одной командой, а не
# копированием списка моделей из переписки.
#
# В список попали только те, кто принимает референсный снимок: моделью без
# `input_references` обещание «ты в этом стиле» не выполнить, и её незачем
# смотреть глазами.
TIERS: dict[str, list[str]] = {
    "cheap": [
        "openrouter:recraft/recraft-v4.1",              # $0.035
        "openrouter:bytedance-seed/seedream-4.5",       # $0.04
        "openrouter:qwen/qwen-image-3-pro",             # $0.04
        "openrouter:black-forest-labs/flux.2-pro",      # $0.03/Мп
    ],
    "mid": [
        "openrouter:bytedance-seed/seedream-5-0-pro",   # $0.045–0.09
        "openrouter:google/gemini-3.1-flash-image",     # ≈$0.067
        "openrouter:black-forest-labs/flux.2-max",      # $0.07/Мп
    ],
    "expensive": [
        "openrouter:google/gemini-3-pro-image",         # ≈$0.134
        "openrouter:sourceful/riverflow-v2.5-pro",      # $0.13–0.17
        "openrouter:openai/gpt-image-2",                # ≈$0.03–0.13
    ],
}


@dataclass(slots=True)
class FileStyle:
    """Стиль, прочитанный из каталога, а не из базы.

    Сравнению нужен ровно `prompt_template` — тот же словарь, который импорт
    кладёт в базу из `style.json` и `prompt.md`. Читать его прямо из файлов
    честнее: замер перестаёт зависеть от поднятого Postgres и от того, успели
    ли прогнать импорт, а текст берётся тот же самый, что уедет в продакшен.
    """

    id: str
    prompt_template: dict


def load_style(style_id: str) -> FileStyle | None:
    """Найти стиль по id в любом из направлений каталога."""
    for meta_path in sorted(CONTENT.glob(f"*/{style_id}/style.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        text = (meta_path.parent / "prompt.md").read_text(encoding="utf-8")
        # Всё до разделителя — заметки для человека, модель получает хвост.
        if "\n---\n" in text:
            text = text.split("\n---\n", 1)[1]
        return FileStyle(
            id=style_id,
            prompt_template={
                "text": text.strip(),
                **({"anchor": meta["anchor"]} if meta.get("anchor") else {}),
                **({"subject": meta["subject"]} if meta.get("subject") else {}),
            },
        )
    return None


def pick_sources(limit: int, only: list[str] | None = None) -> list[Path]:
    """Разные лица, а не один и тот же человек в шести стилях.

    По умолчанию берём по одному исходнику из разных направлений: у них разные
    возраст, пол и свет, и это ближе к тому, с чем придут люди.

    Когда исходники названы явно (`--faces`), правило про одно направление не
    работает: у питомцев все снимки лежат в одной папке, и разбавлять их
    человеческими лицами бессмысленно — сохранение окраса и морды это другая
    задача, чем сохранение лица.
    """
    if only:
        found = []
        for style_id in only:
            match = sorted(CONTENT.glob(f"*/{style_id}/source-1.jpg"))
            if not match:
                print(f"Нет исходника для {style_id!r}.")
                continue
            found.append(match[0])
        return found

    found = []
    seen_categories: set[str] = set()
    for path in sorted(CONTENT.glob("*/*/source-1.jpg")):
        category = path.parent.parent.name
        if category in seen_categories:
            continue
        seen_categories.add(category)
        found.append(path)
        if len(found) >= limit:
            break
    return found


def parse_contender(raw: str) -> tuple[str, str | None]:
    """`openrouter` → модель по умолчанию, `openrouter:модель` → конкретная."""
    provider, _, model = raw.partition(":")
    return provider.strip(), (model.strip() or None)


def folder_name(style: str, provider: str, model: str | None, tag: str = "") -> str:
    """Имя папки, по которому видно, что именно сравнивали.

    В идентификаторах моделей есть слэши — в пути это были бы вложенные
    каталоги, и результаты разъехались бы по дереву вместо одного ряда.

    `tag` разводит прогоны одной и той же модели с разными настройками. Без
    него развёртка по ступеням складывала бы все кадры в одну папку, и
    отличить их можно было бы только по цене в имени — то есть никак, когда
    цена совпала.
    """
    tail = model.replace("/", "--") if model else provider
    return f"{style}-{tail}{'-' + tag if tag else ''}"


async def run_one(style: FileStyle, provider: str, model: str | None,
                  source: Path, out: Path) -> tuple[str, float]:
    prompt, negative = content_gen.build_style_prompt(style)
    request = GenerationRequest(
        operation=Operation.IMAGE_TO_IMAGE,
        prompt=prompt,
        negative_prompt=negative,
        image=source.read_bytes(),
        image_mime="image/jpeg",
    )

    adapter = registry.adapter_for(provider)
    if adapter is None:
        return f"{source.parent.name}: нет адаптера для {provider!r}", 0.0

    started = time.monotonic()
    try:
        request.validate()
        result = await adapter.run(request, model=model)
    except Exception as exc:  # noqa: BLE001 — один упавший вендор не должен рвать прогон
        return f"{source.parent.name}: ОТКАЗ — {str(exc)[:160]}", 0.0

    seconds = time.monotonic() - started
    # Цена: факт от провайдера, если он его сказал, иначе прайс.
    price = result.cost_usd
    if price is None:
        price = PRICE_PER_IMAGE.get(result.provider_id, 0.0)
    (out / f"{source.parent.name}--{seconds:.0f}s--{price:.4f}usd.jpg").write_bytes(result.data)
    shutil.copy2(source, out / f"{source.parent.name}--source.jpg")
    return f"{source.parent.name}: {result.model}, {seconds:.0f} с, ${price:.4f}", price


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--styles", required=True,
                        help="id стилей из каталога через запятую")
    parser.add_argument("--providers", default="",
                        help="через запятую: `openrouter` или `openrouter:вендор/модель`")
    parser.add_argument("--tier", choices=sorted(TIERS),
                        help="готовый шорт-лист сегмента вместо --providers")
    parser.add_argument("--sources", type=int, default=3, help="сколько лиц прогнать")
    parser.add_argument("--faces", default="",
                        help="id стилей, чьи source-1.jpg взять, вместо автоподбора")
    parser.add_argument("--quality", default="",
                        help="ступень качества там, где она есть: low, medium, high")
    parser.add_argument("--resolution", default="",
                        help="ступень разрешения там, где она есть: 512, 1K, 2K, 4K")
    parser.add_argument("--tag", default="",
                        help="приписка к имени папки, чтобы развести прогоны одной модели")
    parser.add_argument("--budget", type=float, default=0.0,
                        help="остановиться, потратив столько долларов (0 — без предела)")
    args = parser.parse_args()

    # Ступень задаётся на прогон, а не правкой настроек: в одном сравнении она
    # должна быть одна и та же у всех, иначе замер сравнивает не модели, а
    # бюджеты. Ручка у вендоров разная — у семейства GPT это `quality`
    # (сколько токенов потратить), у Gemini `resolution` (какого размера кадр),
    # — но смысл один: чем выше, тем дороже и дольше.
    if args.quality:
        settings.openrouter_quality = args.quality
    if args.resolution:
        settings.openrouter_resolution = args.resolution

    raw = args.providers or (",".join(TIERS[args.tier]) if args.tier else "openrouter_gpt")
    contenders = [parse_contender(p) for p in raw.split(",") if p.strip()]

    faces = [f.strip() for f in args.faces.split(',') if f.strip()]
    sources = pick_sources(args.sources, faces)
    if not sources:
        print("Нет исходников: положи source-1.jpg рядом со стилями.")
        return 1

    styles = []
    for style_id in (s.strip() for s in args.styles.split(",") if s.strip()):
        style = load_style(style_id)
        if style is None:
            print(f"Стиль {style_id!r} не найден в каталоге.")
            return 1
        styles.append(style)

    spent = 0.0
    for style in styles:
        for provider, model in contenders:
            if args.budget and spent >= args.budget:
                print(f"\nБюджет ${args.budget:.2f} исчерпан, остановился.")
                break
            out = RUNS / folder_name(style.id, provider, model, args.tag)
            out.mkdir(parents=True, exist_ok=True)
            print(f"\n{style.id} × {model or provider} → {out.relative_to(ROOT)}")
            # Последовательно по кандидатам, параллельно по лицам: так у
            # каждого вендора свой ряд, и на его лимиты не влияет соседний.
            for line, price in await asyncio.gather(
                *(run_one(style, provider, model, src, out) for src in sources)
            ):
                print("  ", line)
                spent += price

    print(f"\nПотрачено ${spent:.2f}.")
    print(f"Готово. Смотреть глазами: {RUNS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
