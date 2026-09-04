"""Разбор фразы человека по слотам — для сопровождения в чате.

Приложение спрашивает по одному, но человек отвечает не по одному: на вопрос
про надпись он говорит про цвета, а первой же фразой закрывает половину полей.
Спрашивать после этого о сказанном — верный способ выглядеть глухим.

Разбор живёт здесь, а не в приложении, по двум причинам. Ключ к модели на
телефоне не хранится. И правило «что считать сказанным» одно на всех: приложение
обновляется неделями, сервер — сегодня.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.deps import Context, costs_money, required_context
from app.services import conversation, gpt
from app.services import agent_analytics

router = APIRouter(prefix="/api/guided", tags=["guided"])


class ParseRequest(BaseModel):
    text: str = Field(max_length=1000)
    # Какие поля ещё не заполнены. Спрашивать про заполненные незачем, а список
    # зависит от назначения — его знает приложение, не сервер.
    slots: list[str] = Field(default_factory=list, max_length=12)


class ParseResponse(BaseModel):
    filled: dict[str, str] = Field(default_factory=dict)


@router.post("/parse")
@agent_analytics.in_session(agent_analytics.STUDIO)
async def parse(
    body: ParseRequest,
    _: Context = Depends(costs_money),
) -> ParseResponse:
    """Что из незаполненного человек уже сказал.

    Пустой ответ — законный и частый. Если модель недоступна, он же: разговор
    задаст свои вопросы, как задавал бы без разбора, и человек не увидит
    поломки — только лишний вопрос.
    """
    return ParseResponse(filled=await gpt.extract_slots(body.text, body.slots))


class IntentPlan(BaseModel):
    """Как устроен разговор про одно назначение."""

    # Порядок вопросов. `photo` — не слот разбора, а вопрос про снимок человека,
    # и он всегда первый: остальное описывает кадр, а он решает, кто в кадре.
    fields: list[str]
    # Вес каждого поля в готовности, в процентах. Вместе со снимком — сто.
    weights: dict[str, int]
    # Сколько снимков нужно, чтобы обещание выполнилось.
    photos_needed: int


class PlanResponse(BaseModel):
    intents: dict[str, IntentPlan]


@router.get("/plan")
async def plan(_: Context = Depends(required_context)) -> PlanResponse:
    """Порядок вопросов и веса — по одному на каждое назначение.

    Раньше это жило в приложении и на сервере двумя копиями: свободный чат
    спрашивал по своему порядку, ведомый разговор по своему, и разъехаться они
    могли молча — оба считали бы себя правыми. Теперь копия одна, а приложение
    её спрашивает.

    Ответ не зависит от человека и меняется вместе с сервером, поэтому его
    можно взять один раз за запуск и запомнить. Приложение обязано пережить и
    ответ, которого не понимает: поле, которого оно ещё не знает, оно
    пропустит, а не покажет пустым вопросом.
    """
    return PlanResponse(intents={
        intent: IntentPlan(
            fields=list(fields),
            weights=conversation.WEIGHTS[intent],
            photos_needed=conversation.photos_needed(intent),
        )
        for intent, fields in conversation.ORDER.items()
    })
