"""User repository.

A guest is an ordinary row with ``kind='guest'`` — that is the whole design.
Everything hanging off ``user_id`` (wallet, media, generations, chat) works for
them without a single branch, and signing in later becomes a merge rather than a
migration of half-built state.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m


async def create_guest(session: AsyncSession) -> m.User:
    user = m.User(kind="guest", last_seen_at=func.now())
    session.add(user)
    await session.flush()
    return user


async def get(session: AsyncSession, user_id: str) -> Optional[m.User]:
    user = await session.get(m.User, user_id)
    if user is None or user.deleted_at is not None:
        return None
    return user


async def get_by_email(session: AsyncSession, email: str) -> Optional[m.User]:
    stmt = select(m.User).where(
        m.User.email == email.strip().lower(), m.User.deleted_at.is_(None)
    )
    return await session.scalar(stmt)


async def get_by_identity(
    session: AsyncSession, provider: str, external_id: str
) -> Optional[m.User]:
    stmt = (
        select(m.User)
        .join(m.AuthIdentity, m.AuthIdentity.user_id == m.User.id)
        .where(
            m.AuthIdentity.provider == provider,
            m.AuthIdentity.external_id == external_id,
            m.User.deleted_at.is_(None),
        )
    )
    return await session.scalar(stmt)


async def touch(session: AsyncSession, user_id: str) -> None:
    """Update last activity — abandoned-guest cleanup reads this."""
    await session.execute(
        update(m.User).where(m.User.id == user_id).values(last_seen_at=func.now())
    )


async def attach_identity(
    session: AsyncSession, user_id: str, provider: str, external_id: str
) -> m.AuthIdentity:
    identity = m.AuthIdentity(user_id=user_id, provider=provider, external_id=external_id)
    session.add(identity)
    await session.flush()
    return identity


async def merge_guest_into(
    session: AsyncSession, guest_id: str, target_id: str
) -> None:
    """Move a guest's work into an account on sign-in.

    Everything is reassigned rather than copied, and the guest row survives with
    ``merged_into_user_id`` set — if a merge is ever disputed, the trail is there.
    Wallet balances are deliberately NOT merged here: that rule belongs to the
    subscription model and is still open (CH-17).
    """
    if guest_id == target_id:
        return

    # `PersonProfile` здесь не сразу: лица переезжали не всегда, и заметно это
    # стало, когда слияние из редкого случая (человек завёл аккаунт) стало
    # основным (человек восстановил покупку на новом телефоне). Работы
    # переезжали, а люди на них — нет: профиль оставался у мёртвого гостя, и
    # следующий кадр рисовался с чужим лицом или без лица вовсе.
    # `Subscription` — по той же причине: гость купил, потом вошёл по паролю
    # или через Apple, и оплаченное оставалось на мёртвом госте до повторного
    # предъявления чека.
    for model in (m.MediaAsset, m.Generation, m.ChatMessage, m.PersonProfile, m.Subscription):
        await session.execute(
            update(model).where(model.user_id == guest_id).values(user_id=target_id)
        )

    # Preferences move only if the account does not have its own answers yet —
    # the account's own onboarding is more recent and more true.
    existing = await session.get(m.UserPreferences, target_id)
    if existing is None:
        await session.execute(
            update(m.UserPreferences)
            .where(m.UserPreferences.user_id == guest_id)
            .values(user_id=target_id)
        )

    await session.execute(
        update(m.User)
        .where(m.User.id == guest_id)
        .values(merged_into_user_id=target_id, deleted_at=func.now())
    )
    await session.flush()


async def abandoned_guests(
    session: AsyncSession, *, older_than: timedelta, limit: int = 500
) -> list[m.User]:
    """Гости, которые не заходили дольше срока и у которых есть что стирать.

    Гость — это человек, не оставивший нам ни почты, ни покупки: спросить его,
    нужны ли ему ещё его снимки, невозможно. Поэтому у их фотографий есть срок,
    и он здесь считается.

    Кто НЕ попадает под уборку и почему:

    * зарегистрированные — их данные их, и у них есть кнопка удаления;
    * уже слитые в аккаунт (`merged_into_user_id`) — их снимки переехали к
      живому человеку и стирать их значило бы стереть чужое;
    * уже удалённые;
    * те, у кого не осталось ни одного файла, — уборке там делать нечего, а
      без этого условия запрос возвращал бы одних и тех же людей вечно.

    Время берётся у базы: реплик несколько, часы у них расходятся, а решение о
    чужих фотографиях не должно зависеть от того, где выполнился запрос.
    """
    db_now = await session.scalar(select(func.now()))
    cutoff = (db_now or datetime.now(timezone.utc)) - older_than

    есть_файлы = (
        select(m.MediaAsset.id)
        .where(m.MediaAsset.user_id == m.User.id, m.MediaAsset.deleted_at.is_(None))
        .exists()
    )
    stmt = (
        select(m.User)
        .where(
            m.User.kind == "guest",
            m.User.deleted_at.is_(None),
            m.User.merged_into_user_id.is_(None),
            # Ни разу не заходившие считаются по дате заведения: `last_seen_at`
            # ставится на первом же запросе, но пустым он остаться может.
            func.coalesce(m.User.last_seen_at, m.User.created_at) < cutoff,
            есть_файлы,
        )
        .order_by(func.coalesce(m.User.last_seen_at, m.User.created_at))
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def forget_everything_of(session: AsyncSession, user_id: str) -> None:
    """Стереть тексты человека: промпты, переписку, имена профилей, ссылки.

    Файлы стирает `media.erase_everything_of`; до этой функции всё остальное
    оставалось. В промпте работы по замыслу стоят имена из профилей — дети,
    партнёры, — и публичная ссылка на неё продолжала открываться после
    «удалить аккаунт». Строки работ остаются: на них смотрит книга проводок.
    Переписку удаляем целиком — на неё не ссылается никто.
    """
    await session.execute(
        update(m.Generation)
        .where(m.Generation.user_id == user_id)
        .values(prompt=None, request_params={}, share_id=None, error=None)
    )
    await session.execute(
        update(m.PersonProfile)
        .where(m.PersonProfile.user_id == user_id)
        .values(name="", media_ids=[], reference_ids=[], deleted_at=func.now())
    )
    await session.execute(delete(m.ChatMessage).where(m.ChatMessage.user_id == user_id))
    await session.flush()
