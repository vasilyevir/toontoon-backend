"""Снимок запуска обязан отдавать то же, что и отдельные маршруты.

`/app/bootstrap` существует ради одного: открыть первый экран одним запросом
вместо четырёх. Значит он отдаёт то же самое, что маршруты каталога и
переписки, — и любое расхождение здесь не оптимизация, а тихая поломка.

Тихая буквально. У приложения поля обязательные: JSONDecoder на недостающем
ключе бросает исключение и не отдаёт НИЧЕГО, а `try?` в AuthManager его
глотает. Снимок молча становится пустым, приложение добирает всё отдельными
запросами и выглядит исправным.

Так и было. Дважды подряд, одной и той же причиной — своя копия полей рядом с
настоящим сборщиком:

  1. `examples` у стиля. Разбор падал на первом же стиле витрины, и снимок не
     доходил до приложения ни разу за всё время его существования.
  2. `result_url` у реплики. Вылезло сразу после починки первого: кадр приходил
     с одним идентификатором работы, и показать его было нечем. Не хватало и
     `attachment_url` — приложенный снимок пропадал так же.

Отсюда тест. Он сравнивает не значения, а набор полей: разойтись они могут
только если кто-то снова напишет копию вместо вызова.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.db.session import connect, disconnect, get_factory
from app.db import models as m
from app.db.repositories import chat as chat_repo
from app.routers import app_meta, chat as chat_router, styles as styles_router


@pytest_asyncio.fixture
async def thread():
    """Человек, у которого в переписке есть и слова, и кадр."""
    await connect()
    async with get_factory()() as session:
        user = m.User(kind="guest")
        session.add(user)
        await session.flush()

        gen = m.Generation(user_id=user.id, operation="text_to_image",
                           status="done", cost=1)
        session.add(gen)
        await session.flush()

        await chat_repo.add_message(session, user_id=user.id, role="user",
                                    content="Хочу постер")
        await chat_repo.add_message(session, user_id=user.id, role="assistant",
                                    generation_id=gen.id)
        await session.commit()

        yield session, user

        await session.execute(delete(m.ChatMessage).where(m.ChatMessage.user_id == user.id))
        await session.execute(delete(m.Generation).where(m.Generation.user_id == user.id))
        await session.execute(delete(m.User).where(m.User.id == user.id))
        await session.commit()
    await disconnect()


@pytest.mark.asyncio
async def test_the_thread_looks_the_same_in_both_places(thread):
    session, user = thread
    rows = await chat_repo.list_messages(session, user, limit=20)
    assert rows, "переписка пуста — тест ничего не проверил"

    built = await chat_router.serialize_thread(session, rows)
    fields = {f for msg in built for f in msg.model_dump(mode="json")}

    # Кадр без ссылки на картинку показать нечем — это и была поломка.
    assert "result_url" in fields
    assert "attachment_url" in fields
    assert "generation_id" in fields

    carrying = [msg for msg in built if msg.generation_id]
    assert carrying, "реплика с кадром не нашлась — тест ничего не проверил"


@pytest.mark.asyncio
async def test_a_style_looks_the_same_in_both_places(thread):
    session, _ = thread
    from app.db.repositories import styles as styles_repo

    rows = await styles_repo.list_styles(session, home_only=True, limit=1)
    if not rows:
        pytest.skip("витрина пуста")
    row = rows[0]

    из_снимка = set(app_meta._style(row))
    из_каталога = set(styles_router._style_out(row).model_dump())
    assert из_снимка == из_каталога, (
        f"поля разошлись: снимку не хватает {из_каталога - из_снимка}, "
        f"лишнее {из_снимка - из_каталога}"
    )
    # Именно на нём всё и падало.
    assert "examples" in из_снимка
