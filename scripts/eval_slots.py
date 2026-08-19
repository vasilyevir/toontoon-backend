#!/usr/bin/env python
"""Прогнать разбор фразы по слотам через несколько моделей и сложить рядом.

    PYTHONPATH=. .venv/bin/python scripts/eval_slots.py --catalogue
    PYTHONPATH=. .venv/bin/python scripts/eval_slots.py \
        --models openai/gpt-4o-mini,google/gemini-2.5-flash --repeats 3
    PYTHONPATH=. .venv/bin/python scripts/eval_slots.py --models openai/gpt-4o-mini \
        --cases poster-anime-nba,nothing-said --verbose

Зачем: разбор решает, о чём разговор ещё спросит, а о чём промолчит. Ошибка
здесь не падает и не логируется — человек просто видит вопрос о том, что уже
написал, или свой ответ, записанный не в то поле. Значит выбирать модель под
эту задачу можно только замером, и замер должен считать обе ошибки отдельно:
они стоят разного.

    промах  — слот не заполнен, хотя человек про него сказал. Один лишний
              вопрос. Досадно, не страшно.
    ошибка  — слот заполнен не тем, что сказано.
    выдумка — заполнен слот, про который не было ни слова.

Последние две дороже промаха в разы: лишний вопрос человек переживёт, а чужой
ответ в поле уедет в картинку, и заметит он это уже на результате, за который
списан TOONTOON. Поэтому годной считается модель, которая ни разу не соврала, —
и уже среди таких берётся самая дешёвая.

Модель подменяется настройкой `slot_extraction_model` — той самой, которой
разбор пользуется в проде, — а зовётся тот же самый `gpt.extract_slots`:
сравнение должно мерить модели, а не две разные сборки запроса.

Датасет лежит рядом с тестами (`tests/data/guided_slots.jsonl`): та же разметка
проверяется офлайн в CI, и две копии разъехались бы на первой правке.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.config import settings
from app.services import gpt

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "data" / "guided_slots.jsonl"
RUNS = ROOT / "docs" / "research" / "runs" / "_slots"

# Во сколько раз выдумка дороже промаха. Не из воздуха: промах стоит одного
# вопроса, выдумка — кадра, за который уже списаны деньги.
LIE_COST = 3


@dataclass(slots=True)
class Case:
    id: str
    text: str
    slots: list[str]
    expect: dict
    allow: list[str]
    note: str = ""


@dataclass(slots=True)
class Verdict:
    """Что модель сделала с одной фразой."""

    case: str
    hit: list[str] = field(default_factory=list)
    miss: list[str] = field(default_factory=list)
    wrong: list[str] = field(default_factory=list)
    invented: list[str] = field(default_factory=list)
    seconds: float = 0.0
    failed: str = ""
    # Расход по факту витрины: у моделей с рассуждением `answered` в разы
    # больше того, что видно в ответе.
    asked: int = 0
    answered: int = 0

    @property
    def lies(self) -> int:
        return len(self.wrong) + len(self.invented)


def load_cases(only: list[str] | None = None) -> list[Case]:
    cases = []
    for line in CASES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        if only and raw["id"] not in only:
            continue
        cases.append(Case(
            id=raw["id"], text=raw["text"], slots=raw["slots"],
            expect=raw.get("expect", {}), allow=raw.get("allow", []),
            note=raw.get("note", ""),
        ))
    return cases


def matches(slot: str, expected, value: str) -> bool:
    """Совпал ли ответ модели с разметкой.

    У закрытых слотов сверка точная: значение уезжает параметром, и «anime
    style» вместо `anime` сервер отбросит — то есть это не менее точный ответ,
    а никакой. Разметка при этом может назвать несколько годных якорей: «как в
    мультике» — это честно и `3d_cartoon`, и `semi_real_3d`.

    У свободных слотов сверяются слова, а не строка целиком: модель вправе
    написать «a quiet café by the window» там, где размечено «caf». Внутри
    одного требования варианты разделяются вертикальной чертой.
    """
    if slot in gpt.SLOT_OPTIONS:
        allowed = expected if isinstance(expected, list) else [expected]
        return value in allowed
    terms = expected if isinstance(expected, list) else [expected]
    low = value.lower()
    return all(
        any(alt.strip().lower() in low for alt in str(term).split("|"))
        for term in terms
    )


def judge(case: Case, filled: dict[str, str]) -> Verdict:
    """Разложить ответ модели на попадания, промахи, ошибки и выдумки."""
    verdict = Verdict(case=case.id)
    for slot, expected in case.expect.items():
        value = filled.get(slot)
        if not value:
            verdict.miss.append(slot)
        elif matches(slot, expected, value):
            verdict.hit.append(slot)
        else:
            verdict.wrong.append(f"{slot}={value!r}")
    for slot, value in filled.items():
        # Слот вне списка запрошенных — тоже выдумка: приложение про него не
        # спрашивало, а значит человек уже ответил на него руками.
        if slot in case.expect or slot in case.allow:
            continue
        verdict.invented.append(f"{slot}={value!r}")
    return verdict


def scoreboard(verdicts: list[Verdict]) -> dict:
    """Свести прогон в числа, по которым принимается решение."""
    hits = sum(len(v.hit) for v in verdicts)
    misses = sum(len(v.miss) for v in verdicts)
    wrong = sum(len(v.wrong) for v in verdicts)
    invented = sum(len(v.invented) for v in verdicts)
    said = hits + misses + wrong
    failed = [v for v in verdicts if v.failed]
    times = [v.seconds for v in verdicts if not v.failed]
    return {
        # Ноль, а не единица, когда сверять нечего: прогон, где всё отвалилось,
        # не «взял всё» — он не взял ничего.
        "взято": round(hits / said, 3) if said else 0.0,
        "промахи": misses,
        "ошибки": wrong,
        "выдумки": invented,
        # Одно число, по которому можно сортировать: попадания минус враньё,
        # делённое на то, что человек вообще сказал.
        "очки": round((hits - LIE_COST * (wrong + invented)) / said, 3) if said else 0.0,
        "секунды": round(statistics.median(times), 2) if times else 0.0,
        "отказы": len(failed),
        # Сколько токенов модель потратила на один разбор. У думающих моделей
        # это главная строка счёта, и в ответе её не видно.
        "токенов на ответ": round(statistics.median(
            [v.answered for v in verdicts if v.answered] or [0])),
    }


# ─── Цена ────────────────────────────────────────────────────────────────────
# Прайс берётся у витрины, а не из памяти: список моделей и цены меняются чаще,
# чем этот файл, а решение принимается именно по цене.

async def price_list() -> dict[str, tuple[float, float]]:
    """`модель → (цена за токен запроса, цена за токен ответа)`."""
    url = f"{settings.openrouter_base_url.rstrip('/')}/models"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url)
        response.raise_for_status()
    prices = {}
    for row in response.json().get("data", []):
        pricing = row.get("pricing") or {}
        try:
            prices[row["id"]] = (float(pricing.get("prompt", 0)),
                                 float(pricing.get("completion", 0)))
        except (TypeError, ValueError):
            continue
    return prices


def run_cost(verdicts: list[Verdict], prices: tuple[float, float]) -> float:
    """Во что обойдётся тысяча таких разборов.

    Токены берутся у витрины по факту, а не считаются по длине строки. Иначе
    модель с рассуждением выглядела бы дешевле всех: ответ у неё в три строки,
    а платим мы и за те несколько сотен токенов, которые она думала.
    """
    prompt_price, completion_price = prices
    measured = [v for v in verdicts if v.asked]
    if not measured:
        return 0.0
    total = sum(v.asked * prompt_price + v.answered * completion_price for v in measured)
    return total / len(measured) * 1000


# ─── Прогон ──────────────────────────────────────────────────────────────────

async def run_case(case: Case, gate: asyncio.Semaphore) -> Verdict:
    async with gate:
        started = time.monotonic()
        usage: dict = {}
        try:
            filled = await gpt.extract_slots(case.text, case.slots, usage=usage)
        except Exception as exc:  # noqa: BLE001 — один отказ не должен рвать прогон
            verdict = Verdict(case=case.id, failed=str(exc)[:160])
            verdict.seconds = time.monotonic() - started
            return verdict
        verdict = judge(case, filled)
        verdict.seconds = time.monotonic() - started
        verdict.asked = int(usage.get("prompt_tokens") or 0)
        verdict.answered = int(usage.get("completion_tokens") or 0)
        return verdict


async def run_model(model: str, cases: list[Case], repeats: int,
                    parallel: int) -> list[Verdict]:
    settings.slot_extraction_model = model
    gate = asyncio.Semaphore(parallel)
    # Повторы подряд, а не вперемешку: температура нулевая, но модели всё равно
    # плывут, и одинаковый ответ трижды — это тоже результат замера.
    runs = [case for _ in range(repeats) for case in cases]
    return await asyncio.gather(*(run_case(case, gate) for case in runs))


def report_lines(verdicts: list[Verdict], verbose: bool) -> list[str]:
    lines = []
    for verdict in verdicts:
        if verdict.failed:
            lines.append(f"    ОТКАЗ  {verdict.case}: {verdict.failed}")
            continue
        if verdict.lies:
            for item in verdict.wrong:
                lines.append(f"    ОШИБКА {verdict.case}: {item}")
            for item in verdict.invented:
                lines.append(f"    ВЫДУМКА {verdict.case}: {item}")
        elif verbose and verdict.miss:
            lines.append(f"    промах {verdict.case}: {', '.join(verdict.miss)}")
    return lines


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default="",
                        help="идентификаторы витрины через запятую")
    parser.add_argument("--cases", default="", help="id случаев через запятую")
    parser.add_argument("--repeats", type=int, default=1,
                        help="сколько раз прогнать каждый случай")
    parser.add_argument("--parallel", type=int, default=4,
                        help="сколько запросов держать одновременно")
    parser.add_argument("--verbose", action="store_true", help="показывать и промахи")
    parser.add_argument("--min-taken", type=float, default=0.8,
                        help="сколько сказанного модель обязана взять, чтобы считаться годной")
    parser.add_argument("--max-seconds", type=float, default=2.5,
                        help="потолок медианной задержки: разбор идёт, пока человек читает "
                             "вопрос, и опоздавший ответ приходит к уже отвеченному")
    parser.add_argument("--max-tokens", type=int, default=0,
                        help="потолок ответа; моделям с рассуждением продовых 200 не хватает "
                             "даже на пустой JSON — они тратят их на размышление")
    parser.add_argument("--catalogue", action="store_true",
                        help="показать дешёвые текстовые модели витрины и выйти")
    args = parser.parse_args()

    if not settings.openrouter_api_key:
        print("Нет OPENROUTER_API_KEY — замер зовёт витрину напрямую.")
        return 1

    if args.max_tokens:
        gpt.EXTRACT_MAX_TOKENS = args.max_tokens

    prices = await price_list()

    if args.catalogue:
        # Список приходит от витрины: держать его в файле значило бы выбирать
        # модель по ценам полугодовой давности.
        rows = sorted(((price, model) for model, (price, _) in prices.items() if price > 0),
                      key=lambda row: row[0])
        print(f"{'$/1M токенов запроса':>22}  модель")
        for price, model in rows[:40]:
            print(f"{price * 1_000_000:>22.3f}  {model}")
        return 0

    cases = load_cases([c.strip() for c in args.cases.split(",") if c.strip()] or None)
    if not cases:
        print("Не нашёл ни одного случая — проверь --cases.")
        return 1

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        models = [settings.slot_extraction_model or settings.openrouter_text_model]

    RUNS.mkdir(parents=True, exist_ok=True)
    report = {}
    was = settings.slot_extraction_model
    try:
        for model in models:
            print(f"\n{model} — {len(cases)} случаев × {args.repeats}")
            verdicts = await run_model(model, cases, args.repeats, args.parallel)
            board = scoreboard(verdicts)
            board["цена за 1000 разборов, $"] = round(run_cost(verdicts, prices.get(model, (0.0, 0.0))), 4)
            report[model] = board
            for line in report_lines(verdicts, args.verbose):
                print(line)
            print("    " + "  ".join(f"{k}: {v}" for k, v in board.items()))
    finally:
        settings.slot_extraction_model = was

    # Годная обязана пройти три порога сразу, и они не взаимозаменяемы.
    #
    # Не соврать — потому что чужой ответ в поле человек увидит на кадре, за
    # который списаны деньги. Взять хотя бы четыре пятых сказанного — потому что
    # молчаливая модель честна, но бесполезна: разговор с ней переспрашивает всё
    # подряд, то есть ведёт себя ровно так, как до разбора. И уложиться в пару
    # секунд — потому что разбор идёт, пока человек читает следующий вопрос;
    # опоздавший ответ приходит к вопросу, на который уже ответили.
    def passes(board: dict) -> bool:
        return (board["ошибки"] == 0 and board["выдумки"] == 0 and board["отказы"] == 0
                and board["взято"] >= args.min_taken
                and board["секунды"] <= args.max_seconds)

    clean = {m: b for m, b in report.items() if passes(b)}
    print("\n" + "─" * 72)
    for model, board in sorted(report.items(), key=lambda row: -row[1]["очки"]):
        mark = "  " if model in clean else "! "
        print(f"{mark}{model:<40} очки {board['очки']:>6}  взято {board['взято']:>5}  "
              f"{board['секунды']:>5} с  ${board['цена за 1000 разборов, $']:>7}")
    if clean:
        # Между годными выбирает цена, а не доли процента точности: лишний
        # вопрос дешевле любой экономии на модели, и наоборот.
        best = min(clean.items(), key=lambda row: (row[1]["цена за 1000 разборов, $"], -row[1]["взято"]))
        print(f"\nСамая дешёвая из годных: {best[0]} — взято {best[1]['взято']}, "
              f"{best[1]['секунды']} с, ${best[1]['цена за 1000 разборов, $']} за 1000 разборов")
    else:
        print(f"\nНи одна не прошла: нужны ноль ошибок и выдумок, взято ≥ {args.min_taken}, "
              f"медиана ≤ {args.max_seconds} с.")

    out = RUNS / "report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Отчёт: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
