#!/usr/bin/env python
"""Прогнать один и тот же стиль через несколько провайдеров и сложить рядом.

    PYTHONPATH=. .venv/bin/python scripts/compare_providers.py --style golden_hour
    PYTHONPATH=. .venv/bin/python scripts/compare_providers.py --style cartoon_3d \
        --providers kling,kling_v2 --sources 3

Смысл в том, чтобы сравнение было замером, а не впечатлением: один промпт,
одни исходники, результаты в одной папке с ценой и временем в имени файла.
Сравнивать разные промпты на разных лицах бессмысленно — так получается
вкусовщина.

Результаты кладутся в `docs/research/runs/<стиль>-<провайдер>/`, рядом с
исходником, из которого сделаны. Папка не попадает в git: картинки тяжёлые,
а выводы всё равно переносятся в PROVIDERS.md руками.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import time
from pathlib import Path

from app.db.repositories import styles as styles_repo
from app.db.session import connect, disconnect, session_scope
from app.services import content_gen
from app.services.generation import registry
from app.services.generation.operations import GenerationRequest, Operation

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content" / "styles"
RUNS = ROOT / "docs" / "research" / "runs"

# Прикидка цены за кадр, чтобы прогон сразу показывал стоимость выбора.
# Числа из документации вендоров, август 2026; для точного счёта смотреть
# биллинг, здесь — порядок величины.
PRICE_PER_IMAGE = {
    "kling": 0.028,
    "kling_v2": 0.028,
    "openai_images": 0.04,
    "pollinations": 0.0,
}


def pick_sources(limit: int) -> list[Path]:
    """Разные лица, а не один и тот же человек в шести стилях.

    Берём по одному исходнику из разных направлений: у них разные возраст,
    пол и свет, и это ближе к тому, с чем придут люди.
    """
    found: list[Path] = []
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


async def run_one(session, style, provider: str, source: Path, out: Path) -> str:
    prompt, negative = content_gen.build_style_prompt(style)
    started = time.monotonic()
    try:
        result = await registry.run(
            session,
            GenerationRequest(
                operation=Operation.IMAGE_TO_IMAGE,
                prompt=prompt,
                negative_prompt=negative,
                image=source.read_bytes(),
                image_mime="image/jpeg",
            ),
            prefer=provider,
        )
    except Exception as exc:  # noqa: BLE001 — один упавший вендор не должен рвать прогон
        return f"{source.parent.name}: ОТКАЗ — {str(exc)[:120]}"

    seconds = time.monotonic() - started
    # Если реестр ушёл на фолбэк, в имени будет виден настоящий исполнитель:
    # иначе прогон тихо сравнивал бы один вендор сам с собой.
    price = PRICE_PER_IMAGE.get(result.provider_id, 0.0)
    name = f"{source.parent.name}--{result.provider_id}--{seconds:.0f}s--{price:.3f}usd.jpg"
    (out / name).write_bytes(result.data)
    shutil.copy2(source, out / f"{source.parent.name}--source.jpg")
    return f"{source.parent.name}: {result.provider_id}/{result.model}, {seconds:.0f} с, ${price:.3f}"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--style", required=True, help="id стиля из каталога")
    parser.add_argument("--providers", default="kling",
                        help="через запятую, как в реестре")
    parser.add_argument("--sources", type=int, default=3, help="сколько лиц прогнать")
    args = parser.parse_args()

    sources = pick_sources(args.sources)
    if not sources:
        print("Нет исходников: положи source-1.jpg рядом со стилями.")
        return 1

    await connect()
    try:
        async with session_scope() as session:
            style = await styles_repo.get(session, args.style)
            if style is None:
                print(f"Стиль {args.style!r} не найден.")
                return 1

            for provider in [p.strip() for p in args.providers.split(",") if p.strip()]:
                out = RUNS / f"{args.style}-{provider}"
                out.mkdir(parents=True, exist_ok=True)
                print(f"\n{provider} → {out.relative_to(ROOT)}")
                for line in await asyncio.gather(
                    *(run_one(session, style, provider, src, out) for src in sources)
                ):
                    print("  ", line)
    finally:
        await disconnect()

    print(f"\nГотово. Смотреть глазами: {RUNS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
