#!/usr/bin/env python
"""Переснять один пример каталога — своим конвейером, с выбранным лицом.

    PYTHONPATH=. .venv/bin/python scripts/reshoot.py future_me ~/Downloads/лицо.png

Отличается от `shoot_examples.py` тремя вещами, каждая из которых уже стоила
испорченных файлов:

1. **Ждёт готовности.** `/api/generate` отдаёт заказ сразу, а кадр дорисовывает
   в фоне. Скачивать сразу после ответа — значит скачать пустоту.
2. **Не клеит адрес.** Ссылка на кадр приходит абсолютной, когда
   `PUBLIC_BASE_URL` сетевой; приклеенная к базовому она даёт 404.
3. **Снимает поверх существующего.** Тот скрипт пропускает стили, у которых
   пример уже есть, — а здесь задача обратная.

Кадр забирается из хранилища по ключу, а не по ссылке: так надёжнее и не
зависит от того, кому эта ссылка принадлежит. Рядом ложится `source-1.jpg` —
по нему видно, из чего сделан пример, и прогон можно повторить.
"""
import asyncio, io, pathlib, sys
import httpx
from PIL import Image
from sqlalchemy import text

BASE = "http://127.0.0.1:8020"
ROOT = pathlib.Path("content/styles")
ЖДЁМ_СЕКУНД = 400


def папка_стиля(слаг: str) -> pathlib.Path:
    найдено = [d for d in ROOT.glob(f"*/{слаг}") if d.is_dir()]
    if not найдено:
        raise SystemExit(f"нет такого стиля: {слаг}")
    return найдено[0]


async def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    слаг, снимок = sys.argv[1], pathlib.Path(sys.argv[2]).expanduser()
    if not снимок.exists():
        raise SystemExit(f"нет файла: {снимок}")
    папка = папка_стиля(слаг)

    from app.db.session import connect, disconnect, get_factory
    from app.storage import get_storage

    лицо = pathlib.Path(f"/tmp/reshoot-{слаг}.jpg")
    им = Image.open(снимок).convert("RGB")
    им.thumbnail((1280, 1280), Image.LANCZOS)
    им.save(лицо, quality=92)

    async with httpx.AsyncClient(timeout=600) as c:
        r = await c.post(f"{BASE}/api/auth/guest"); r.raise_for_status()
        h = {"Authorization": f"Bearer {r.json()['session_token']}"}
        up = await c.post(f"{BASE}/api/uploads", headers=h,
                          files={"file": (лицо.name, лицо.read_bytes(), "image/jpeg")})
        up.raise_for_status()
        r = await c.post(f"{BASE}/api/generate", headers=h,
                         json={"type": "image", "style_id": слаг,
                               "photo_url": up.json()["url"], "from_chat": False})
        if r.status_code != 200:
            print(f"{слаг}: отказ {r.status_code} — {r.text[:140]}")
            return 1

    await connect()
    ключ = None
    try:
        for _ in range(ЖДЁМ_СЕКУНД // 10):
            async with get_factory()() as s:
                row = (await s.execute(text("""
                    SELECT g.status, m.storage_key
                    FROM generations g
                    LEFT JOIN media_assets m ON m.id = g.result_media_id
                    WHERE g.request_params->>'style_id' = :s
                    ORDER BY g.created_at DESC LIMIT 1"""), {"s": слаг})).first()
            if row and row[0] == "done" and row[1]:
                ключ = row[1]; break
            if row and row[0] == "failed":
                print(f"{слаг}: работа не получилась"); return 1
            await asyncio.sleep(10)

        if not ключ:
            print(f"{слаг}: кадр не дождался за {ЖДЁМ_СЕКУНД} с"); return 1

        данные = await get_storage().get(ключ)
        готово = Image.open(io.BytesIO(данные)).convert("RGB")
        готово.thumbnail((1024, 1024), Image.LANCZOS)
        готово.save(папка / "example-1.jpg", quality=90, optimize=True)
        (папка / "source-1.jpg").write_bytes(лицо.read_bytes())
        размер = (папка / "example-1.jpg").stat().st_size // 1024
        print(f"{слаг}: снят, {размер} КБ, исходник рядом → {папка}")
        return 0
    finally:
        await disconnect()


raise SystemExit(asyncio.run(main()))
