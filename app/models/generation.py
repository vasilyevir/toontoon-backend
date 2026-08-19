from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class GenerationType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class GenerationStatus(str, Enum):
    QUEUED = "queued"
    DONE = "done"
    FAILED = "failed"


class Refinement(str, Enum):
    """Что человек хочет поправить в уже полученном кадре.

    Список закрытый и составлен не из воображения, а из отказов, замеренных на
    ста одиннадцати кадрах в прогоне двенадцати моделей: часть моделей молча
    возвращает фотографию вместо рисунка, часть под сильной переделкой
    идеализирует лицо, и это два разных лечения.
    """

    MORE_DRAWN = "more_drawn"
    CLOSER_TO_PHOTO = "closer_to_photo"
    WIDER_FRAME = "wider_frame"
    DIFFERENT_LIGHT = "different_light"


class Generation(BaseModel):
    """A single generation record. Stored under ``generation:{id}`` and indexed
    per-user in the list ``user:generations:{user_id}``."""

    id: str
    user_id: str
    type: GenerationType = GenerationType.IMAGE
    status: GenerationStatus = GenerationStatus.QUEUED

    tile_id: Optional[str] = None
    tile_label: Optional[str] = None
    prompt: str = ""
    result_url: Optional[str] = None
    # For videos: a JPEG thumbnail extracted from the first frame (shown in gallery/sidebar).
    thumbnail_url: Optional[str] = None

    # Wallet bookkeeping.
    payment_id: Optional[str] = None
    cost: int = 0

    # Public sharing.
    share_id: Optional[str] = None

    # If the request came from within a chat session, the result (or error,
    # on failure) is appended there automatically — crucial for video, whose
    # completion happens in a background job long after the HTTP response.
    chat_id: Optional[str] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ─── Request / response bodies ──────────────────────────────────────────────


class GenerateRequest(BaseModel):
    """Body for ``POST /api/generate``.

    A request is either tile-driven (``tile_id`` + ``answers``) or free-form
    (``prompt``). ``style`` is the free-form follow-up answer.
    """

    type: GenerationType = GenerationType.IMAGE
    tile_id: Optional[str] = None
    answers: dict[str, str] = Field(default_factory=dict)
    prompt: Optional[str] = None
    style: Optional[str] = None
    # Зачем человек пришёл: «poster», «card», «portrait», «product». Отдельно от
    # стиля, потому что это разные вопросы к одному запросу: назначение решает,
    # кто исполнитель (буквы умеет не всякий), а стиль — как это нарисовано.
    #
    # Пока они жили в одном поле, назначение съедало стиль: приложение слало
    # «poster», и просьба «постер в стиле аниме» уезжала мультяшным якорем по
    # умолчанию — то есть человек выбирал технику, а она никуда не доезжала.
    # Старые сборки продолжают слать назначение в `style`, и маршрут по-прежнему
    # смотрит в оба поля.
    intent: Optional[str] = None
    # Стиль из каталога: «нажал на пример — подставил своё фото». Промпт тогда
    # берётся из строки стиля, а не сочиняется: витрина показывает конкретный
    # результат, и получить его можно только тем же текстом, которым он сделан.
    style_id: Optional[str] = None
    photo_url: Optional[str] = None
    # Дополнительные снимки: человек с товаром, пара, семья. Первый остаётся в
    # `photo_url` — однолицый путь основной, и переписывать его ради редкого
    # случая значило бы менять то, что работает, ради того, чего ещё нет.
    #
    # Кто из подключённых моделей сколько снимков берёт, объявлено в реестре, и
    # запрос с двумя референсами просто не попадёт к тому, кто умеет один.
    extra_photo_urls: list[str] = Field(default_factory=list, max_length=8)
    # Картинки-образцы: «сделай меня в стилистике вот этого постера».
    #
    # Роль у них другая, чем у снимков выше, и путать их нельзя. Те отвечают на
    # вопрос «кто в кадре» — модель обязана сохранить лицо. Эти отвечают на
    # «как это выглядит»: из них берутся палитра, техника и композиция, а люди
    # и предметы на них — чужие, и переносить их в кадр запрещено.
    #
    # Приложить образец дешевле, чем описать его словами: это самый короткий
    # путь от «хочу как здесь» до кадра.
    style_ref_urls: list[str] = Field(default_factory=list, max_length=4)
    # Человек сам расставил роли снимков — не трогать.
    #
    # Без этого флага «оба снимка — люди» неотличимо от «ничего не выбирал»:
    # роль человека стоит умолчанием, и пустой список образцов означает и то и
    # другое. А случаи разные: двое в кадре — законная просьба, и разбор,
    # который молча переставит второго в образцы, испортит именно её.
    roles_chosen: bool = False
    # Optional: id of a chat session (POST /api/chats) to append the result
    # (or error) to automatically. Omit to generate without chat history.
    chat_id: Optional[str] = None
    # Уточнение к предыдущему результату: «сделай рисованнее», «держись ближе к
    # фото». Приходит смыслом, а не текстом, потому что промпт собирается здесь,
    # и дописывать к нему фразу на стороне приложения значило бы держать сборку
    # в двух местах. Заодно от уточнения зависит выбор исполнителя, а он тоже
    # решается сервером.
    refine: Optional[Refinement] = None
    # Пояснение к уточнению своими словами: «свет из окна слева», «теплее».
    # Кнопка говорит, что не так, а это — как именно надо. Без него уточнение
    # остаётся угадыванием: «другой свет» не значит «какой».
    refine_note: Optional[str] = Field(default=None, max_length=200)
    # Пришёл ли запрос из разговора. Только такие кадры туда и попадают.
    #
    # Раньше туда падали все: человек запускал стиль с карточки на главной, а
    # кадр оказывался репликой в переписке — без вопроса, без просьбы, просто
    # картинка посреди разговора, которого не было. Читалось это как «фотографии
    # появились сами», а история чата переставала быть историей чата.
    #
    # По умолчанию `True`: сборки, которые про это поле не знают, работают как
    # раньше, а знающие говорят «нет» там, где кадр пришёл с карточки.
    from_chat: bool = True
    # Пропорции кадра. По умолчанию вертикаль: продукт мобильный, и квадрат
    # там выглядит обрезанным. Но постер и обложка бывают другими, поэтому
    # выбор отдан наружу.
    aspect: Optional[str] = None


class GenerateResponse(BaseModel):
    # Снимок человека подставлен нами, а не приложен им.
    #
    # Приложение обязано сказать об этом словами: своё лицо там, где его не
    # ждали, — чувствительнее любой другой неожиданности, и человек должен
    # видеть, что мы взяли, а не обнаруживать это на кадре.
    used_saved_photo: bool = False
    id: str
    url: str
    type: GenerationType
    balance: int
    prompt: str
    # "done" for images (synchronous) or "queued" for videos (async + polled).
    status: GenerationStatus = GenerationStatus.DONE


class CreateGenerationRequest(BaseModel):
    """Body for ``POST /api/generations`` — reserve a queued record up-front."""

    type: GenerationType = GenerationType.IMAGE
    tile_id: Optional[str] = None
    prompt: str = ""


class ShareResponse(BaseModel):
    share_id: str
    share_url: str
