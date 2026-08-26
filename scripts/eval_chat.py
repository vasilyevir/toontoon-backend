#!/usr/bin/env python
"""Прогон разговора целиком: от первой реплики до готового кадра.

    PYTHONPATH=. .venv/bin/python scripts/eval_chat.py --faces путь/к/снимкам
    PYTHONPATH=. .venv/bin/python scripts/eval_chat.py --faces … --only restated-drops-the-old-picture
    PYTHONPATH=. .venv/bin/python scripts/eval_chat.py --faces … --no-frames

Зачем: список ручных проверок чата — двадцать два пункта, и половина из них
это одна и та же переписка, набранная руками заново. Человек устаёт печатать
раньше, чем находит ошибку, а находится она обычно на третьем повторе.

Что здесь проверяется и что нет. Через API проходит всё, что решает сервер:
память, готовность, вопросы, язык, цены, назначение — и сам кадр, который
судится теми же средствами, что и замер кадров. Не проходит ничего, что живёт
только на экране: чипы строки понятого, касание крестика, перезапуск
приложения, карточка подтверждения. Это XCUITest, и он тут не притворяется.

Каждый сценарий — свежий гость: состояние разговора живёт на сервере, и
прогон, начатый с чужой памятью, проверял бы не то.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import subprocess
import sys
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import eval_frames as ef  # noqa: E402

BASE = ef.BASE
FLOWS = pathlib.Path("tests/data/chat_flows.jsonl")
RUNS = pathlib.Path("docs/research/runs/_chat")


class Fail(Exception):
    """Ожидание не сошлось. Текст — то, что увидит человек в отчёте."""


def matches(pattern: str, text: str) -> bool:
    return re.search(pattern, text or "", re.IGNORECASE) is not None


async def upload(client: httpx.AsyncClient, headers: dict, path: pathlib.Path) -> str:
    files = {"file": (path.name, path.read_bytes(), "image/jpeg")}
    r = await client.post(f"{BASE}/api/uploads", headers=headers, files=files)
    r.raise_for_status()
    d = r.json()
    return d.get("media_id") or d.get("id")


async def make_profile(client: httpx.AsyncClient, headers: dict, faces: list[pathlib.Path]) -> str:
    ids = [await upload(client, headers, f) for f in faces]
    r = await client.post(f"{BASE}/api/profiles", headers=headers,
                          json={"name": "Me", "media_ids": ids})
    r.raise_for_status()
    return r.json()["id"]


def words_on(path: pathlib.Path) -> str:
    """Что написано на кадре — системным зрением macOS."""
    out = subprocess.run(["swift", "scripts/ocr.swift", str(path)],
                         capture_output=True, text=True)
    return out.stdout.split("\t", 1)[1].strip() if "\t" in out.stdout else ""


async def check_frame(data: bytes, want: dict, path: pathlib.Path) -> list[str]:
    """Кадр судится тем же, чем и замер кадров: зрением, чтением, арифметикой."""
    from app.storage import images

    notes = []
    if (absent := want.get("absent")):
        gone, why = await ef.judge_absent(data, absent)
        if gone is False:
            notes.append(f"в кадре есть «{absent}»: {why[:80]}")
    if (medium := want.get("medium")):
        from app.services import gpt
        photographic = await gpt.looks_photographic(data)
        seen = "photo" if photographic else "drawn"
        if seen != medium:
            notes.append(f"вид кадра {seen}, ждали {medium}")
    if (aspect := want.get("aspect")):
        seen = images.aspect_of(data)
        if seen != aspect:
            notes.append(f"пропорции {seen}, ждали {aspect}")
    if (forbidden := want.get("forbidden")):
        text = words_on(path)
        if forbidden.lower() in text.lower():
            notes.append(f"в кадре чужое слово «{forbidden}»: {text[:60]}")
    if (letters := want.get("letters")):
        text = words_on(path)
        if letters.lower() not in text.lower():
            notes.append(f"надписи «{letters}» в кадре нет: {text[:60] or 'пусто'}")
    return notes


async def run_flow(client: httpx.AsyncClient, flow: dict, faces: list[pathlib.Path],
                   out: pathlib.Path, with_frames: bool) -> tuple[bool, list[str]]:
    guest = (await client.post(f"{BASE}/api/auth/guest", json={})).json()
    headers = {"Authorization": f"Bearer {guest['session_token']}"}
    expect = flow.get("expect", {})
    notes: list[str] = []

    profile = None
    if flow.get("profile", True) and faces:
        profile = await make_profile(client, headers, faces[:3])

    asked: list[str] = []
    reply = ""
    remark = ""
    answer: dict = {}
    attached: list[str] = []
    for step in flow["steps"]:
        if isinstance(step, dict):
            # «Очистка» — не реплика, а кнопка. Сбрасывает и переписку, и
            # понятое, поэтому список заданных вопросов идёт следом.
            if step.get("clear"):
                await client.post(f"{BASE}/api/chat/clear", headers=headers)
                asked = []
                continue
            # Приложенная картинка. Ручка одна: снимок кладётся в переписку и
            # сразу получает ответ — реплику и четыре идеи.
            if (name := step.get("attach")):
                path = pathlib.Path("tests/data/samples") / name
                if not path.exists() and faces:
                    path = faces[0]
                media_id = await upload(client, headers, path)
                attached.append(media_id)
                r = await client.post(f"{BASE}/api/chat/attachment", headers=headers,
                                      json={"media_id": media_id})
                if r.status_code == 200:
                    remark = r.json().get("remark") or ""
                continue
            # Снятое со строки понятого. Не реплика: отдельная ручка, молча.
            if (fields := step.get("forget")):
                r = await client.post(f"{BASE}/api/chat/forget", headers=headers,
                                      json={"forget": fields})
                if r.status_code == 200:
                    answer = {**answer, "known": r.json().get("known") or {}}
                continue
            # «Приложение перезапустили»: список заданных вопросов на клиенте
            # пуст, а сервер обязан помнить его сам.
            if step.get("forget_asked"):
                asked = []
            text = step.get("message", "")
            repeat = int(step.get("repeat", 1))
        else:
            text, repeat = step, 1
        for _ in range(repeat):
            body = {"message": text, "asked": asked,
                    "photo_attached": bool(attached),
                    "attachment_ids": attached}
            r = await client.post(f"{BASE}/api/chat", headers=headers, json=body)
            r.raise_for_status()
            answer = r.json()
            if answer.get("ask_about"):
                asked.append(answer["ask_about"])
            reply = answer.get("reply") or ""

    known = answer.get("known") or {}
    for pattern in expect.get("remark_matches") or []:
        if not matches(pattern, remark):
            notes.append(f"в реплике на снимок нет «{pattern}»: {remark[:70] or 'пусто'}")

    # Холодное чтение: так строку понятого видит приложение после перезапуска.
    # Половина проверок про неё — не про отрисовку, а про то, что сервер отдаёт.
    if any(k in expect for k in ("state_has", "state_lacks", "state_intent", "state_clean")):
        state = (await client.get(f"{BASE}/api/chat/state", headers=headers)).json()
        cold = state.get("known") or {}
        if "state_intent" in expect and state.get("intent") != expect["state_intent"]:
            notes.append(f"в состоянии назначение {state.get('intent')!r}, "
                         f"ждали {expect['state_intent']!r}")
        for field, pattern in (expect.get("state_has") or {}).items():
            if not matches(pattern, str(cold.get(field, ""))):
                notes.append(f"после перезапуска {field}={cold.get(field)!r} "
                             f"не совпало с «{pattern}»")
        for field in expect.get("state_lacks") or []:
            if cold.get(field):
                notes.append(f"после перезапуска {field} остался: {cold[field]!r}")
        if expect.get("state_clean"):
            # Служебные пометки и английские заглушки на экране становятся чипом
            # посреди русской переписки.
            leaked = [f for f in cold if f.startswith("_")]
            leaked += [f for f, v in cold.items() if str(v).startswith("from the attached")]
            if leaked:
                notes.append(f"наружу вышло служебное: {leaked}")
    if "intent" in expect and answer.get("intent") != expect["intent"]:
        notes.append(f"назначение {answer.get('intent')!r}, ждали {expect['intent']!r}")
    if "ready" in expect and bool(answer.get("ready")) is not expect["ready"]:
        notes.append(f"готовность {answer.get('ready')}, ждали {expect['ready']}")
    if "gap" in expect and answer.get("ask_about") != expect["gap"]:
        notes.append(f"спросил {answer.get('ask_about')!r}, ждали {expect['gap']!r}")
    # Чаще важно не «о чём спросил», а «о чём НЕ переспросил»: разговор законно
    # идёт к следующему пробелу, и жёсткое ожидание ловило бы это как поломку.
    if (never := expect.get("gap_not")) and answer.get("ask_about") == never:
        notes.append(f"переспросил про {never!r}, а об этом уже спрашивали")
    for field, pattern in (expect.get("known_has") or {}).items():
        if not matches(pattern, str(known.get(field, ""))):
            notes.append(f"поле {field}={known.get(field)!r} не совпало с «{pattern}»")
    for field in expect.get("known_lacks") or []:
        if known.get(field):
            notes.append(f"поле {field} осталось: {known[field]!r}")
    for pattern in expect.get("reply_matches") or []:
        if not matches(pattern, reply):
            notes.append(f"в ответе нет «{pattern}»: {reply[:70]}")
    for pattern in expect.get("reply_lacks") or []:
        if matches(pattern, reply):
            notes.append(f"в ответе есть лишнее «{pattern}»: {reply[:70]}")

    if with_frames and (want := expect.get("frame")):
        body = {"type": "image", "from_chat": True, "post_prompt": False,
                "roles_chosen": False}
        if profile:
            body |= {"profile_id": profile, "profile_ids": [profile]}
        # Роли приложенным картинкам раздаёт сам разговор — приложение только
        # раскладывает их по полям запроса. Прогон обязан делать то же самое:
        # образец, уехавший в поле человека, — это чужое лицо в кадре.
        roles = answer.get("roles") or []
        if attached and roles:
            refs = [f"/api/media/{mid}" for mid, role in zip(attached, roles)
                    if role == "style"]
            if refs:
                body |= {"style_ref_urls": refs, "roles_chosen": True}
        r = await client.post(f"{BASE}/api/generate", headers=headers, json=body)
        if r.status_code != 200:
            notes.append(f"кадр не сделан: {r.status_code} {r.text[:90]}")
        else:
            d = r.json()
            frame = (await client.get(f"{BASE}{d['url']}", headers=headers)).content
            path = out / f"{flow['id']}.jpg"
            path.write_bytes(frame)
            notes += await check_frame(frame, want, path)
            # Слово после кадра и четыре идеи ложатся в ту же переписку, значит
            # и язык у них оттуда же. Происходит это после КАЖДОЙ генерации,
            # поэтому английская строка здесь заметнее всего.
            for pattern in expect.get("ideas_match") or []:
                r = await client.get(f"{BASE}/api/generations/{d['id']}/ideas",
                                     headers=headers)
                if r.status_code != 200:
                    notes.append(f"идеи не пришли: {r.status_code} {r.text[:60]}")
                    break
                ideas = r.json()
                said = " ".join([ideas.get("remark") or ""] + (ideas.get("ideas") or []))
                if not matches(pattern, said):
                    notes.append(f"в идеях нет «{pattern}»: {said[:70] or 'пусто'}")

    return not notes, notes


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--faces", type=pathlib.Path,
                        help="папка со снимками: из них собирается профиль")
    parser.add_argument("--only", help="через запятую: только эти сценарии")
    parser.add_argument("--no-frames", action="store_true",
                        help="без генерации: только разговор, зато бесплатно")
    args = parser.parse_args()

    flows = [json.loads(line) for line in FLOWS.read_text().splitlines() if line.strip()]
    if args.only:
        wanted = set(args.only.split(","))
        flows = [f for f in flows if f["id"] in wanted]
    if not flows:
        raise SystemExit("Не нашёл ни одного сценария")

    faces = sorted(args.faces.glob("*.jpg")) if args.faces else []
    out = RUNS / time.strftime("%Y-%m-%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    results = []
    async with httpx.AsyncClient(timeout=600) as client:
        for flow in flows:
            started = time.monotonic()
            try:
                ok, notes = await run_flow(client, flow, faces, out, not args.no_frames)
            except Exception as exc:  # noqa: BLE001 — упавший сценарий тоже результат
                ok, notes = False, [f"{type(exc).__name__}: {exc}"[:120]]
            results.append({"id": flow["id"], "ok": ok, "notes": notes,
                            "seconds": round(time.monotonic() - started, 1)})
            mark = "✓" if ok else "✗"
            print(f"  {mark} {flow['id']:34} {results[-1]['seconds']:5.1f} c")
            for note in notes:
                print(f"      {note}")

    bad = [r for r in results if not r["ok"]]
    print(f"\nсценариев: {len(results)}, не прошло: {len(bad)}")
    (out / "run.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    (out / ".gitignore").write_text("*.jpg\n")
    print(f"Кадры и числа: {out}")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    asyncio.run(main())
