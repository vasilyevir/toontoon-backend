"""Сколько места в Redis может занять поток аналитики.

Ручка неаутентифицированная, а счётчики жили по ключу на имя события — имя
выбирает отправитель, TTL нет, числа разных имён нет. Миллион придуманных имён
означал миллион вечных ключей. В том же Redis лежат сессии, и при
`maxmemory-policy allkeys-lru` заполнение выкидывало бы именно их, то есть
разлогинивало живых людей.

Лечится устройством, а не бдительностью: один хеш, потолок на число полей,
TTL. Списка известных имён нарочно НЕТ — он жил бы в двух местах, и новое
событие в приложении молча пропадало бы.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_events_bounded.py -q
"""
from __future__ import annotations

import pytest

from app.routers import events


@pytest.mark.parametrize("имя", [
    "app_opened", "generation_succeeded", "onboarding_step_viewed", "a", "a1_b2",
])
def test_a_normal_event_name_is_kept_as_is(имя: str):
    """Настоящие имена приложения обязаны считаться каждое за себя.

    Без этой половины потолок можно было бы «выполнить», складывая вообще всё
    в одну корзину, — и аналитики бы не стало.
    """
    assert events._NAME_SHAPE.match(имя)


@pytest.mark.parametrize("имя", [
    "Имя",              # кириллица
    "App_Opened",       # заглавные
    "app opened",       # пробел
    "app-opened",       # дефис
    "1app",             # начинается с цифры
    "_app",             # начинается с подчёркивания
    "события:счёт",     # двоеточие — попытка влезть в чужое пространство ключей
    "a" * 49,           # длиннее предела
    "",
])
def test_a_name_of_the_wrong_shape_is_not_kept_as_is(имя: str):
    """Форма ограничивается, а не список имён.

    Двоеточие отдельно: пока счётчики жили по ключу на имя, имя с двоеточием
    попадало прямо в пространство ключей Redis рядом с сессиями.
    """
    assert not events._NAME_SHAPE.match(имя)


def test_the_overflow_bucket_is_not_a_valid_name():
    """Корзина не должна совпасть с настоящим именем, иначе счёт смешается."""
    assert not events._NAME_SHAPE.match(events._OVERFLOW)


def test_the_counters_are_one_key_with_a_ceiling_and_a_ttl():
    """Три свойства, на которых всё держится, — все три проверяются.

    Ключ один: иначе их число растёт с числом имён. Потолок есть: иначе растёт
    число полей. TTL есть: иначе ключ, который никто никогда не сотрёт.
    """
    assert events._COUNTS_KEY == "events:counts"
    assert isinstance(events._MAX_NAMES, int) and 0 < events._MAX_NAMES <= 1000
    assert isinstance(events._COUNTS_TTL, int) and events._COUNTS_TTL > 0


def test_nothing_writes_a_key_per_event_name_any_more():
    """Прямая проверка того, что старое устройство не вернулось.

    Оно возвращается одной строкой `incr(f"events:count:{name}")`, и заметить
    это на обзоре легко ровно до тех пор, пока не перестанешь смотреть.
    """
    import ast
    import pathlib

    дерево = ast.parse(pathlib.Path("app/routers/events.py").read_text())
    вызовы = {
        n.func.attr for n in ast.walk(дерево)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    # По исходнику разбором, а не поиском подстроки: строка `events:count:`
    # стоит в комментарии, объясняющем, как было, — и поиск ловил бы объяснение
    # вместо кода.
    assert "incr" not in вызовы, "счётчик снова пишется по ключу на имя события"
    assert "hincrby" in вызовы, "счётчики должны идти в один хеш"
    assert "expire" in вызовы, "на хеше счётчиков должен стоять TTL"
