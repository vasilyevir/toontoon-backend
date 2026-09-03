#!/usr/bin/env python
"""Снять примеры для карточек каталога — разными людьми.

    ./run-local.sh &
    PYTHONPATH=. .venv/bin/python scripts/shoot_examples.py            # лица из каталога
    PYTHONPATH=. .venv/bin/python scripts/shoot_examples.py ~/people   # лица из папки

Снимает только те стили, у которых примера ещё нет: прогон можно прерывать и
продолжать, ничего не переснимая заново.

Витрина из одного лица — это альбом одного человека, а не каталог. Лица
чередуются по кругу, чтобы соседние карточки в ленте не совпадали: человек
листает подряд, и повтор он видит именно у соседей.

Исходник каждого примера кладётся рядом (`source-1.jpg`) — по нему видно, из
чего кадр сделан, и прогон можно повторить.

## Почему список стилей больше не зашит сюда

Раньше здесь лежал перечень лент и слагов, и он дважды разошёлся с каталогом:
скрипт не знал про 34 стиля из 97, а два слага в нём указывали на папки,
удалённые из каталога. Расхождение молчаливое — скрипт просто проходил мимо, и
восемь карточек так и остались с чужими примерами, потому что снять их было
некому.

Теперь ленты читаются с диска: порядок направлений берётся из `CATEGORIES`
(тот же источник, что у главной), порядок внутри — из `sort_order` в
`style.json` (тот же, что у каталога). Разойтись с каталогом этот список уже
не может: он и есть каталог.

## Откуда берутся лица

Из `content/faces/` — отобранных руками по правилу «молодые модельные,
женские в приоритете». Раньше без аргумента пул собирался из всех
`source-1.jpg` каталога, и туда попадали пожилые люди и шесть фотографий
питомцев из `pet_magic`: скрипт не смотрит, кто на снимке. Папка с аргументом
по-прежнему допустима — для разовой съёмки чужим набором.
"""
import asyncio, hashlib, itertools, os, pathlib, sys
import httpx

sys.path.insert(0, ".")
from app.routers.onboarding import CATEGORIES  # noqa: E402

BASE = "http://127.0.0.1:8020"
ROOT = pathlib.Path("content/styles")


def ленты() -> list[tuple[str, str]]:
    """Пары (направление, слаг) в том же порядке, в каком их видит человек."""
    import json
    out: list[tuple[str, str]] = []
    for категория in CATEGORIES:
        каталог = ROOT / категория
        if not каталог.is_dir():
            continue
        папки = [p for p in каталог.iterdir() if (p / "style.json").exists()]
        def порядок(p: pathlib.Path) -> tuple[int, str]:
            try:
                return int(json.loads((p / "style.json").read_text()).get("sort_order", 100)), p.name
            except Exception:  # noqa: BLE001 — битый style.json не должен рушить съёмку
                return 100, p.name
        out += [(категория, p.name) for p in sorted(папки, key=порядок)]
    return out


def лица(аргумент: str | None) -> list[pathlib.Path]:
    """Снимки людей: из указанной папки или из отобранного пула `content/faces/`."""
    папка = pathlib.Path(аргумент).expanduser() if аргумент else ROOT.parent / "faces"
    найдено = sorted(p for p in папка.glob("*") if p.suffix.lower() in {".jpg", ".jpeg"})
    if not найдено:
        raise SystemExit(f"В {папка} нет снимков — пул лиц пуст, снимать некем")
    return найдено

async def дождаться(c, h, gen_id: str, *, терпение: int = 300) -> str | None:
    """Дождаться кадра и вернуть путь к нему.

    Генерация асинхронная: `POST /api/generate` отвечает `queued` с ПУСТЫМ
    `url`, а кадр появляется секунд через сорок. Раньше здесь этот пустой
    адрес брался сразу — и в карточку писался ответ «Not Found» на 22 байта.
    Скрипт при этом рапортовал «снято»: он не смотрел, картинка ли пришла.

    Отсюда две проверки вместо одной. Дождаться `done` — и убедиться, что в
    файл лёг JPEG, а не текст ошибки: карточка каталога, в которой лежит
    строка «Not Found», выглядит в приложении как битая картинка, и заметит
    это уже человек.
    """
    ждём = 0
    while ждём < терпение:
        await asyncio.sleep(5)
        ждём += 5
        r = await c.get(f"{BASE}/api/generations/{gen_id}", headers=h)
        if r.status_code != 200:
            continue
        d = r.json()
        if d.get("status") == "done":
            return d.get("result_url")
        if d.get("status") in {"failed", "cancelled"}:
            return None
    return None


async def main():
    колесо = itertools.cycle(лица(sys.argv[1] if len(sys.argv) > 1 else None))
    надо = [(r, s) for r, s in ленты() if not (ROOT / r / s / "example-1.jpg").exists()]
    if not надо:
        print("У всех карточек примеры на месте — снимать нечего.")
        return
    print(f"Снять предстоит: {len(надо)}")

    async with httpx.AsyncClient(timeout=600) as c:
        # Свежий гость получает `SIGNUP_TOONTOON_BALANCE` — этого хватает на
        # три-четыре кадра, а в каталоге их девяносто семь. Пересъёмка целиком
        # упиралась в баланс на четвёртой карточке и молча пропускала остальные
        # («ОШИБКА 402»), то есть выглядела как отказ провайдера.
        #
        # Поэтому токен можно передать снаружи: завести счёт, пополнить его и
        # снимать под ним. Без переменной поведение прежнее — гость.
        токен = os.environ.get("TOONTOON_TOKEN")
        if not токен:
            r = await c.post(f"{BASE}/api/auth/guest"); r.raise_for_status()
            токен = r.json()["session_token"]
            print("Снимаем гостем. На весь каталог его баланса не хватит — "
                  "для полной пересъёмки задайте TOONTOON_TOKEN.")
        h = {"Authorization": f"Bearer {токен}"}

        загружено: dict[str, str] = {}
        for рельс, слаг in надо:
            папка = ROOT / рельс / слаг
            лицо = next(колесо)
            ключ = hashlib.md5(лицо.read_bytes()).hexdigest()
            if ключ not in загружено:
                up = await c.post(f"{BASE}/api/uploads", headers=h,
                                  files={"file": ("face.jpg", лицо.read_bytes(), "image/jpeg")})
                up.raise_for_status()
                загружено[ключ] = up.json()["url"]

            r = await c.post(f"{BASE}/api/generate", headers=h,
                             json={"type": "image", "style_id": слаг,
                                   "photo_url": загружено[ключ], "from_chat": False})
            if r.status_code == 402:
                # Продолжать нечем: каждая следующая карточка упрётся в то же
                # самое, и прогон закончится списком одинаковых ошибок, в
                # котором настоящий отказ модели уже не разглядеть.
                print(f"{слаг:20} кончился баланс — остальные не сняты")
                print("Пополните счёт и задайте TOONTOON_TOKEN.")
                return
            if r.status_code != 200:
                print(f"{слаг:20} ОШИБКА {r.status_code}: {r.text[:120]}"); continue
            готово = await дождаться(c, h, r.json()["id"])
            if not готово:
                print(f"{слаг:20} не дождались кадра"); continue
            image = await c.get(f"{BASE}{готово}", headers=h)
            if not image.content.startswith(b"\xff\xd8") and not image.content.startswith(b"\x89PNG"):
                print(f"{слаг:20} пришла не картинка: {image.content[:80]!r}"); continue
            (папка / "example-1.jpg").write_bytes(image.content)
            (папка / "source-1.jpg").write_bytes(лицо.read_bytes())
            print(f"{рельс}/{слаг:20} снято с {лицо.parent.name}")


asyncio.run(main())
