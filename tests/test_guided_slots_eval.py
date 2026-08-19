"""Разметка и счёт для замера моделей на разборе фразы.

Сам замер ходит в сеть и стоит денег — здесь проверяется то, что деньгам не
подчиняется: что разметка описывает существующие слоты и допустимые значения, и
что счёт наказывает выдумку сильнее промаха. Ошибка в счёте тише всех: прогон
пройдёт, таблица нарисуется, и мы выберем модель, которая уверенно врёт.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import pytest

from app.services import gpt
from scripts.eval_slots import Case, judge, load_cases, matches, scoreboard

CASES = load_cases()


def test_dataset_is_not_empty():
    assert len(CASES) >= 15


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_case_describes_real_slots(case: Case):
    """Разметка не может спрашивать о том, чего сервер не разбирает."""
    assert case.text.strip(), f"{case.id}: пустая фраза"
    assert case.slots, f"{case.id}: не сказано, какие поля были открыты"
    for slot in case.slots:
        assert slot in gpt.SLOT_MEANING, f"{case.id}: слота {slot!r} нет в разборе"
    for slot in case.expect:
        assert slot in case.slots, f"{case.id}: ждём {slot!r}, но его не спрашивали"
    for slot in case.allow:
        assert slot not in case.expect, f"{case.id}: {slot!r} и ждём, и разрешаем"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_closed_slot_expectations_are_real_options(case: Case):
    """У закрытого слота ждать можно только то, что сервер потом примет.

    Разметка вроде `format: "horizontal"` сделала бы тест невыполнимым для любой
    модели: сервер такое значение отбрасывает, и правильный ответ выглядел бы
    как ошибка.
    """
    for slot, expected in case.expect.items():
        options = gpt.SLOT_OPTIONS.get(slot)
        if options is None:
            continue
        for value in (expected if isinstance(expected, list) else [expected]):
            assert value in options, f"{case.id}: {value!r} нет среди вариантов {slot!r}"


def test_ids_are_unique():
    ids = [case.id for case in CASES]
    assert len(ids) == len(set(ids))


def test_the_reported_run_is_in_the_dataset():
    """Тот самый прогон остаётся в наборе.

    Разбор чинили по нему; без него правка вернётся вместе с первой же
    перестановкой промпта, и заметим мы это снова по скриншоту.
    """
    case = next(c for c in CASES if c.id == "poster-anime-nba")
    assert case.expect["technique"] == "anime"
    assert "reference" in case.expect


# ─── Сверка ответа с разметкой ───────────────────────────────────────────────

def test_closed_slot_is_matched_exactly():
    assert matches("technique", "anime", "anime")
    # Описание вместо идентификатора — не «менее точный ответ», а никакой:
    # сервер его отбросит.
    assert not matches("technique", "anime", "anime illustration")
    assert matches("technique", ["3d_cartoon", "semi_real_3d"], "semi_real_3d")


def test_free_slot_is_matched_by_words():
    # Сверяется вхождение, а не строка целиком: «caf» покрывает и «café», и
    # «coffee shop», а требовать от модели дословную формулировку разметки
    # значило бы мерить совпадение слов, а не понимание фразы.
    assert matches("place", ["caf"], "a quiet café by the window")
    assert matches("place", ["window"], "a quiet café by the window")
    assert not matches("place", ["kitchen"], "a quiet café by the window")
    # Несколько требований — все обязательны.
    assert matches("palette", ["white", "blue"], "white and blue")
    assert not matches("palette", ["white", "blue"], "white and red")
    # Варианты внутри одного требования — через черту.
    assert matches("reference", ["sport|basketball"], "a basketball campaign")


# ─── Счёт ────────────────────────────────────────────────────────────────────

CASE = Case(
    id="проба",
    text="постер в стиле аниме в бело-синих тонах",
    slots=["technique", "palette", "place", "format"],
    expect={"technique": "anime", "palette": ["white", "blue"]},
    allow=["place"],
)


def test_perfect_answer_scores_clean():
    verdict = judge(CASE, {"technique": "anime", "palette": "white and blue"})
    assert verdict.hit == ["technique", "palette"]
    assert not verdict.miss and not verdict.lies


def test_silence_is_a_miss_not_a_lie():
    verdict = judge(CASE, {})
    assert set(verdict.miss) == {"technique", "palette"}
    assert verdict.lies == 0


def test_wrong_value_is_a_lie():
    verdict = judge(CASE, {"technique": "3d_cartoon", "palette": "white and blue"})
    assert verdict.hit == ["palette"]
    assert verdict.wrong == ["technique='3d_cartoon'"]


def test_slot_nobody_mentioned_is_invented():
    verdict = judge(CASE, {"technique": "anime", "palette": "white and blue",
                           "format": "16:9"})
    assert verdict.invented == ["format='16:9'"]


def test_allowed_slot_is_neither_credited_nor_punished():
    """Спорное прочтение не должно ни спасать модель, ни топить её."""
    verdict = judge(CASE, {"technique": "anime", "palette": "white and blue",
                           "place": "a basketball court"})
    assert verdict.lies == 0
    assert verdict.hit == ["technique", "palette"]


def test_lying_scores_below_staying_silent():
    """Главное правило продукта, переведённое в число.

    Молчаливая модель задаёт лишний вопрос. Врущая записывает человеку в поле
    то, чего он не говорил, и он увидит это на кадре, за который уже заплатил.
    Счёт обязан их различать — иначе замер выберет вторую, она «отвечает чаще».
    """
    silent = scoreboard([judge(CASE, {})])
    liar = scoreboard([judge(CASE, {"technique": "realistic", "palette": "red"})])
    assert liar["очки"] < silent["очки"]
    assert silent["ошибки"] == 0 and liar["ошибки"] == 2


def test_scoreboard_counts_what_was_taken():
    board = scoreboard([judge(CASE, {"technique": "anime"})])
    assert board["взято"] == 0.5
    assert board["промахи"] == 1
