"""Кадр как заказ, а не как ожидание.

Картинка рисуется от сорока секунд до минуты, и всё это время запрос висел
открытым. iOS усыпляет приложение через тридцать секунд в фоне и убивает
висящий запрос: человек сворачивал приложение, отвечал на уведомление или
просто гасил экран — и видел «ошибку генерации». Сервер при этом работу
доводил до конца, кадр ложился в историю, TOONTOON списывался. Хуже того,
человек жал «Try again» и платил второй раз за то же самое.

Поэтому запрос теперь принимает заказ и отвечает сразу, а кадр рисуется здесь,
в фоновой задаче, со своей сессией базы. Ровно так уже работает видео
(`video_gen`) — там это было вынужденно (пять минут никакой запрос не
переживёт), здесь оказалось нужно по той же причине, только менее очевидно.

Что бы ни случилось с приложением, работа доходит до конца: кадр ложится в
переписку и в историю, а при неудаче деньги возвращаются. Приложение узнаёт об
этом опросом `GET /api/generations/{id}` — тем же, которым узнаёт про видео.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import chat as chat_repo
from app.db.repositories import generations as generations_repo
from app.db.repositories import media as media_repo
from app.db.session import session_scope
from app.models.payment import Payment, PaymentStatus
from app.services import policy, prompt_style, wallet
from app.services import agent_analytics
from app.services import gpt as gpt_service
from app.services import generation as generation_core

logger = logging.getLogger(__name__)

# Держим ссылки на живые задачи: без этого сборщик мусора может забрать их
# посреди работы, и кадр исчезнет вместе с деньгами.
_RUNNING: set[asyncio.Task] = set()


def schedule(**kwargs) -> None:
    """Отправить кадр рисоваться и вернуться к человеку немедленно."""
    task = asyncio.create_task(run_image_job(**kwargs))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


async def redraw_if_photographic(
    db: AsyncSession,
    request: "generation_core.GenerationRequest",
    result,
    prompt: str,
    *,
    prefer: str | None,
):
    """Посмотреть на готовый кадр и, если это фотография, нарисовать заново.

    Один повтор, не больше: если и он вернулся снимком, отдаём что есть —
    бесконечно платить за упрямство модели нельзя, а человек ждёт картинку.

    Возвращает кадр, промпт, которым он в итоге сделан, и был ли повтор: в
    историю должно лечь то, что действительно уехало исполнителю, а в счёт —
    оба запроса. Человек платит один раз, мы платим дважды, и пока это нигде не
    записывалось, вопрос «сколько стоит упрямство модели» нельзя было задать.
    """
    if not await gpt_service.looks_photographic(result.data):
        return result, prompt, False

    # Повтор уходит к ДРУГОМУ исполнителю, а не к тому, который только что
    # провалился. Замер 26 августа на одном промпте: `gemini-3-pro` возвращает
    # рисунок два раза из трёх, `gemini-3.1-flash` — три из трёх. Второй заход к
    # тому же — это ставка на ту же монету, и однажды она легла так же:
    # «постер в стиле аниме» пришёл фотографией дважды подряд.
    other = (prompt_style.REDRAW_FALLBACK_PROVIDER
             if prefer != prompt_style.REDRAW_FALLBACK_PROVIDER else prefer)
    logger.info("Кадр приехал фотографией там, где просили рисунок — "
                "переделываем у %s", other)
    harder = f"{prompt}, {prompt_style.REDRAW_HARDER}"
    second = replace(request, prompt=harder)
    try:
        again = await generation_core.run(db, second, prefer=other)
    except Exception:  # noqa: BLE001 — повтор не удался, отдаём первый кадр
        logger.warning("Повтор не удался, отдаём первый кадр")
        return result, prompt, True
    # Первый кадр выброшен, но оплачен: складываем оба.
    if again.cost_usd is not None or result.cost_usd is not None:
        again.cost_usd = (again.cost_usd or 0) + (result.cost_usd or 0)
    return again, harder, True


async def reshoot_if_brand_leaked(
    db: AsyncSession,
    request: "generation_core.GenerationRequest",
    result,
    prompt: str,
    brands: list[str],
    *,
    prefer: str | None,
):
    """Посмотреть на готовый кадр и, если в нём чужая марка, снять заново.

    Замер: имя с образца уцелевает примерно в двух кадрах из пяти, всегда в
    мелкой печати — заголовок модель заменяет исправно, а шеврон размером в
    сто пикселей воспроизводит вместе с рисунком. Формулировкой это закрыть не
    удалось: слово называлось буквально, число не сдвинулось.

    Проверяем только когда на образце есть чужие марки. У большинства образцов
    их нет, и тогда не тратится ни вызова: риск здесь не в буквах, а в чужом
    знаке, и платить за проверку там, где знака нет, не за что.

    Один повтор, как и у переделки фотографии в рисунок: если марка уцелела и
    во второй раз, отдаём что есть. Человек ждёт картинку, а не нашу борьбу с
    упрямством модели.
    """
    if not brands:
        return result, prompt, False
    leaked = await gpt_service.brands_on_image(result.data, brands)
    if not leaked:
        return result, prompt, False

    logger.info("В кадр уехала чужая марка (%s) — переснимаем", ", ".join(leaked))
    harder = f"{prompt}, {prompt_style.brand_leaked(leaked)}"
    second = replace(request, prompt=harder)
    try:
        again = await generation_core.run(db, second, prefer=prefer)
    except Exception:  # noqa: BLE001 — повтор не удался, отдаём первый кадр
        logger.warning("Пересъёмка не удалась, отдаём первый кадр")
        return result, prompt, True
    if again.cost_usd is not None or result.cost_usd is not None:
        again.cost_usd = (again.cost_usd or 0) + (result.cost_usd or 0)
    return again, harder, True


async def run_image_job(**kwargs) -> None:
    """Сессия Amplitude на время работы над кадром (сторож, перерисовка, бренды)."""
    async with agent_analytics.session(agent_analytics.STUDIO, user_id=kwargs.get("user_id")):
        await _run_image_job(**kwargs)


async def _run_image_job(
    *,
    gen_id: str,
    user_id: str,
    payment_id: str,
    payment_amount: int,
    request: "generation_core.GenerationRequest",
    prompt: str,
    prefer: str | None,
    sample_brands: list[str],
    check_drawn: bool,
    from_chat: bool,
    said: str | None,
) -> None:
    """Нарисовать кадр и довести работу до конца, чем бы ни кончился запрос.

    Своя сессия базы: задача переживает и запрос, и приложение, а чужую сессию
    к этому моменту уже закрыли.

    При неудаче деньги возвращаются здесь же. Запись в бухгалтерии
    идемпотентна по идентификатору платежа, поэтому повторный возврат
    безвреден — а вот не вернуть вовсе значит взять деньги за то, чего человек
    не получил.
    """
    payment = Payment(payment_id=payment_id, status=PaymentStatus.PENDING,
                      amount=payment_amount)
    try:
        async with session_scope() as db:
            result = await generation_core.run(db, request, prefer=prefer)

            # Рисунок, вернувшийся фотографией, — брак, и человек за него уже
            # заплатил. Переделываем один раз за свой счёт, а не за его.
            redrawn = False
            if check_drawn:
                result, prompt, redrawn = await redraw_if_photographic(
                    db, request, result, prompt, prefer=prefer)
            result, prompt, brand_reshot = await reshoot_if_brand_leaked(
                db, request, result, prompt, sample_brands, prefer=prefer)

            await wallet.confirm(db, payment)
            asset = await media_repo.save_image(
                db, user_id=user_id, kind="generation", data=result.data)

            # И удалённую тоже: человек стёр работу, пока она рисовалась. Без
            # `include_deleted` запись оставалась `running`, сверка признавала
            # её оборвавшейся и возвращала деньги — а кадр провайдеру мы уже
            # оплатили. Тридцать таких кругов в час на человека.
            record = await generations_repo.get(db, gen_id, user_id=user_id, include_deleted=True)
            if record is None:
                logger.warning("Работа %s исчезла, пока рисовался кадр", gen_id)
                await media_repo.soft_delete(db, asset)
                return
            if record.status == "failed":
                # Сверка опередила: деньги уже возвращены. Кадр никому не
                # нужен, стираем — иначе это возврат плюс картинка.
                logger.warning("Работа %s уже признана неудачей — кадр стирается", gen_id)
                await media_repo.soft_delete(db, asset)
                return
            record.provider_id = result.provider_id
            record.provider_model = result.model
            record.provider_cost_usd = result.cost_usd
            if redrawn or brand_reshot:
                record.request_params = {
                    **(record.request_params or {}),
                    **({"redrawn": True} if redrawn else {}),
                    **({"brand_reshot": True} if brand_reshot else {}),
                }
            await generations_repo.mark_done(db, record, result_media_id=asset.id,
                                             prompt=prompt)
            if record.deleted_at is not None:
                # Удалил, пока рисовалось. Работа закрыта (деньги за неё
                # взяты честно — кадр был сделан), а файл стирается, как при
                # любом удалении.
                await media_repo.soft_delete(db, asset)
                return

            # В переписку попадает только то, что из неё и вышло. Кадр с
            # карточки на главной там был бы репликой без вопроса.
            if from_chat:
                if said:
                    await chat_repo.add_message(db, user_id=user_id, role="user",
                                                content=said)
                await chat_repo.add_message(db, user_id=user_id, role="assistant",
                                            generation_id=gen_id)
    except Exception as exc:  # noqa: BLE001 — задача обязана дожить до возврата денег
        logger.exception("Кадр %s не получился", gen_id)
        if policy.looks_like_moderation(repr(exc)):
            # Отказ модерации провайдера — в тот же счётчик, что и наши: кто
            # упорно пробует запрещённое, не должен жечь наш ключ.
            try:
                await policy.note_refusal(user_id)
            except Exception:  # noqa: BLE001 — счётчик не важнее возврата
                logger.warning("Не записался отказ модерации для %s", user_id)
        try:
            async with session_scope() as db:
                await wallet.cancel(db, user_id, payment)
                record = await generations_repo.get(db, gen_id, user_id=user_id)
                if record is not None:
                    await generations_repo.mark_failed(db, record, error=repr(exc)[:500])
        except Exception:
            logger.exception("Возврат за неудавшийся кадр %s не прошёл", gen_id)
