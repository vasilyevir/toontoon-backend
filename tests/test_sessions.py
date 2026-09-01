"""Что происходит с выданными сессиями.

Две находки аудита, обе про то, что «отозвано» на деле не означало отозвано.

Смена пароля не отзывала ничего: сессии живут в Redis по тридцать дней и
переживали её. Человек, у которого увели аккаунт, менял пароль — а укравший
оставался внутри ещё на месяц.

А выход гасил не ту сессию: разбор идентификатора в `deps` предпочитал
заголовок, а в самом выходе — куку. Клиент с обоими работал под заголовочной,
а «выйти» гасило кукную; заголовочная оставалась жить.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_sessions.py -q
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.deps import _issued_before_the_cutoff, session_id_from
from app.models.session import Session
from app.models.user import AuthProvider


class Человек:
    """Ровно то поле, которое читает сверка."""

    def __init__(self, cutoff):
        self.sessions_valid_from = cutoff


def сессия(*, выдана: float) -> Session:
    return Session(sid="s", user_id="u", provider=AuthProvider.GUEST, issued_at=выдана)


# ─── отзыв при смене пароля ──────────────────────────────────────────────────


def test_nothing_is_revoked_until_a_password_changes():
    """Пустая отметка — обычное состояние: пароль никто не менял."""
    assert _issued_before_the_cutoff(сессия(выдана=time.time()), Человек(None)) is False


def test_a_session_issued_before_the_change_stops_working():
    момент = datetime.now(timezone.utc)
    старая = сессия(выдана=(момент - timedelta(minutes=5)).timestamp())
    assert _issued_before_the_cutoff(старая, Человек(момент)) is True


def test_a_session_issued_after_the_change_keeps_working():
    """Иначе смена пароля выкидывала бы и того, кто её только что сделал."""
    момент = datetime.now(timezone.utc)
    новая = сессия(выдана=(момент + timedelta(seconds=1)).timestamp())
    assert _issued_before_the_cutoff(новая, Человек(момент)) is False


def test_a_session_from_before_this_change_is_revoked_too():
    """Сессии, лежащие в Redis со времён до правки, поля не имеют.

    Умолчание у поля — ноль, а не «сейчас», и это единственный верный выбор:
    с «сейчас» такая сессия при каждом чтении выглядела бы свежевыданной, и
    смена пароля её бы не отзывала — то есть починка не работала бы ровно для
    тех, кто уже вошёл.
    """
    прежняя = Session.model_validate_json(
        '{"sid":"s","user_id":"u","provider":"guest"}')
    assert прежняя.issued_at == 0.0
    assert _issued_before_the_cutoff(прежняя, Человек(datetime.now(timezone.utc))) is True


def test_a_naive_cutoff_is_read_as_utc():
    """База может отдать время без пояса — сравнение не должно падать."""
    момент = datetime.now(timezone.utc).replace(tzinfo=None)
    старая = сессия(выдана=time.time() - 3600)
    assert _issued_before_the_cutoff(старая, Человек(момент)) is True


# ─── какую сессию имел в виду запрос ─────────────────────────────────────────


def test_the_header_wins_over_the_cookie():
    """Родной клиент шлёт Bearer намеренно, а кука может остаться от прежнего
    входа. Проиграв эту гонку, мы опознали бы человека как другого."""
    assert session_id_from("Bearer из-заголовка", "из-куки") == "из-заголовка"


def test_the_cookie_is_used_when_there_is_no_header():
    assert session_id_from(None, "из-куки") == "из-куки"


@pytest.mark.parametrize("заголовок", ["", "Bearer ", "Bearer    ", "Basic что-то"])
def test_an_empty_or_foreign_header_falls_back_to_the_cookie(заголовок: str):
    """`Bearer ` без значения — не ответ, а мусор: пустой заголовок не должен
    отменять живую куку."""
    assert session_id_from(заголовок, "из-куки") == "из-куки"


def test_nothing_at_all_means_nobody():
    assert session_id_from(None, None) is None
    assert session_id_from("", "") is None
