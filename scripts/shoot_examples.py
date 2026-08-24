#!/usr/bin/env python
"""Снять примеры для карточек каталога — разными людьми.

    ./run-local.sh &
    PYTHONPATH=. .venv/bin/python scripts/shoot_examples.py ~/people

Снимает только те стили, у которых примера ещё нет: прогон можно прерывать и
продолжать, ничего не переснимая заново.

Витрина из одного лица — это альбом одного человека, а не каталог. Лица
чередуются по кругу, чтобы соседние карточки в ленте не совпадали: человек
листает подряд, и повтор он видит именно у соседей.

Исходник каждого примера кладётся рядом (`source-1.jpg`) — по нему видно, из
чего кадр сделан, и прогон можно повторить.
"""
import asyncio, itertools, pathlib, sys
import httpx

BASE = "http://127.0.0.1:8020"
ROOT = pathlib.Path("content/styles")
FACES = pathlib.Path(sys.argv[1])

# Ленты и порядок карточек в них. Порядок важен для лиц: они чередуются по
# кругу, и соседние карточки не должны совпадать.
RAILS = {
    "ai_photo_studio": ["platinum", "soft_daylight", "olive_room", "ruby_glow",
                        "golden_waves", "silver_sheen", "candy_pink", "warm_knit",
                        "hard_noir", "blue_hour"],
    "lifestyle_travel": ["beach_day", "morning_coffee", "boutique_street", "alpine_air",
                         "night_drive", "marina_sunset", "blossom_garden", "old_town",
                         "golden_corridor", "paris_cafe", "seaside_editorial", "city_walk",
                         "mountain_trail", "greenhouse", "rooftop_night", "burgundy",
                         "coast_drive", "lakeside", "stone_terrace"],
    "cartoon_me": ["neon_rain", "plant_room", "artist_studio", "bedroom_beats",
                   "fashion_plate", "comic_panel", "bakery", "ballpoint", "cosy_cafe",
                   "skate_park"],
    "artistic_touch": ["sunset_shore", "croissant_morning", "neon_street", "park_bench",
                       "seaside_lounge", "gallery_room", "rainy_window", "golden_park",
                       "paris_trench", "night_market"],
    "paparazzi_flash": ["restaurant_exit", "back_seat", "red_carpet", "party_2000s",
                        "hotel_elevator", "night_street", "candid_moment"],
    "glow_up": ["cafe_fashion", "old_money_hotel", "luxury_rooftop", "beauty_closeup",
                "magazine_portrait"],
    "polaroid_reunion": ["hug_younger_self", "then_and_now", "imagined_childhood",
                         "future_me"],
}


async def main():
    faces = sorted(p for p in FACES.iterdir() if p.suffix.lower() in {".jpg", ".jpeg"})
    if not faces:
        raise SystemExit(f"В {FACES} нет снимков")
    wheel = itertools.cycle(faces)

    async with httpx.AsyncClient(timeout=600) as c:
        r = await c.post(f"{BASE}/api/auth/guest"); r.raise_for_status()
        h = {"Authorization": f"Bearer {r.json()['session_token']}"}

        uploaded: dict[str, str] = {}
        for rail, slugs in RAILS.items():
            for slug in slugs:
                folder = ROOT / rail / slug
                if (folder / "example-1.jpg").exists():
                    continue
                face = next(wheel)
                if face.name not in uploaded:
                    up = await c.post(f"{BASE}/api/uploads", headers=h,
                                      files={"file": (face.name, face.read_bytes(), "image/jpeg")})
                    up.raise_for_status()
                    uploaded[face.name] = up.json()["url"]

                r = await c.post(f"{BASE}/api/generate", headers=h,
                                 json={"type": "image", "style_id": slug,
                                       "photo_url": uploaded[face.name], "from_chat": False})
                if r.status_code != 200:
                    print(f"{slug:18} ОШИБКА {r.status_code}: {r.text[:100]}"); continue
                res = r.json()
                image = await c.get(f"{BASE}{res['url']}", headers=h)
                (folder / "example-1.jpg").write_bytes(image.content)
                (folder / "source-1.jpg").write_bytes(face.read_bytes())
                print(f"{slug:18} {face.stem}")

asyncio.run(main())
