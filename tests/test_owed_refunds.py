"""Сверка: деньги за работу, которая не получилась, а возврат не дошёл.

Обычно возвращает сама фоновая задача, сразу после неудачи. Этот путь — про
то, что случилось, когда возвращать было нечем: база легла ровно в тот момент,
процесс убили посреди возврата. Тогда человек заплатил, ничего не получил, и
след остался только в журнале приложения — то есть нигде.

Тесты идут в настоящий Postgres: запрос лезет в JSONB через ->>, и написать
его так, чтобы он молча ничего не находил, легче всего. Проверять такое на
подделке базы бессмысленно — подделка согласится с чем угодно.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.db.session import connect, disconnect, get_factory
from app.db import models as m
from app.db.repositories import wallet as wallet_repo
from app.services import wallet


@pytest_asyncio.fixture
async def unlucky():
    """Человек, чья работа не получилась, а деньги не вернулись."""
    await connect()
    async with get_factory()() as session:
        user = m.User(kind="guest")
        session.add(user)
        await session.flush()

        # Заплатил: проводка есть, деньги ушли.
        await wallet_repo.grant(session, user.id, amount=10, bucket="free",
                                reason="signup", idempotency_key=f"seed:{user.id}")
        pay = f"pay_{user.id[-8:]}"
        await wallet_repo.spend(session, user.id, cost=3, reason="generation",
                                ref_id=pay, idempotency_key=f"spend:{pay}")

        # Работа не получилась. Возврата в книге нет — задача до него не дожила.
        gen = m.Generation(user_id=user.id, operation="text_to_image", status="failed",
                           cost=3, request_params={"payment_id": pay})
        session.add(gen)
        await session.flush()
        await session.commit()

        yield session, user, gen, pay

        await session.execute(delete(m.Generation).where(m.Generation.user_id == user.id))
        await session.execute(delete(m.WalletLedger).where(m.WalletLedger.user_id == user.id))
        await session.execute(delete(m.User).where(m.User.id == user.id))
        await session.commit()
    await disconnect()


@pytest.mark.asyncio
async def test_the_query_finds_money_we_owe(unlucky):
    session, user, gen, pay = unlucky
    owed = await wallet_repo.owed_refunds(session)
    mine = [r for r in owed if r["generation_id"] == gen.id]
    assert mine, "неудавшаяся работа без возврата не найдена"
    assert mine[0] == {"generation_id": gen.id, "user_id": user.id,
                       "amount": 3, "payment_id": pay}


@pytest.mark.asyncio
async def test_settling_gives_the_money_back(unlucky):
    session, user, gen, _ = unlucky
    before = (await wallet_repo.balance(session, user.id)).total

    settled = await wallet.settle_owed(session)
    await session.commit()

    assert gen.id in [r["generation_id"] for r in settled]
    after = (await wallet_repo.balance(session, user.id)).total
    assert after == before + 3, f"вернули не столько: было {before}, стало {after}"


@pytest.mark.asyncio
async def test_settling_twice_pays_once(unlucky):
    """Иначе сверка по расписанию раздавала бы деньги каждый свой запуск."""
    session, user, gen, _ = unlucky

    await wallet.settle_owed(session)
    await session.commit()
    once = (await wallet_repo.balance(session, user.id)).total

    again = await wallet.settle_owed(session)
    await session.commit()
    twice = (await wallet_repo.balance(session, user.id)).total

    assert twice == once, "второй прогон заплатил ещё раз"
    assert gen.id not in [r["generation_id"] for r in again], "работа осталась в долгах"


@pytest.mark.asyncio
async def test_a_generation_that_worked_is_not_refunded(unlucky):
    """Сверка смотрит на статус, а не на наличие платежа."""
    session, user, gen, _ = unlucky
    gen.status = "done"
    await session.flush()

    owed = await wallet_repo.owed_refunds(session)
    assert gen.id not in [r["generation_id"] for r in owed]


@pytest.mark.asyncio
async def test_a_free_generation_is_not_refunded(unlucky):
    """Возврат нуля — проводка ни о чём, а в книге она навсегда."""
    session, user, gen, _ = unlucky
    gen.cost = 0
    await session.flush()

    owed = await wallet_repo.owed_refunds(session)
    assert gen.id not in [r["generation_id"] for r in owed]


@pytest.mark.asyncio
async def test_records_from_before_this_change_are_left_alone(unlucky):
    """Сразу после выкатки таких большинство: платёж в них не записан.

    Вернуть по ним нельзя — неизвестно, по какому платежу проводить, — и
    попытаться значило бы либо упасть, либо заплатить не тому. Молча пропустить
    здесь правильно: ошибка не в них, а в том, что мы начали связывать поздно.
    """
    session, user, gen, _ = unlucky
    gen.request_params = {"tile_id": "birthday"}   # как писалось раньше
    await session.flush()

    owed = await wallet_repo.owed_refunds(session)
    assert gen.id not in [r["generation_id"] for r in owed]


@pytest.mark.asyncio
async def test_a_refund_that_did_go_through_is_not_repeated(unlucky):
    """Обычный случай: задача вернула деньги сама. Сверке тут делать нечего."""
    session, user, gen, pay = unlucky
    await wallet_repo.refund(session, user.id, amount=3, bucket="free",
                             ref_id=pay, idempotency_key=f"refund:{pay}")
    await session.flush()

    owed = await wallet_repo.owed_refunds(session)
    assert gen.id not in [r["generation_id"] for r in owed]


# ─── Оборвавшиеся: убиты раньше, чем успели себя пометить ────────────────────
# Выкатка посреди генерации — обычное дело, и до этой сверки такая работа
# висела в `running` вечно: деньги списаны, человек видит бесконечное
# «рисуется», а проверка выше её не видит, потому что смотрит на `failed`.

@pytest_asyncio.fixture
async def interrupted():
    """Работа, чей процесс убили на середине."""
    from datetime import timedelta
    from sqlalchemy import func, select as sa_select

    await connect()
    async with get_factory()() as session:
        user = m.User(kind="guest")
        session.add(user)
        await session.flush()
        await wallet_repo.grant(session, user.id, amount=10, bucket="free",
                                reason="signup", idempotency_key=f"seed2:{user.id}")
        pay = f"pay2_{user.id[-8:]}"
        await wallet_repo.spend(session, user.id, cost=3, reason="generation",
                                ref_id=pay, idempotency_key=f"spend:{pay}")

        # Часы берём у базы: тест не должен зависеть от часов машины.
        db_now = await session.scalar(sa_select(func.now()))
        gen = m.Generation(user_id=user.id, operation="text_to_image", status="running",
                           cost=3, request_params={"payment_id": pay},
                           created_at=db_now - timedelta(hours=2))
        session.add(gen)
        await session.flush()
        await session.commit()

        yield session, user, gen, db_now

        await session.execute(delete(m.Generation).where(m.Generation.user_id == user.id))
        await session.execute(delete(m.WalletLedger).where(m.WalletLedger.user_id == user.id))
        await session.execute(delete(m.User).where(m.User.id == user.id))
        await session.commit()
    await disconnect()


@pytest.mark.asyncio
async def test_an_interrupted_job_gets_its_money_back(interrupted):
    session, user, gen, _ = interrupted
    before = (await wallet_repo.balance(session, user.id)).total

    settled = await wallet.settle_owed(session)
    await session.commit()

    assert gen.status == "failed", "работа осталась вечно рисующейся"
    assert gen.id in [r["generation_id"] for r in settled]
    assert (await wallet_repo.balance(session, user.id)).total == before + 3


@pytest.mark.asyncio
async def test_a_job_still_running_is_left_alone(interrupted):
    """Самое опасное здесь — отменить чужую работу на середине.

    Кадр, который вот-вот придёт, объявленный неудачей, — это хуже, чем
    подождать лишние двадцать минут: человек получит возврат и не получит
    картинку, за которую уже заплатил.
    """
    from datetime import timedelta
    session, user, gen, db_now = interrupted
    gen.created_at = db_now - timedelta(minutes=2)   # началась две минуты назад
    await session.flush()

    settled = await wallet.settle_owed(session)
    await session.commit()

    assert gen.status == "running", "сверка оборвала работу, которая ещё шла"
    assert gen.id not in [r["generation_id"] for r in settled]


@pytest.mark.asyncio
async def test_the_threshold_is_the_setting_not_a_number_in_the_code(interrupted):
    """Иначе подобрать его на живых отказах стоило бы релиза."""
    from datetime import timedelta
    from app.config import settings

    session, user, gen, db_now = interrupted
    gen.created_at = db_now - timedelta(minutes=settings.stale_generation_minutes - 1)
    await session.flush()
    await wallet.settle_owed(session)
    assert gen.status == "running", "порог не соблюдается: оборвало раньше срока"

    gen.created_at = db_now - timedelta(minutes=settings.stale_generation_minutes + 1)
    await session.flush()
    await wallet.settle_owed(session)
    assert gen.status == "failed", "порог не соблюдается: не оборвало после срока"
