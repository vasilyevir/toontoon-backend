"""Что можно сделать с готовым кадром — предложениями, а не вопросами.

Разговор спрашивает «что должно быть на фоне?», и человек должен придумать ответ
с нуля, глядя в пустоту. Это и есть самая дорогая часть: не касания, а
формулировка. Выбрать всегда легче, чем сочинить.

Поэтому после кадра приходят четыре готовые правки, написанные под этот самый
кадр: «смени палитру на неоновый синий», «убери фон, оставь плашки». Каждая —
законченная инструкция, которую можно отправить как есть.

Откуда мы знаем, что на кадре: из промпта, которым он сделан. Он лежит в базе
целиком и описывает картинку подробнее, чем её описал бы человек. Смотреть на
сам файл не нужно — это лишние деньги и лишняя зависимость от того, умеет ли
выбранная модель читать картинки.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MediaAsset
from app.config import settings
from app.db.repositories import chat as chat_repo
from app.db.repositories import generations as generations_repo
from app.db.repositories import profiles as profiles_repo
from app.storage import get_storage
from app.db.session import get_session as get_db_session
from app.deps import Context, required_context
from app.services import gpt as gpt_service

router = APIRouter(prefix="/api", tags=["ideas"])


class IdeasResponse(BaseModel):
    # Слово о кадре с открытым вопросом: разговор не должен заканчиваться
    # картинкой. Человек получил её и остаётся один на один с пустым полем.
    remark: str = ""
    ideas: list[str]


@router.get("/ideas/starters", response_model=IdeasResponse)
async def starters(
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> IdeasResponse:
    """С чего начать, когда ещё ничего нет.

    Пустой экран и мигающая строка ввода — это чистый лист, а лист и есть самая
    дорогая часть работы. Эти четыре фразы отвечают на единственный вопрос,
    который у человека сейчас есть: «а что тут вообще можно?»

    Если профиль собран, идеи пишутся по его лицу: «сделаю тебя в стилистике
    комикса» цепляется за человека сильнее, чем общий список услуг, — а лицо у
    нас уже есть, и второй раз просить его незачем.
    """
    user, _ = ctx
    profile = await profiles_repo.get_default(db, user.id)
    faces = profiles_repo.references(profile, limit=1) if profile else []
    if faces:
        asset = await db.get(MediaAsset, faces[0])
        data = await get_storage().get(asset.storage_key) if asset else None
        if data:
            _, ideas = await gpt_service.photo_remark_and_ideas(data)
            if ideas:
                return IdeasResponse(ideas=ideas)

    return IdeasResponse(ideas=await gpt_service.starter_ideas())


@router.get("/media/{media_id}/ideas", response_model=IdeasResponse)
async def photo_ideas(
    media_id: str,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> IdeasResponse:
    """Что можно сделать со снимком, который человек только что приложил.

    Снимок — это уже просьба, просто без слов, и отвечать на неё пустым полем
    ввода значит вернуть человека к чистому листу. Здесь смотрит зрение: идеи
    должны цепляться за то, что на кадре, — иначе они подошли бы к любому
    снимку, а такие не читают.
    """
    user, _ = ctx
    asset = await db.get(MediaAsset, media_id)
    if asset is None or asset.user_id != user.id or asset.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")

    data = await get_storage().get(asset.storage_key)
    _, ideas = await gpt_service.photo_remark_and_ideas(data or b"")
    return IdeasResponse(ideas=ideas)


@router.get("/generations/{gen_id}/ideas", response_model=IdeasResponse)
async def ideas(
    gen_id: str,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> IdeasResponse:
    """Четыре правки к этому кадру, каждая — готовая фраза.

    Пустой список — законный ответ: модель недоступна или кадр ещё не готов.
    Приложение тогда показывает обычные кнопки уточнений, а не пустое место.
    """
    user, _ = ctx
    row = await generations_repo.get(db, gen_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")

    # Слово ложится в переписку — значит, и язык у него от переписки. Промпт
    # кадра языка не подскажет: он всегда английский, это машинерия.
    window = await chat_repo.context_messages(db, user, limit=settings.chat_context_messages)
    spoken = " ".join(msg["content"] for msg in window if msg.get("role") == "user")
    remark, ideas = await gpt_service.next_step_ideas(
        prompt=row.prompt or "",
        intent=(row.request_params or {}).get("intent"),
        spoken=spoken,
    )
    # Слово ложится в переписку, идеи — нет: слово это разговор, и завтра оно
    # должно быть на месте, а идеи живут до выбора.
    if remark:
        await chat_repo.add_message(db, user_id=user.id, role="assistant", content=remark)
    return IdeasResponse(remark=remark, ideas=ideas)
