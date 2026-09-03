"""Заказ на кадр, который переживает перезапуск.

Пока кадр рисовался задачей внутри веб-процесса (`image_job.schedule`), любой
деплой или падение пода уносил всё, что было в работе: человек видел
«готовится» до получаса, потом отказ, деньги возвращала сверка при следующем
старте. Первый же релиз под нагрузкой сделал бы это заметным.

Здесь заказ становится ДАННЫМИ, а не корутиной. Всё, что нужно, чтобы
нарисовать кадр, записывается в строку работы (`request_params["job"]`) —
без байтов: снимки называются по id медиа и перечитываются из хранилища.
Идентификатор работы кладётся в Redis-список; отдельный процесс
(`app.worker`) забирает его и зовёт тот же `run_image_job`, что и раньше.
При старте воркер подбирает всё, что осталось со статусом queued/running и
спецификацией в строке, — ради этого всё и затевалось.

Деньги при повторе в безопасности: списание и возврат идемпотентны по
`payment_id` (`wallet.py`), второй прогон той же работы ничего не удвоит.

Режим выбирается настройкой `worker_mode`; умолчание — прежнее `inline`, чтобы
включать сознательно, вместе с воркером в чарте.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import models as m
from app.redis_client import get_client
from app.services import image_job
from app.services.generation.operations import GenerationRequest, Operation
from app.storage import get_storage

logger = logging.getLogger(__name__)

SPEC_KEY = "job"


@dataclass
class JobSpec:
    """Всё, что нужно воркеру, чтобы нарисовать кадр, — без байтов."""

    gen_id: str
    user_id: str
    payment_id: str
    payment_amount: int
    operation: str
    prompt: str
    negative_prompt: Optional[str]
    photo_media_id: Optional[str]
    extra_media_ids: list[str]
    params: dict
    style_prompt: str            # `prompt` kwarg у run_image_job — то, что уехало исполнителю
    prefer: Optional[str]
    sample_brands: list[str] = field(default_factory=list)
    check_drawn: bool = False
    from_chat: bool = True
    said: Optional[str] = None

    def to_record(self) -> dict:
        return asdict(self)

    @classmethod
    def from_record(cls, rec: dict) -> "JobSpec":
        return cls(**{k: rec.get(k) for k in cls.__dataclass_fields__})  # type: ignore[arg-type]


def spec_from_call(*, photo_media_id: Optional[str], extra_media_ids: list[str],
                   **kwargs: Any) -> JobSpec:
    """Спецификация из тех же аргументов, что уходят в `run_image_job`.

    `request` берётся из тех же kwargs, а не отдельным параметром: вызывающий
    держит один словарь на оба пути, и второе имя для того же объекта — это
    «multiple values for keyword argument» на первом же заказе.
    """
    request: GenerationRequest = kwargs["request"]
    return JobSpec(
        gen_id=kwargs["gen_id"], user_id=kwargs["user_id"],
        payment_id=kwargs["payment_id"], payment_amount=kwargs["payment_amount"],
        operation=request.operation.value, prompt=request.prompt or "",
        negative_prompt=request.negative_prompt,
        photo_media_id=photo_media_id, extra_media_ids=list(extra_media_ids),
        params=dict(request.params or {}),
        style_prompt=kwargs["prompt"], prefer=kwargs.get("prefer"),
        sample_brands=list(kwargs.get("sample_brands") or []),
        check_drawn=bool(kwargs.get("check_drawn")), from_chat=bool(kwargs.get("from_chat", True)),
        said=kwargs.get("said"),
    )


async def _bytes_of(db: AsyncSession, media_id: str) -> tuple[bytes, str]:
    asset = await db.get(m.MediaAsset, media_id)
    if asset is None or asset.deleted_at is not None:
        raise LookupError(f"медиа {media_id} нет или удалено")
    data = await get_storage().get(asset.storage_key)
    if data is None:
        raise LookupError(f"файл медиа {media_id} не читается из хранилища")
    return data, asset.mime or "image/jpeg"


async def kwargs_from_spec(db: AsyncSession, spec: JobSpec) -> dict:
    """Обратно в аргументы `run_image_job`: снимки перечитываются из хранилища."""
    image = image_mime = None
    if spec.photo_media_id:
        image, image_mime = await _bytes_of(db, spec.photo_media_id)
    extra = [await _bytes_of(db, mid) for mid in spec.extra_media_ids]
    request = GenerationRequest(
        operation=Operation(spec.operation), prompt=spec.prompt,
        negative_prompt=spec.negative_prompt, image=image, image_mime=image_mime,
        extra_images=extra, params=dict(spec.params or {}),
    )
    return dict(
        gen_id=spec.gen_id, user_id=spec.user_id, payment_id=spec.payment_id,
        payment_amount=spec.payment_amount, request=request, prompt=spec.style_prompt,
        prefer=spec.prefer, sample_brands=list(spec.sample_brands or []),
        check_drawn=spec.check_drawn, from_chat=spec.from_chat, said=spec.said,
    )


async def enqueue(db: AsyncSession, spec: JobSpec) -> None:
    """Записать спецификацию в строку работы и положить id в очередь.

    Сначала строка, потом очередь: если упадём между ними, воркер подберёт
    работу при старте по спецификации в строке. Наоборот — id в очереди без
    спецификации — воркеру нечего было бы делать.
    """
    record = await db.get(m.Generation, spec.gen_id)
    if record is None:
        raise LookupError(f"работы {spec.gen_id} нет")
    record.request_params = {**(record.request_params or {}), SPEC_KEY: spec.to_record()}
    await db.flush()
    await db.commit()
    await get_client().lpush(settings.worker_queue_key, spec.gen_id)


async def dispatch(db: AsyncSession, spec: JobSpec, **kwargs: Any) -> str:
    """Отправить кадр рисоваться — так, как велит `worker_mode`. Возвращает режим."""
    if settings.worker_mode == "queue":
        await enqueue(db, spec)
        return "queue"
    image_job.schedule(**kwargs)
    return "inline"


def spec_of(record: m.Generation) -> Optional[JobSpec]:
    rec = (record.request_params or {}).get(SPEC_KEY)
    try:
        return JobSpec.from_record(rec) if rec else None
    except Exception:  # noqa: BLE001 — битая спецификация это «нет спецификации»
        logger.warning("Спецификация работы %s не читается", record.id)
        return None
