#!/usr/bin/env python
"""Сколько опорных снимков брать в кадр: один, три или пять.

    PYTHONPATH=. .venv/bin/python scripts/eval_references.py --faces путь/к/снимкам

Зачем: три референса на фотографическом пути были верой, а не замером. Цифру
взяли по аналогии с рисованным путём, где вред от трёх доказан, — но «вредно
там» не значит «полезно здесь», и решение о том, сколько лиц уходит в каждый
запрос, стоит денег на каждой генерации.

Меряем то единственное, ради чего референсы и нужны: узнаётся ли человек.
Судья щедр — он почти всем ставит десять, — поэтому смотрим не на абсолют, а на
разницу между вариантами и на провалы: сходство ниже семи это уже «кто-то
другой». Опорный снимок для сравнения в запрос не уходит ни в одном варианте,
иначе мы сравнивали бы кадр с его же исходником.

Сравнение честное только при одинаковой просьбе: сцена, стиль и пропорции у
всех трёх вариантов одни и те же, меняется одно число.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import statistics
import sys
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import eval_frames as ef  # noqa: E402  — судья и вспомогательное живут там

BASE = ef.BASE
OUT = pathlib.Path("docs/research/runs/_refs")

# Одна и та же просьба на все варианты. Фотографический путь: рисованный
# стилизует лицо и тем самым прячет потерю сходства.
CASE = {
    "type": "image",
    "prompt": "сфотографируй меня на вечерней улице, тёплый свет витрин, поясной портрет",
    "style": "realistic",
    "aspect": "4:5",
    "roles_chosen": True,
}
# Что сравниваем. Пять оказалось не лучше трёх на первом же прогоне, поэтому на
# следующих лицах вариант можно и сузить: сравнивать стоит там, где разница
# была, а была она между одним и тремя.
COUNTS = (1, 3, 5)


async def upload(client: httpx.AsyncClient, headers: dict, path: pathlib.Path) -> str:
    files = {"file": (path.name, path.read_bytes(), "image/jpeg")}
    r = await client.post(f"{BASE}/api/uploads", headers=headers, files=files)
    r.raise_for_status()
    d = r.json()
    return d.get("url") or f"/api/media/{d.get('media_id') or d.get('id')}"


async def shoot(client: httpx.AsyncClient, headers: dict, urls: list[str]) -> dict:
    body = dict(CASE, photo_url=urls[0], extra_photo_urls=urls[1:])
    started = time.monotonic()
    r = await client.post(f"{BASE}/api/generate", headers=headers, json=body)
    if r.status_code != 200:
        return {"error": f"{r.status_code} {r.text[:160]}"}
    d = r.json()
    frame = await client.get(f"{BASE}{d['url']}", headers=headers)
    return {"id": d["id"], "bytes": frame.content, "seconds": time.monotonic() - started}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--faces", type=pathlib.Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--counts", default=",".join(str(c) for c in COUNTS),
                        help="сколько референсов сравнивать, через запятую")
    args = parser.parse_args()

    counts = tuple(int(c) for c in args.counts.split(",") if c.strip())
    faces = sorted(p for p in args.faces.glob("*.jpg"))
    if len(faces) < max(counts) + 1:
        raise SystemExit(
            f"Нужно хотя бы {max(counts) + 1} снимков, нашёл {len(faces)}. "
            "Последний по алфавиту уходит в опорные и в запрос не попадает.")
    # Последний — опорный для судьи, в запрос не уходит.
    reference, pool = faces[-1].read_bytes(), faces[:-1]

    OUT.mkdir(parents=True, exist_ok=True)
    # Метка до секунд и имя набора в папке.
    #
    # До минут её хватало ровно до первой пары проб, запущенных разом: две
    # параллельные записались в одну папку, имена кадров совпали, и вторая
    # затёрла первую. Заметно это стало только по отрицательным числам —
    # опорный кадр одного человека сравнивался с кадрами другого.
    out = OUT / f"{time.strftime('%Y-%m-%d-%H%M%S')}-{args.faces.name}"
    out.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=600) as client:
        headers = await ef.guest(client)
        urls = [await upload(client, headers, p) for p in pool[:max(counts)]]
        print(f"Загружено {len(urls)} снимков, опорный для сравнения — {faces[-1].name}\n")

        for attempt in range(args.repeats):
            for count in counts:
                shot = await shoot(client, headers, urls[:count])
                if "error" in shot:
                    print(f"  {count} реф. #{attempt + 1}: отказ {shot['error']}")
                    rows.append({"count": count, "attempt": attempt + 1,
                                 "error": shot["error"]})
                    continue
                path = out / f"{count}ref-{attempt + 1}.jpg"
                path.write_bytes(shot["bytes"])
                score, why = await ef.judge_likeness(reference, shot["bytes"])
                provider, model = await ef.provider_of(shot["id"])
                rows.append({"count": count, "attempt": attempt + 1, "likeness": score,
                             "sharpness": round(ef.sharpness(shot["bytes"]), 1),
                             "seconds": round(shot["seconds"], 1), "model": model,
                             "why": why[:120], "path": str(path)})
                print(f"  {count} реф. #{attempt + 1}: сходство {score} | "
                      f"резкость {rows[-1]['sharpness']:7.1f} | {shot['seconds']:5.1f} c | {model}")

    print("\nПо количеству референсов:")
    for count in counts:
        # `if r.get(...)` здесь однажды выбрасывал из статистики нули — то есть
        # ровно те кадры, ради которых замер и делается. Ноль это оценка, а не
        # отсутствие оценки, и отличать их надо явным `is not None`.
        mine = [r for r in rows if r["count"] == count and r.get("likeness") is not None]
        scores = [r["likeness"] for r in mine]
        sharps = [r["sharpness"] for r in mine]
        secs = [r["seconds"] for r in mine]
        if not scores:
            print(f"  {count}: ни одного кадра")
            continue
        low = sum(1 for s in scores if s < 7)
        print(f"  {count} реф.: сходство медиана {statistics.median(scores):.1f} "
              f"(разброс {min(scores)}–{max(scores)}), ниже семи: {low}/{len(scores)} | "
              f"резкость {statistics.median(sharps):8.1f} | {statistics.median(secs):5.1f} c")

    (out / "run.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    (out / ".gitignore").write_text("*.jpg\n")
    print(f"\nКадры и числа: {out}")


if __name__ == "__main__":
    asyncio.run(main())
