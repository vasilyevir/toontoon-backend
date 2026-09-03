"""Деньги: три дыры аудита 2 сентября 2026, закрытые и закреплённые.

Идут в настоящий Postgres, как и остальные тесты кошелька.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_money_after_audit.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.config import settings
from app.db import models as m
from app.db.repositories import generations as generations_repo
from app.db.repositories import wallet as wallet_repo
from app.db.session import connect, disconnect, get_factory
from app.services import image_job, wallet

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db():
    await connect()
    async with get_factory()() as session:
        user = m.User(kind="guest")
        session.add(user)
        await session.flush()
        await session.commit()
        uid = user.id
        yield session, user
        await session.rollback()
        for model in (m.Generation, m.MediaAsset, m.WalletLedger, m.WalletBalance):
            await session.execute(delete(model).where(model.user_id == uid))
        await session.execute(delete(m.User).where(m.User.id == uid))
        await session.commit()
    await disconnect()


async def test_deleting_a_work_while_it_renders_does_not_earn_a_refund(db, monkeypatch):
    """Удалил из истории, пока рисуется → раньше задача не находила запись,
    оставляла `running`, и сверка возвращала деньги за кадр, который
    провайдеру мы уже оплатили. Тридцать таких кругов в час на человека.
    Теперь работа закрывается, файл стирается, возврата нет."""
    session, user = db
    await wallet_repo.grant(session, user.id, amount=15, bucket="free", reason="signup",
                            idempotency_key=f"t:seed:{user.id}")
    pay = f"pay_t_{user.id[-8:]}"
    await wallet_repo.spend(session, user.id, cost=15, ref_id=pay, idempotency_key=f"spend:{pay}")
    gen = await generations_repo.create(
        session, user_id=user.id, operation="image_to_image", status="running",
        request_params={"payment_id": pay, "type": "image"}, cost=15)
    gen.created_at = datetime.now(timezone.utc) - timedelta(minutes=31)
    await session.flush()
    await generations_repo.soft_delete(session, gen)
    await session.commit()
    gen_id = gen.id

    стёрто = []

    async def fake_run(db_, request, *, prefer=None):
        return SimpleNamespace(data=b"PNG", provider_id="fal_nano_edit", model="x",
                               cost_usd=0.04, duration_ms=1)

    async def fake_save(db_, *, user_id, kind, data):
        # Настоящая строка: на неё ссылается работа, и внешний ключ это проверит.
        asset = m.MediaAsset(user_id=user_id, kind=kind, storage_key="t/fake.png",
                             mime="image/png", bytes=len(data))
        db_.add(asset)
        await db_.flush()
        стёрто.append(("создан", asset.id))
        return asset

    async def fake_soft_delete(db_, asset):
        стёрто.append(("стёрт", asset.id))

    monkeypatch.setattr(image_job.generation_core, "run", fake_run)
    monkeypatch.setattr(image_job.media_repo, "save_image", fake_save)
    monkeypatch.setattr(image_job.media_repo, "soft_delete", fake_soft_delete)

    await image_job.run_image_job(
        gen_id=gen_id, user_id=user.id, payment_id=pay, payment_amount=15,
        request=SimpleNamespace(), prompt="p", prefer=None, sample_brands=[],
        check_drawn=False, from_chat=False, said=None)

    row = await session.get(m.Generation, gen_id)
    await session.refresh(row)
    assert row.status == "done", f"ожидал done, получил {row.status}"
    assert [д for д, _ in стёрто] == ["создан", "стёрт"], "файл удалённой работы должен стереться"

    before = (await wallet_repo.balance(session, user.id)).total
    settled = await wallet.settle_owed(session)
    await session.commit()
    after = (await wallet_repo.balance(session, user.id)).total
    assert after == before and not [r for r in settled if r["generation_id"] == gen_id]


async def test_a_refund_goes_back_to_the_bucket_it_was_taken_from(db):
    """Квота подписки сгорает, бесплатная — нет и ограничена потолком. Возврат
    в бесплатную конвертировал 850 сгораемых в 850 вечных, мимо потолка, и
    ежедневная награда переставала начисляться. Теперь — откуда взято."""
    session, user = db
    await wallet_repo.reset_subscription_quota(session, user.id, quota=850,
                                               idempotency_key=f"t:quota:{user.id}")
    await wallet_repo.grant(session, user.id, amount=20, bucket="free", reason="signup",
                            idempotency_key=f"t:seed:{user.id}")
    pays = []
    for i in range(10):
        pay = f"pay_t_{i}_{user.id[-6:]}"
        pays.append(pay)
        await wallet_repo.spend(session, user.id, cost=85, ref_id=pay,
                                idempotency_key=f"spend:{pay}")
    # Одиннадцатый — через границу корзин: 5 из квоты (квота уже 0) — нет,
    # квота пуста, значит 20 из бесплатных. Делаем его 10 монет, чтобы было
    # что вернуть в обе корзины у смешанного платежа ниже.
    b = await wallet_repo.balance(session, user.id)
    assert b.sub == 0 and b.free == 20
    for pay in pays:
        await wallet.cancel(session, user.id,
                            wallet.Payment(payment_id=pay, status=wallet.PaymentStatus.PENDING, amount=85))
    b = await wallet_repo.balance(session, user.id)
    assert (b.sub, b.free) == (850, 20), f"sub={b.sub} free={b.free}"

    # Смешанный платёж: 855 при квоте 850 и 20 бесплатных — 850 из sub, 5 из free.
    pay = f"pay_t_mix_{user.id[-6:]}"
    await wallet_repo.spend(session, user.id, cost=855, ref_id=pay, idempotency_key=f"spend:{pay}")
    b = await wallet_repo.balance(session, user.id)
    assert (b.sub, b.free) == (0, 15)
    await wallet.cancel(session, user.id,
                        wallet.Payment(payment_id=pay, status=wallet.PaymentStatus.PENDING, amount=855))
    # И второй раз — ничего не добавляет.
    await wallet.cancel(session, user.id,
                        wallet.Payment(payment_id=pay, status=wallet.PaymentStatus.PENDING, amount=855))
    b = await wallet_repo.balance(session, user.id)
    assert (b.sub, b.free) == (850, 20), f"sub={b.sub} free={b.free}"
