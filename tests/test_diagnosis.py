"""Сервер сам говорит, всё ли у него в порядке.

Смысл этой проверки — чтобы отказ нашёл нас сам. Значит проверять нужно обе
стороны: что на беду диагноз ругается и что на обычную работу — молчит.
Диагноз, который ругается всегда, сторож научится игнорировать за неделю, и
тогда он хуже, чем никакого.

Правила проверяются точно, на подложенных числах. Подсчёт — в настоящем
Postgres и по приращению: таблица работ общая на всю базу, у разработчика там
свои прогоны, и абсолютные числа в тесте означали бы «проходит, пока никто не
генерировал».
"""
from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select

from app import main
from app.config import settings
from app.db import models as m
from app.db.repositories import wallet as wallet_repo
from app.db.session import connect, disconnect, get_factory
from app.services import diagnosis


def facts(**свои) -> dict:
    """Обычные сутки: работы идут, ничего не сломано."""
    return {"работ_за_сутки": 40, "удалось": 39, "отказов": 1,
            "оборвалось": 0, "не_вернули_денег": 0} | свои


# ─── Правила ────────────────────────────────────────────────────────────────

def test_an_ordinary_day_is_not_worth_waking_anyone():
    d = diagnosis.judge(facts(), ever=True)
    assert d.ok, f"поднял тревогу на ровном месте: {d.reasons}"
    assert d.status == "ok"


def test_a_bad_failure_rate_is_a_fault():
    d = diagnosis.judge(facts(удалось=30, отказов=10), ever=True)
    assert not d.ok
    assert any("не получается" in r for r in d.reasons), d.reasons


def test_a_few_failures_among_many_are_not():
    """Отказы бывают всегда: провайдер моргнул, человек прислал не то.

    Тревога на каждый из них — тревога каждый день.
    """
    assert diagnosis.judge(facts(удалось=99, отказов=1), ever=True).ok


def test_three_failures_in_a_row_at_the_start_of_the_day_is_a_fault():
    """Доля, а не число: три из трёх — это сломано, три из трёхсот — нет."""
    assert not diagnosis.judge(facts(работ_за_сутки=3, удалось=0, отказов=3), ever=True).ok
    assert diagnosis.judge(facts(работ_за_сутки=300, удалось=297, отказов=3), ever=True).ok


def test_an_interrupted_job_is_a_fault_even_alone():
    """За неё заплачено, и сама она не рассосётся."""
    d = diagnosis.judge(facts(оборвалось=1), ever=True)
    assert not d.ok
    assert any("оборвалась" in r for r in d.reasons), d.reasons


def test_money_not_returned_is_a_fault_even_alone():
    d = diagnosis.judge(facts(не_вернули_денег=1), ever=True)
    assert not d.ok
    assert any("деньги не вернулись" in r for r in d.reasons), d.reasons


def test_silence_where_there_used_to_be_work_is_a_fault():
    """Отказов нет, потому что и попыток нет: сломалось раньше генерации.

    По доле отказов этого не видно вовсе — она равна нулю, как в лучший день.
    """
    d = diagnosis.judge(facts(работ_за_сутки=0, удалось=0, отказов=0), ever=True)
    assert not d.ok
    assert any("ни одной работы" in r for r in d.reasons), d.reasons


def test_silence_on_a_fresh_install_is_not():
    """Первый день после выкатки. Работ не было никогда — и это не поломка."""
    assert diagnosis.judge(facts(работ_за_сутки=0, удалось=0, отказов=0), ever=False).ok


@pytest.mark.parametrize("n,ожидание", [
    (1, "1 работа оборвалась"), (2, "2 работы оборвались"), (5, "5 работ оборвались"),
    (11, "11 работ оборвались"), (21, "21 работа оборвалась"),
])
def test_the_alarm_is_written_in_russian(n, ожидание):
    """Эту строку человек читает ночью из письма, а не грепает."""
    d = diagnosis.judge(facts(оборвалось=n), ever=True)
    assert d.reasons[0].startswith(ожидание), d.reasons


def test_every_fault_is_told_in_words():
    """Сторож пришлёт эту строку письмом, и читать её будет человек ночью."""
    d = diagnosis.judge(facts(удалось=0, отказов=5, оборвалось=2,
                              не_вернули_денег=3), ever=True)
    assert len(d.reasons) == 3, d.reasons
    assert all(len(r) > 20 and not r.isdigit() for r in d.reasons)


# ─── Подсчёт ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def база():
    """Сессия без записи: всё подложенное откатывается.

    Именно откат, а не удаление в конце: диагноз считает по всей таблице, и
    упавший посреди теста прогон иначе оставил бы в базе разработчика вечно
    висящую работу — ровно то, на что он же и ругается.
    """
    await connect()
    async with get_factory()() as session:
        yield session
        await session.rollback()
    await disconnect()


async def _человек(session) -> m.User:
    user = m.User(kind="guest")
    session.add(user)
    await session.flush()
    await wallet_repo.grant(session, user.id, amount=100, bucket="free",
                            reason="signup", idempotency_key=f"diag:{user.id}")
    return user


@pytest.mark.asyncio
async def test_a_job_that_worked_adds_no_complaints(база):
    """Положительный путь: удачная работа не должна ничего добавить."""
    было = (await diagnosis.diagnose(база)).reasons
    user = await _человек(база)
    база.add(m.Generation(user_id=user.id, operation="text_to_image",
                          status="done", cost=15, request_params={}))
    await база.flush()

    стало = await diagnosis.diagnose(база)
    # Не равенство, а «ничего не прибавилось». Удачная работа умеет жалобу и
    # снять: если до неё за сутки не случилось ни одной, диагноз кричал о
    # тишине — и теперь замолкает, что правильно. Равенство ловило это как
    # провал, но только на базе, где работы были давно, а сегодня их нет.
    прибавилось = set(стало.reasons) - set(было)
    assert not прибавилось, f"удачная работа добавила жалоб: {прибавилось}"


@pytest.mark.asyncio
async def test_counting_sees_an_interrupted_job(база):
    было = (await diagnosis.diagnose(база)).facts["оборвалось"]
    user = await _человек(база)
    # Часы у базы, а не у машины: расхождение здесь и решает попадание в окно.
    now = await база.scalar(select(func.now()))
    база.add(m.Generation(
        user_id=user.id, operation="text_to_image", status="running", cost=15,
        request_params={},
        created_at=now - timedelta(minutes=settings.stale_generation_minutes + 5)))
    await база.flush()

    d = await diagnosis.diagnose(база)
    assert d.facts["оборвалось"] == было + 1, "оборвавшаяся работа не найдена"
    assert not d.ok


@pytest.mark.asyncio
async def test_a_job_still_within_its_time_is_not_counted_as_interrupted(база):
    """Иначе диагноз ругался бы на каждую работу, пока она рисуется."""
    было = (await diagnosis.diagnose(база)).facts["оборвалось"]
    user = await _человек(база)
    база.add(m.Generation(user_id=user.id, operation="text_to_image",
                          status="running", cost=15, request_params={}))
    await база.flush()

    assert (await diagnosis.diagnose(база)).facts["оборвалось"] == было


@pytest.mark.asyncio
async def test_counting_sees_money_we_owe(база):
    было = (await diagnosis.diagnose(база)).facts["не_вернули_денег"]
    user = await _человек(база)
    pay = f"pay_diag_{user.id[-8:]}"
    await wallet_repo.spend(база, user.id, cost=15, reason="generation",
                            ref_id=pay, idempotency_key=f"spend:{pay}")
    база.add(m.Generation(user_id=user.id, operation="text_to_image",
                          status="failed", cost=15,
                          request_params={"payment_id": pay}))
    await база.flush()

    d = await diagnosis.diagnose(база)
    assert d.facts["не_вернули_денег"] == было + 1, "долг не найден"
    assert not d.ok


@pytest.mark.asyncio
async def test_a_refund_that_went_through_is_not_a_debt(база):
    было = (await diagnosis.diagnose(база)).facts["не_вернули_денег"]
    user = await _человек(база)
    pay = f"pay_ok_{user.id[-8:]}"
    await wallet_repo.spend(база, user.id, cost=15, reason="generation",
                            ref_id=pay, idempotency_key=f"spend:{pay}")
    база.add(m.Generation(user_id=user.id, operation="text_to_image",
                          status="failed", cost=15,
                          request_params={"payment_id": pay}))
    await wallet_repo.refund(база, user.id, amount=15, bucket="free",
                             ref_id=pay, idempotency_key=f"refund:{pay}")
    await база.flush()

    assert (await diagnosis.diagnose(база)).facts["не_вернули_денег"] == было


@pytest.mark.asyncio
async def test_old_work_is_outside_the_day(база):
    """Окно — сутки. Позавчерашний отказ не должен портить сегодняшнюю долю."""
    было = (await diagnosis.diagnose(база)).facts["работ_за_сутки"]
    user = await _человек(база)
    now = await база.scalar(select(func.now()))
    база.add(m.Generation(user_id=user.id, operation="text_to_image",
                          status="failed", cost=0, request_params={},
                          created_at=now - timedelta(days=2)))
    await база.flush()

    assert (await diagnosis.diagnose(база)).facts["работ_за_сутки"] == было


# ─── Ручка для сторожа ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_without_a_token_the_endpoint_does_not_exist(monkeypatch):
    monkeypatch.setattr(settings, "watchdog_token", "")
    with pytest.raises(HTTPException) as beda:
        await main.pulse(token="", x_watchdog_token="")
    assert beda.value.status_code == 404


@pytest.mark.asyncio
async def test_a_wrong_token_gets_the_same_404(monkeypatch):
    """Не 403: иначе ответ сам сообщал бы, что за этим путём что-то есть."""
    monkeypatch.setattr(settings, "watchdog_token", "правильный")
    with pytest.raises(HTTPException) as beda:
        await main.pulse(token="подобранный", x_watchdog_token="")
    assert beda.value.status_code == 404


@pytest.mark.asyncio
async def test_trouble_answers_503(monkeypatch):
    """Код ответа, а не тело: тревогу поднимает любой монитор из коробки."""
    monkeypatch.setattr(settings, "watchdog_token", "ключ")
    monkeypatch.setattr(diagnosis, "diagnose", _подделка(ok=False))

    await connect()
    try:
        r = await main.pulse(token="ключ", x_watchdog_token="")
    finally:
        await disconnect()
    assert r.status_code == 503
    assert b"degraded" in r.body


@pytest.mark.asyncio
async def test_calm_answers_200(monkeypatch):
    monkeypatch.setattr(settings, "watchdog_token", "ключ")
    monkeypatch.setattr(diagnosis, "diagnose", _подделка(ok=True))

    await connect()
    try:
        r = await main.pulse(token="ключ", x_watchdog_token="")
    finally:
        await disconnect()
    assert r.status_code == 200


def _подделка(*, ok: bool):
    async def _diagnose(session):
        d = diagnosis.Diagnosis()
        if not ok:
            d.fault("для теста")
        return d
    return _diagnose


# ─── Никто не сторожит ──────────────────────────────────────────────────────

def test_production_without_a_watchdog_says_so(caplog, monkeypatch):
    import logging
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "watchdog_token", "")
    with caplog.at_level(logging.WARNING, logger="toontoon.watchdog"):
        main.warn_if_nobody_is_watching()
    assert any("WATCHDOG_TOKEN" in r.message for r in caplog.records), \
        "прод без сторожа поднялся молча"


def test_it_says_so_as_a_warning_not_an_error(caplog, monkeypatch):
    """Упущение, а не дыра. Ошибка на старте, которую видят каждый день,
    перестаёт значить «ошибка», и настоящая теряется среди привычных."""
    import logging
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "watchdog_token", "")
    with caplog.at_level(logging.ERROR, logger="toontoon.watchdog"):
        main.warn_if_nobody_is_watching()
    assert not caplog.records, "сказали голосом опасного флага"


def test_a_watched_production_start_is_quiet(caplog, monkeypatch):
    import logging
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "watchdog_token", "есть")
    with caplog.at_level(logging.WARNING, logger="toontoon.watchdog"):
        main.warn_if_nobody_is_watching()
    assert not caplog.records


def test_a_local_run_is_not_nagged(caplog, monkeypatch):
    """На своей машине сторожить некому и незачем."""
    import logging
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "watchdog_token", "")
    with caplog.at_level(logging.WARNING, logger="toontoon.watchdog"):
        main.warn_if_nobody_is_watching()
    assert not caplog.records


@pytest.mark.asyncio
async def test_the_token_may_come_in_a_header(monkeypatch):
    """Заголовком — потому что адрес запроса оседает в журналах прокси.

    Параметр в адресе тоже принимается: не всякий бесплатный монитор умеет
    слать заголовки, и ломать наблюдение ради находки уровня Low было бы
    плохой сделкой. Но заголовок обязан работать, иначе рекомендация в
    документации — пустой звук.
    """
    monkeypatch.setattr(settings, "watchdog_token", "ключ")
    monkeypatch.setattr(diagnosis, "diagnose", _подделка(ok=True))

    await connect()
    try:
        r = await main.pulse(token="", x_watchdog_token="ключ")
    finally:
        await disconnect()
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_a_wrong_header_gets_the_same_404(monkeypatch):
    """Неверный заголовок — тот же 404, что и неверный параметр."""
    monkeypatch.setattr(settings, "watchdog_token", "правильный")
    with pytest.raises(HTTPException) as beda:
        await main.pulse(token="", x_watchdog_token="подобранный")
    assert beda.value.status_code == 404
