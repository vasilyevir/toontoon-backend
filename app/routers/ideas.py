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

from app.db.repositories import generations as generations_repo
from app.db.session import get_session as get_db_session
from app.deps import Context, required_context
from app.services import gpt as gpt_service

router = APIRouter(prefix="/api", tags=["ideas"])


class IdeasResponse(BaseModel):
    ideas: list[str]


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

    return IdeasResponse(ideas=await gpt_service.next_step_ideas(
        prompt=row.prompt or "",
        intent=(row.request_params or {}).get("intent"),
    ))
