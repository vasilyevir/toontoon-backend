"""Политика контента: что мы не рисуем, кем бы ни был заказчик.

До 2 сентября 2026 серверной политики не было вовсе: обнажёнку, детей и
публичных людей отсеивала только модерация провайдера. Это риск не
репутационный, а прямой: за поток отказов fal и OpenRouter блокируют ключ,
и тогда кадры не получает никто.

Четыре правила, решение Ильи от 2 сентября 2026:

  1. Обнажёнка и сексуальный контент — запрещены целиком.
  2. Несовершеннолетний в кадре — только в «безопасных» направлениях
     каталога (студия, ч/б, мультяшный, полароид, семья, питомцы) и никогда
     в сочетании с телесным текстом. В свободном промпте — допустим, пока
     текст не про тело.
  3. Публичные люди — запрещены: и названные в тексте, и узнанные на снимке.
     Исключение одно: пользователь с флагом `verified_public_figure`, который
     поддержка ставит руками после проверки. Снимок такого пользователя не
     сверяется с «похож на известного», текст — сверяется всегда.
  4. Сцены, которые сойдут за настоящие: форма полиции и военных со знаками
     различия, документы, оружие, направленное на человека, наркотики.

Проверка стоит ДО списания: за отказ мы не берём. Текст проверяется до
резервирования; снимки — после загрузки, внутри участка, который при отказе
возвращает деньги (`_abort_paid_order`). Вердикт зрения на снимке
запоминается в `media_assets.screening`, чтобы платить за него один раз,
а не при каждом кадре.

Молчание модели — не отказ: если зрение или разбор недоступны, человек
получает кадр, а модерация провайдера остаётся страховкой. Иначе любой сбой
витрины останавливал бы продукт целиком. Отказы провайдера по модерации при
этом считаются: после `policy_refusals_per_day` подряд генерация для этого
человека закрывается на сутки — тот, кто упорно пробует запрещённое, не
должен жечь наш ключ.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import asdict, dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import rate_limit
from app.db import models as m
from app.services import gpt
from app.storage import images as storage_images

log = logging.getLogger("toontoon.policy")

# Версия вердикта в `media_assets.screening`. Поменяется вопрос модели —
# поменяется номер, и старые ответы пересчитаются.
SCREENING_VERSION = 1


@dataclass(slots=True)
class TextVerdict:
    nudity: bool = False
    revealing: bool = False      # про тело: купальник, бельё, «сексуально»
    minor: bool = False          # ребёнок упомянут в тексте
    public_figure: bool = False  # назван реальный публичный человек
    deceptive: bool = False


@dataclass(slots=True)
class PhotoVerdict:
    nudity: bool = False
    minor: bool = False
    public_figure: bool = False

    def as_record(self) -> dict:
        return {**asdict(self), "v": SCREENING_VERSION}

    @classmethod
    def from_record(cls, record: dict) -> "PhotoVerdict":
        return cls(nudity=bool(record.get("nudity")), minor=bool(record.get("minor")),
                   public_figure=bool(record.get("public_figure")))


class PolicyRefusal(Exception):
    """Отказ по политике. `detail` — то, что увидит человек."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _refusal(code: str) -> PolicyRefusal:
    texts = {
        "nudity": "We don't make nude or sexual pictures. Your TOONTOON weren't charged.",
        "minor": "We don't put children into this kind of picture. "
                 "Try a studio, cartoon or family style. Your TOONTOON weren't charged.",
        "public_figure": "This looks like a public figure, and we don't restyle public people. "
                         f"If this is you, write to {settings.support_email} and we'll verify you. "
                         "Your TOONTOON weren't charged.",
        "deceptive": "We don't make pictures that could pass for real uniforms, documents "
                     "or weapons. Your TOONTOON weren't charged.",
    }
    return PolicyRefusal(code, texts[code])


BLOCKED_DETAIL = "Too many refused requests — generation is paused for 24 hours."


# ─── Текст ───────────────────────────────────────────────────────────────────

_TEXT_SYSTEM = (
    "You screen a request to an image generator. The request may be in any "
    "language. Answer with ONE JSON object and nothing else, all values true or "
    "false:\n"
    '{"nudity": ..., "revealing": ..., "minor": ..., "public_figure": ..., "deceptive": ...}\n'
    "nudity — asks for nudity, sexual content or pornography.\n"
    "revealing — asks for swimwear, underwear, lingerie, 'sexy', 'seductive' or "
    "otherwise body-focused depiction, even without nudity.\n"
    "minor — a child, baby, teenager or someone described as under 18 is a "
    "subject of the picture.\n"
    "public_figure — names a real, living or dead, publicly known person "
    "(politician, actor, musician, athlete, influencer) to be depicted or "
    "imitated. Fictional characters and brands are NOT public figures.\n"
    "deceptive — asks for a realistic police, military or medical uniform with "
    "insignia, an identity document, a weapon pointed at someone, drugs, or a "
    "scene meant to pass for a real event.\n"
    "Be literal: judge what is asked, not what could be inferred."
)

# Запасной разбор на случай, когда модель недоступна: грубый, только про
# обнажёнку, и только чтобы не пропустить очевидное.
_NUDITY_WORDS = (
    "nude", "naked", "topless", "nudity", "porn", "erotic", "nsfw", "sex ",
    "sexual", "голый", "голая", "голое", "голые", "голой", "голым", "голую",
    "голого", "голом", "обнаж", "нагиш", "эротич", "порно", "секс",
)


def _keyword_hit(text: str, words: tuple[str, ...]) -> bool:
    low = f" {text.lower()} "
    return any(w in low for w in words)


async def screen_text(text: Optional[str]) -> TextVerdict:
    """Что просят словами. Сбой модели — вердикт по словарю, а не отказ."""
    text = (text or "").strip()
    if not text:
        return TextVerdict()
    verdict = TextVerdict(nudity=_keyword_hit(text, _NUDITY_WORDS))
    if not settings.policy_enabled or not settings.openai_enabled:
        return verdict
    try:
        raw = await gpt._call(
            [{"role": "system", "content": _TEXT_SYSTEM},
             {"role": "user", "content": text[:4000]}],
            max_tokens=80, temperature=0,
            model=settings.slot_extraction_model or None,
            purpose="content-screen",
        )
        start, end = raw.index("{"), raw.rindex("}") + 1
        parsed = json.loads(raw[start:end])
    except Exception as exc:  # noqa: BLE001 — молчание модели не отказ
        log.warning("Проверка текста не прошла (%s): решает словарь", type(exc).__name__)
        return verdict
    for field in ("nudity", "revealing", "minor", "public_figure", "deceptive"):
        if parsed.get(field) is True:
            setattr(verdict, field, True)
    return verdict


def text_of(*parts: Optional[str], answers: Optional[dict] = None) -> str:
    """Всё, что человек сказал словами, одной строкой для проверки."""
    words = [p.strip() for p in parts if p and p.strip()]
    if answers:
        words.extend(str(v).strip() for v in answers.values() if str(v).strip())
    return " ".join(words)


# ─── Снимок ──────────────────────────────────────────────────────────────────

_PHOTO_SYSTEM = (
    "You screen one photograph before it is restyled by an image generator. "
    "Answer with ONE JSON object and nothing else, all values true or false:\n"
    '{"nudity": ..., "minor": ..., "public_figure": ...}\n'
    "nudity — exposed genitals, breasts or buttocks, or sexual activity.\n"
    "minor — a person who appears to be under 18 is the main subject. When "
    "clearly an adult, false; when genuinely unsure between teenager and adult, "
    "true.\n"
    "public_figure — the main subject is a recognisable, publicly known real "
    "person: a politician, actor, musician, athlete or major influencer. An "
    "ordinary person who merely resembles someone is false.\n"
    "Drawings, posters and product shots without a real person: all false."
)


async def screen_photo(data: bytes) -> Optional[PhotoVerdict]:
    """Вердикт зрения. `None` — не посмотрели; такое не запоминается."""
    if not settings.policy_enabled or not settings.openai_enabled:
        return None
    try:
        small = base64.b64encode(storage_images.preview(data, side=512)).decode()
        raw = await gpt._call(
            [{"role": "system", "content": _PHOTO_SYSTEM},
             {"role": "user", "content": [
                 {"type": "image_url",
                  "image_url": {"url": f"data:image/jpeg;base64,{small}"}}]}],
            max_tokens=60, temperature=0,
            model=settings.slot_extraction_model or None,
            purpose="content-screen",
        )
        start, end = raw.index("{"), raw.rindex("}") + 1
        parsed = json.loads(raw[start:end])
    except Exception as exc:  # noqa: BLE001
        log.warning("Проверка снимка не прошла (%s): кадр идёт без неё", type(exc).__name__)
        return None
    return PhotoVerdict(nudity=parsed.get("nudity") is True,
                        minor=parsed.get("minor") is True,
                        public_figure=parsed.get("public_figure") is True)


async def screen_asset(db: AsyncSession, asset: m.MediaAsset, data: bytes) -> PhotoVerdict:
    """Вердикт по снимку — из записи, если уже смотрели, иначе от зрения.

    Запоминается только настоящий ответ: «не посмотрели» сегодня не должно
    превращаться в «всё чисто» навсегда.
    """
    record = asset.screening or {}
    if record.get("v") == SCREENING_VERSION:
        return PhotoVerdict.from_record(record)
    verdict = await screen_photo(data)
    if verdict is None:
        return PhotoVerdict()
    asset.screening = verdict.as_record()
    await db.flush()
    return verdict


# ─── Решение ─────────────────────────────────────────────────────────────────

def minor_safe_categories() -> set[str]:
    return {c.strip() for c in settings.minor_safe_categories.split(",") if c.strip()}


def judge_text(text: TextVerdict) -> None:
    """Отказ по словам. Публичное лицо в тексте — отказ всегда, флаг не
    помогает: «сделай меня как Бекхэма» не нужно и самому Бекхэму."""
    if text.nudity:
        raise _refusal("nudity")
    if text.minor and text.revealing:
        raise _refusal("minor")
    if text.public_figure:
        raise _refusal("public_figure")
    if text.deceptive:
        raise _refusal("deceptive")


def judge_photos(photos: list[PhotoVerdict], *, text: TextVerdict,
                 category: Optional[str], verified_public_figure: bool = False) -> None:
    """Отказ по снимкам с учётом того, куда они пойдут.

    `category` — направление стиля из каталога; `None` для свободного
    промпта. Ребёнок в свободном промпте допустим, пока текст не про тело.
    """
    if any(p.nudity for p in photos):
        raise _refusal("nudity")
    if any(p.minor for p in photos):
        if text.revealing or text.nudity:
            raise _refusal("minor")
        if category is not None and category not in minor_safe_categories():
            raise _refusal("minor")
    if any(p.public_figure for p in photos) and not verified_public_figure:
        raise _refusal("public_figure")


# ─── Отказы провайдера ───────────────────────────────────────────────────────

_MODERATION_MARKS = ("safety", "content policy", "content_policy", "moderation",
                     "refus", "blocked", "nsfw", "flagged")


def looks_like_moderation(error: Optional[str]) -> bool:
    low = (error or "").lower()
    return any(mark in low for mark in _MODERATION_MARKS)


def _key(user_id: str) -> str:
    return f"policy:{user_id}"


async def note_refusal(user_id: str) -> int:
    """Записать отказ — наш или провайдера. Возвращает, сколько их за сутки."""
    _, remaining = await rate_limit.hit(_key(user_id), settings.policy_refusals_per_day, 86400)
    return settings.policy_refusals_per_day - remaining


async def is_blocked(user_id: str) -> bool:
    return await rate_limit.peek(_key(user_id)) >= settings.policy_refusals_per_day
