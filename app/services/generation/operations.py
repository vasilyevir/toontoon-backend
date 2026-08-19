"""What a generation is, independent of who performs it.

Three notions hold this together (CH-21): an **operation** says what to do, a
**style** says what the person will get, a **provider** says what actually made
it. The link is one-way — a style never names a provider, a provider never
knows about styles — and that is what makes adding a model an adapter plus a
row in the registry rather than a change to the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Operation(str, Enum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"
    IMAGE_TO_VIDEO = "image_to_video"
    INPAINT = "inpaint"
    OUTPAINT = "outpaint"
    UPSCALE = "upscale"
    # Восстановление старого снимка: убрать шум и царапины, вернуть цвет.
    #
    # Отдельная операция, а не разновидность image_to_image, потому что обещание
    # здесь противоположное. Стилизация меняет кадр и обязана сохранить лицо;
    # реставрация обязана сохранить ВСЁ и не имеет права ничего дорисовывать —
    # человек пришёл за своей бабушкой, а не за похожей на неё. Промпт ей тоже
    # не нужен, и общий негатив со списком запретов на стиль здесь бессмыслен.
    RESTORE = "restore"


# What each operation needs on input. The launch screen in the app is built
# from a style's input_spec, which is derived from this — so a new operation
# ships without a new screen.
REQUIRED_INPUTS: dict[Operation, tuple[str, ...]] = {
    Operation.TEXT_TO_IMAGE: ("prompt",),
    Operation.IMAGE_TO_IMAGE: ("prompt", "image"),
    Operation.IMAGE_TO_VIDEO: ("image",),
    Operation.INPAINT: ("prompt", "image", "mask"),
    Operation.OUTPAINT: ("prompt", "image"),
    Operation.UPSCALE: ("image",),
    Operation.RESTORE: ("image",),
}


class GenerationUnavailable(RuntimeError):
    """No provider could deliver. The caller refunds and asks to retry."""


@dataclass(slots=True)
class GenerationRequest:
    operation: Operation
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    # Raw bytes, not URLs: a reference photo must not be reachable by a link,
    # and providers that need a URL get a short-lived signed one from the
    # adapter rather than from the whole system.
    image: Optional[bytes] = None
    image_mime: Optional[str] = None
    # Дополнительные снимки для многосубъектных кадров: пара, семья, человек с
    # товаром. Первый снимок остаётся в `image` — так не пришлось переписывать
    # весь однолицый путь, который и дальше будет основным.
    #
    # Порядок значим: модели связывают референсы с упоминаниями в промпте по
    # очереди, и перестановка меняет, кто в кадре кем окажется.
    extra_images: list[tuple[bytes, str]] = field(default_factory=list)
    mask: Optional[bytes] = None
    params: dict = field(default_factory=dict)

    @property
    def references(self) -> list[tuple[bytes, str]]:
        """Все приложенные снимки, основной первым."""
        if self.image is None:
            return list(self.extra_images)
        return [(self.image, self.image_mime or "image/jpeg"), *self.extra_images]

    def validate(self) -> None:
        missing = [
            name
            for name in REQUIRED_INPUTS[self.operation]
            if getattr(self, name, None) in (None, "", b"")
        ]
        if missing:
            raise ValueError(
                f"{self.operation.value} requires {', '.join(missing)}"
            )


@dataclass(slots=True)
class GenerationResult:
    data: bytes
    mime: str
    provider_id: str
    model: str
    # Filled by the caller; kept here so the record of what actually happened
    # travels with the result instead of being reconstructed later.
    duration_ms: Optional[int] = None
    # Сколько эта генерация стоила на самом деле, если провайдер сказал. У
    # большинства цену приходится брать из прайса и умножать на догадку о
    # размере кадра; те, кто возвращает факт, избавляют от этой арифметики —
    # а при сравнении вендоров цена и есть половина ответа.
    cost_usd: Optional[float] = None
