# ARTEKI — Полная документация проекта

> **Дата:** 2026-06-20  
> **Версия:** 1.0 (production @ 193.149.190.155)

---

## Содержание

1. [Что такое ARTEKI](#1-что-такое-arteki)
2. [Продуктовая логика](#2-продуктовая-логика)
3. [Технический стек и инфраструктура](#3-технический-стек-и-инфраструктура)
4. [Архитектура backend](#4-архитектура-backend)
5. [Архитектура frontend](#5-архитектура-frontend)
6. [Хранилище данных (Redis)](#6-хранилище-данных-redis)
7. [Авторизация и сессии](#7-авторизация-и-сессии)
8. [Кошелёк TEKI](#8-кошелёк-teki)
9. [Тайлы — каталог контента](#9-тайлы--каталог-контента)
10. [Генерация изображений — полный флоу](#10-генерация-изображений--полный-флоу)
11. [Генерация видео — полный флоу](#11-генерация-видео--полный-флоу)
12. [Система промптов для изображений](#12-система-промптов-для-изображений)
13. [Система промптов для видео](#13-система-промптов-для-видео)
14. [Чат-ассистент](#14-чат-ассистент)
15. [Push-уведомления](#15-push-уведомления)
16. [Floating Generation Indicator](#16-floating-generation-indicator)
17. [API — все эндпоинты](#17-api--все-эндпоинты)
18. [Конфигурация (.env)](#18-конфигурация-env)
19. [Деплой и сервисы](#19-деплой-и-сервисы)
20. [Известные баги и P0/P1 проблемы](#20-известные-баги-и-p0p1-проблемы)

---

## 1. Что такое ARTEKI

ARTEKI — это AI-генератор персонализированного визуального контента. Пользователь открывает веб-приложение, выбирает тип контента (изображение, открытка, видео), отвечает на 2–4 быстрых вопроса (Quick Reply кнопки) и получает готовый красивый 3D-мультяшный контент за несколько секунд (изображения) или несколько минут (видео).

**Целевая аудитория:** люди 40–60 лет, не техносавантные, хотят быстро создать открытку или видео-поздравление для близких.

**Единица оплаты:** TEKI — внутренняя валюта. При регистрации выдаётся 50 TEKI. Изображение стоит 1 TEKI, видео — 2 TEKI.

**Внешние провайдеры генерации:**
- **OpenAI gpt-image-1** — основной генератор изображений
- **OpenAI gpt-4o-mini** — промпт-строитель и чат-ассистент
- **kie.ai / ByteDance Seedance** — видео-генерация (модели `seedance-2-fast` и `seedance-2`)

---

## 2. Продуктовая логика

### 2.1 Флоу пользователя (полный)

```
Лендинг (/landing или /)
  └─► Кнопка "Create image / postcard / video"
        └─► Редирект на /generate?category=image|postcard|video

/generate (главная страница)
  ├── Левая панель: история чатов (sidebar)
  └── Центр: чат с Arteki

В чате два режима:
  A) Свободный текст  →  chat-ассистент → предлагает тайл → пользователь выбирает → Quick Reply флоу
  B) Клик на тайл     →  мгновенный Quick Reply флоу (тайл уже выбран)

Quick Reply флоу:
  1. Показать вопросы тайла (1–4 вопроса) — Quick Reply пилюли
  2. После последнего вопроса: показать Summary-карточку ("Everything is ready! Click to create")
  3. Клик → POST /api/generate → wait
     ├── Image: ответ через ~15–45с, URL в ответе → рендер в чате
     └── Video: ответ QUEUED → polling GET /api/generations/{id} каждые 6с → до 10 мин
         ├── После ответа: рендер video-плеера в чате
         └── Push-уведомление если пользователь вышел со страницы

Аутентификация:
  └── Magic Link — ввести email → получить ссылку → клик по ссылке → сессионная кука
```

### 2.2 Как пользователь взаимодействует с чатом

Чат — это conversational UI, имитирующий мессенджер. Arteki отвечает текстом, задаёт вопросы и показывает контент прямо в пузырях сообщений. Пузыри бывают:
- **Текст** — обычный ответ ассистента
- **Quick Reply пилюли** — кнопки для выбора ответа на вопрос
- **Изображение** — готовый результат (img-тег)
- **Видео** — HTML5 видео-плеер
- **Открытка** — изображение + `CardTextOverlay` (CSS-текст поверх)
- **Ошибка** — красная карточка с кнопкой Retry

### 2.3 Типы контента

| Тип | Тайлов | Стоимость | Время генерации |
|-----|--------|-----------|-----------------|
| Image | 6 | 1 TEKI | 15–45 сек |
| Postcard | 13 | 1 TEKI | 15–45 сек |
| Announcement | 7 | 1 TEKI | 15–45 сек |
| Video | 8 | 2 TEKI | 4–8 мин |

---

## 3. Технический стек и инфраструктура

### Backend
- **Python 3.12** + **FastAPI 0.115** + **uvicorn 0.34** (ASGI)
- **Pydantic v2** для моделей и валидации
- **Redis 5.2** (redis-py async) — единственное хранилище (users, sessions, generations)
- **httpx 0.28** — все исходящие HTTP запросы (OpenAI, kie.ai, Pollinations)
- **ffmpeg** — извлечение превью-кадра из MP4

### Frontend
- **Next.js 14.2** (App Router, SSR/CSR смешанный)
- **React 18** + TypeScript 5
- **Tailwind CSS 3.4** — вся стилизация
- **Нет внешних UI-библиотек** — всё написано вручную

### Инфраструктура
- **Ubuntu** на сервере `193.149.190.155`
- **nginx** — reverse proxy, раздаёт фронт и проксирует `/api/*` → бэк
- **systemd** — `arteki-backend.service` и `arteki-frontend.service` (автостарт)
- **Redis** — `redis.service`, стандартный локальный инстанс

### Сетевая схема

```
Browser
  │  GET / POST /api/*
  ▼
nginx :80
  ├── /api/*  →  fastapi :8000 (uvicorn)
  ├── /uploads/*  →  fastapi staticfiles → /opt/gen-backend/uploads/
  └── /*  →  nextjs :3000
```

---

## 4. Архитектура backend

```
/opt/gen-backend/
├── app/
│   ├── config.py              # Settings (pydantic-settings, .env)
│   ├── main.py                # FastAPI app, middleware, lifespan
│   ├── deps.py                # Context = Tuple[User, Session], Depends()
│   ├── redis_client.py        # Global redis connection
│   ├── cookies.py             # Session cookie helpers
│   │
│   ├── core/
│   │   ├── rate_limit.py      # Fixed-window rate limit via Redis INCR
│   │   ├── security.py        # new_id(), new_token() (UUID-based)
│   │   └── transliterate.py   # Translit Russian→Latin for Pollinations
│   │
│   ├── models/
│   │   ├── generation.py      # Generation, GenerateRequest/Response
│   │   ├── user.py            # User, PublicUser, Session
│   │   ├── tile.py            # Tile, TileCategory, Question
│   │   ├── session.py         # Session (with Boostify tokens)
│   │   └── payment.py         # Payment, Balance
│   │
│   ├── routers/
│   │   ├── auth.py            # POST /api/auth/magic-link, GET /api/auth/verify, GET /api/auth/me
│   │   ├── chat.py            # POST /api/chat
│   │   ├── generate.py        # POST /api/uploads, POST /api/generate
│   │   ├── generations.py     # GET/POST /api/generations, share
│   │   ├── tiles.py           # GET /api/tiles, /api/tiles/featured
│   │   ├── profile.py         # PATCH /api/auth/profile, DELETE
│   │   ├── payments.py        # GET /api/balance
│   │   ├── push.py            # Push notification subscription endpoints
│   │   └── webhooks.py        # Boostify webhook
│   │
│   └── services/
│       ├── auth_service.py    # User+session CRUD in Redis
│       ├── generations_service.py  # Generation CRUD + share in Redis
│       ├── wallet.py          # Two-phase payment (reserve/confirm/cancel)
│       ├── tiles_data.py      # Static tile catalog (all 34 tiles)
│       ├── content_gen.py     # Image generation (OpenAI + Pollinations)
│       ├── video_gen.py       # Video generation (kie.ai, background job)
│       ├── gpt.py             # GPT-4o-mini: prompt builder + chat assistant
│       ├── prompt_style.py    # Style anchors, NEGATIVE, brand guard, IP neutralize
│       ├── picture_prompts.py # Per-tile image/postcard mechanical templates
│       ├── card_prompts.py    # Card prompt enrichment (VIS-layers)
│       ├── video_prompts.py   # Per-tile video builders + free-text LLM builder
│       ├── push_service.py    # Web Push send via pywebpush + Redis subscription storage
│       └── boostify.py        # Boostify OAuth + wallet API client (mock mode)
```

### Зависимости между сервисами

```
routers/generate.py
  ├── tiles_data.get_tile()
  ├── wallet.reserve/confirm/cancel()
  ├── content_gen.generate()         ← for images
  │     ├── gpt.build_prompt()
  │     ├── prompt_style.assemble()
  │     └── _openai_image_to_uploads() / _fetch_to_uploads()
  └── video_gen.schedule_video_job() ← for videos (background asyncio.Task)
        ├── video_prompts.build_storyboard()
        ├── _submit_task()  → kie.ai createTask
        ├── _poll_task()    → kie.ai recordInfo (loop)
        ├── _download_video()
        ├── _extract_thumbnail() → ffmpeg
        ├── generations_service.save()
        └── push_service.send_push_to_user()
```

---

## 5. Архитектура frontend

```
/opt/gen-frontend/
├── src/app/
│   ├── layout.tsx             # Root layout (font, meta, viewport)
│   ├── page.tsx               # Лендинг / стартовый экран (/)
│   ├── PromptInput.tsx        # Компонент поля ввода (нижняя панель)
│   ├── generate/
│   │   └── page.tsx           # Главная страница (весь UI чата + генерация) — ~1500 строк
│   ├── landing/
│   │   └── page.tsx           # Маркетинговый лендинг (/landing)
│   ├── login/
│   │   ├── page.tsx           # Форма ввода email
│   │   └── code/page.tsx      # "Check your inbox" экран
│   ├── components/
│   │   ├── CardTextOverlay.tsx   # CSS-наложение текста на открытку
│   │   ├── OnboardingTour.tsx    # Тур первого запуска
│   │   └── TopNavRight.tsx       # Кнопки в шапке (баланс, профиль, logout)
│   └── lib/
│       ├── api.ts             # Typed HTTP client (auth, catalog, gen, chat)
│       ├── types.ts           # TypeScript типы (User, Tile, Generation, ...)
│       ├── useIsMobile.ts     # Хук определения мобильного устройства
│       └── usePushNotifications.ts  # Хук Web Push подписки
├── public/
│   ├── sw.js                  # Service Worker для Web Push
│   └── assets/                # SVG-иконки, фоны, Pic.svg (spinning loader)
└── next.config.mjs            # rewrites /api/* → http://backend:8000/api/*
```

### Ключевые состояния `generate/page.tsx`

| Стейт | Тип | Описание |
|-------|-----|----------|
| `messages` | `Message[]` | Все сообщения активного чата |
| `history` | `HistoryItem[]` | Список чатов в левом sidebar |
| `aiFlowState` | enum-like string | Текущий этап флоу: `idle`, `tileSelected`, `questionsFlow`, `styleSelection`, `generationConfirmed`, `generating`, `done`, `error` |
| `currentTile` | `Tile \| null` | Выбранный тайл |
| `tileAnswers` | `Record<string,string>` | Ответы пользователя на вопросы тайла |
| `currentQuestionIdx` | number | Какой вопрос сейчас показывается |
| `flowMode` | `'image'\|'video'` | Тип текущей генерации |
| `genLabel` | string | Название текущей генерации для индикатора |
| `genIsVideo` | boolean | Идёт ли видеогенерация |
| `genElapsed` | number | Секунды с начала генерации (таймер) |

### Персистентность (localStorage)

| Ключ | Содержимое |
|------|-----------|
| `arteki_chat_history` | JSON-массив метаданных чатов (id, label, image) |
| `arteki_chat_msgs_{id}` | JSON-массив сообщений чата N |
| `arteki_gen_active` | `{id, label, isVideo, startedAt}` — активная генерация |
| `arteki_start_category` | Стартовая категория при переходе с лендинга |
| `arteki_migration_v1_cleaned` | Флаг миграции |
| `arteki_migration_v2_video_thumbs` | Флаг миграции |

---

## 6. Хранилище данных (Redis)

Все данные хранятся **только в Redis**. Нет SQL-базы, нет файловой системы для данных (только загруженные изображения/видео в `uploads/`).

### Схема ключей

```
user:{user_id}                 → User JSON
user:email:{email}             → user_id (строка)
session:{sid}                  → Session JSON  (TTL = 30 дней)
magic:{token}                  → email          (TTL = 15 мин)
generation:{gen_id}            → Generation JSON
user:generations:{user_id}     → Redis List из gen_id (LIFO, max 200)
share:{share_id}               → gen_id (строка)
ratelimit:gen:{user_id}        → счётчик (TTL = 1 час)
push:sub:{user_id}             → JSON Web Push subscription
oauth_state:{state}            → "1"  (TTL = 10 мин, Boostify OAuth)
```

### Модель `Generation`

```python
class Generation:
    id: str                    # "gen_<uuid>"
    user_id: str
    type: "image" | "video"
    status: "queued" | "done" | "failed"
    tile_id: str | None        # id тайла или None для free-text
    tile_label: str | None     # читаемое название тайла
    prompt: str                # финальный промпт (после сборки)
    result_url: str | None     # "/uploads/img_xxx.png" или "/uploads/vid_xxx.mp4"
    thumbnail_url: str | None  # "/uploads/vid_xxx_thumb.jpg" (только видео)
    payment_id: str | None
    cost: int
    share_id: str | None       # для публичного шаринга
    created_at: datetime
```

---

## 7. Авторизация и сессии

### Magic Link (основной метод)

```
1. POST /api/auth/magic-link { email }
   → создаёт magic:{token} → email (TTL 15 мин) в Redis
   → возвращает devLink: "/api/auth/verify?token=..."
   (в prod — отправил бы email, сейчас возвращает ссылку напрямую)

2. GET /api/auth/verify?token=...
   → consume magic:{token} → email
   → get_or_create_magic_user(email) — ищет по user:email:{email}
     → если не нашёл: создаёт User с teki_balance=50
   → create_session(user) → session:{sid} в Redis (TTL 30 дней)
   → Set-Cookie: arteki-session={sid}; SameSite=Lax
   → 302 Redirect → /generate

3. Каждый запрос с кукой:
   deps.py: required_context() / optional_context()
   → session:{sid} → Session
   → user:{user_id} → User
   → возвращает Context = (User, Session)
```

### Boostify OAuth (v2, пока в mock-режиме)
Полный OAuth2 flow (authorize → callback → token exchange) реализован, но `BOOSTIFY_MOCK=true` в `.env`, поэтому реально не используется.

---

## 8. Кошелёк TEKI

Двухфазная система оплаты (reserve → confirm/cancel):

```
reserve(user, amount):
  ├── Проверить balance >= amount
  ├── user.teki_balance -= amount
  ├── save_user(user)
  └── вернуть Payment(PENDING)

confirm(user, payment):
  └── Для magic-link: ничего (деньги уже сняты в reserve)

cancel(user, payment):  ← при ошибке генерации
  ├── user.teki_balance += payment.amount  (рефанд)
  └── save_user(user)
```

**Стоимость:**
- Изображение / открытка / анонс: **1 TEKI** (конфигурируется `IMAGE_TEKI_COST`)
- Видео: **2 TEKI** (конфигурируется `VIDEO_TEKI_COST`)
- При ошибке генерации: автоматический рефанд

---

## 9. Тайлы — каталог контента

### Что такое тайл
Тайл — это шаблон контента с фиксированным набором вопросов (Quick Reply). Каждый вопрос имеет от 2 до 6 готовых опций + иногда `allow_custom=true` для свободного ввода.

### Структура тайла

```python
Tile:
  id: str              # "birthday", "cute_animal_video" и т.д.
  category: "image" | "postcard" | "announcement" | "video"
  emoji: str
  title: str           # читаемое название
  hint: str | None     # подсказка для чата
  featured: bool       # показывать ли в быстром доступе
  needs_photo: bool    # требуется загрузка фото (animate_photo, animate_pet)
  cost: int            # 1 или 2
  questions: [Question]
```

### Полный каталог (34 тайла)

#### 🖼️ Image (6) — 1 TEKI

| ID | Название | Вопросы |
|----|---------|---------|
| `cartoon_character` | Cartoon character | character_type, action, setting |
| `cute_animal` | Cute animal | animal, action, setting |
| `birds` | Birds | bird, action, setting |
| `fish` | Fish | fish, action, setting |
| `nature` | Beautiful nature | place, time_of_day, detail |
| `food` | Food | dish, setting |

#### 💌 Postcard (13) — 1 TEKI

| ID | Название |
|----|---------|
| `birthday` | Birthday |
| `jubilee` | Milestone Birthday |
| `valentine` | Valentine's Day |
| `wedding` | Wedding |
| `anniversary` | Anniversary |
| `mothers_day` | Mother's Day |
| `fathers_day` | Father's Day |
| `easter` | Easter |
| `thanksgiving` | Thanksgiving |
| `new_year` | New Year / Christmas |
| `graduation` | Graduation |
| `get_well` | Get Well Soon |
| `just_because` | Just Because |

#### 📣 Announcement (7) — 1 TEKI

| ID | Название |
|----|---------|
| `cafe` | Cafe / restaurant |
| `beauty_salon` | Beauty salon |
| `handyman` | Home handyman |
| `tutor` | Tutor |
| `bakery` | Cakes & baking |
| `rental` | Property rental |
| `selling` | Selling items |

#### 🎬 Video (8) — 2 TEKI

| ID | Название | Особенности |
|----|---------|------------|
| `animate_photo` | Animate photo | `needs_photo=true` |
| `animate_pet` | Animate pet | `needs_photo=true` ⚠️ BROKEN |
| `cartoon_character_video` | Cartoon character | — |
| `video_greeting` | Video greeting | Приветственный текст-оверлей |
| `living_nature` | Living nature | Пейзаж без персонажей |
| `cute_animal_video` | Cute animal | — |
| `morning_video` | Good morning | Текст-оверлей |
| `inspiring_video` | Inspiring video | Текст-оверлей |

---

## 10. Генерация изображений — полный флоу

### 10.1 Синхронный пайплайн

Изображения генерируются **синхронно** — HTTP-запрос висит до готового результата.

```
POST /api/generate
{
  "type": "image",
  "tile_id": "birthday",
  "answers": {
    "who": "Mom",
    "name": "Elena",
    "age": "60",
    "text": "Happy Birthday!"
  }
}
```

**Шаги backend:**

```
1. rate_limit.hit(user_id, 30, 3600)          # 30 генераций в час
2. tiles_data.get_tile("birthday")             # достать тайл из каталога
3. Проверить: body.type == tile.category       # iмage != video → 400
4. cost = tile.cost = 1
5. wallet.reserve(user, amount=1)             # снять 1 TEKI (refund on fail)

6. content_gen.generate(
     gen_type="image",
     tile=<Birthday tile>,
     answers={...},
     photo_url=None
   )
   ├── analyze_photo(photo_url)  # если загружено фото → Pollinations vision → описание
   ├── style = answers.get("style") or body.style
   ├── gpt.build_prompt(tile, answers, style)
   │   ├── card_prompts.enrich_card_prompt() — если postcard
   │   │   ├── VIS-слои: palette, expression, pose, composition, light
   │   │   ├── LAYOUT_BLOCK для space под текст
   │   │   └── TECHNICAL block
   │   └── _SCENE_SYSTEM промпт → gpt-4o-mini → один абзац сцены
   │       ├── prompt_style.assemble(scene, style_key, is_text)
   │       └── нейтрализация IP (neutralize_ip)
   ├── fallback: content_gen.build_prompt()    # если GPT недоступен
   ├── prompt = strip_brands(prompt)           # убрать бренды
   └── _openai_image_to_uploads(prompt + OPENAI_VISUAL_GUARDS)
       ├── POST https://api.openai.com/v1/images/generations
       │   model: gpt-image-1, size: 1024x1024, quality: medium
       └── resp.data[0].b64_json → UPLOAD_DIR/img_{id}.png

7. wallet.confirm(user, payment)
8. generations_service.add_for_user(Generation(..., status=DONE, result_url="/uploads/img_xxx.png"))
9. Вернуть GenerateResponse(id, url, type, balance, prompt, status=done)
```

**Frontend после ответа:**
```javascript
const res = await gen.generate(body);
// res.url = "/uploads/img_xxx.png"
// Добавить сообщение в чат с generatedImg = res.url
// Обновить баланс
```

### 10.2 Промпт для изображений — подробно

Для **postcard/announcement тайлов** используется специальная обёртка `card_prompts.enrich_card_prompt()`:

```
[VIS palette block]
[VIS expression block]
[VIS pose block]
[VIS composition block]
[VIS light block]
[GPT-generated scene]
[LAYOUT_BLOCK — space for text overlay]
[TECHNICAL block]
```

Для **image тайлов** (cartoon_character, cute_animal, etc.):
```
[STYLE ANCHOR] (3d_cartoon / scene_cozy / scene_epic / anime)
[GPT-generated scene]
[LAYOUT_CENTER]
[TECHNICAL block]
```

**Автовыбор стиля** (если пользователь не указал):
1. `_has_living_subject(text)` → `3d_cartoon` (person/animal detected)
2. `_is_epic_scene(text)` → `scene_epic` (mountains, ocean, etc.)
3. fallback → `scene_cozy`

**OPENAI_VISUAL_GUARDS** — строка, добавляемая к промпту для OpenAI Images API (так как этот API не поддерживает negative_prompt):
```
clean correct anatomy with five fingers, friendly non-scary cartoon face,
lively expressive eyes with bright catchlights, appealing dynamic natural pose,
subject large and clear in frame, tidy uncluttered background,
vivid clean harmonious colors, no muddy or washed out tones,
NO text NO letters NO words NO watermark in the image,
glossy textured surfaces (not flat, not matte plastic)
```

### 10.3 Провайдеры изображений

| Провайдер | Когда используется | Fallback |
|-----------|-------------------|---------|
| **OpenAI gpt-image-1** | `IMAGE_PROVIDER=openai` + `OPENAI_API_KEY` задан | → dall-e-3 (если 400/403/404) |
| **Pollinations FLUX** | `IMAGE_PROVIDER=pollinations` или нет OpenAI ключа | — |

> ⚠️ Аккаунт OpenAI не имеет доступа к `dall-e-3`, поэтому fallback мёртв.

---

## 11. Генерация видео — полный флоу

### 11.1 Асинхронный пайплайн

Видео генерируется **асинхронно** — запрос сразу возвращает статус QUEUED, frontend поллингует результат.

```
POST /api/generate { "type": "video", "tile_id": "cute_animal_video", ... }
  ↓
status=QUEUED → id="gen_xxx" → вернуть 200

asyncio.Task: run_video_job(gen_id, tile, answers, ...)
  ↓                               ↓
  backend                         frontend (polling)
  генерирует ~4-8 мин             GET /api/generations/gen_xxx каждые 6с
  ↓                               ↓ status: queued → queued → ... → done
  status=DONE, result_url         result_url появился → показать видео
  thumbnail_url                   thumbnail_url → превью в sidebar
  push_service.send_push(user_id) ← Web Push уведомление пользователю
```

### 11.2 Три группы видео тайлов

| Группа | Тайлы | Механизм |
|--------|-------|---------|
| **A — Сцены** | `living_nature`, `morning_video`, `inspiring_video`, `video_greeting` | Text-to-video, anchor = STYLE_3D_SCENE_COZY/EPIC |
| **B — Персонажи** | `cartoon_character_video`, `cute_animal_video` | Text-to-video, anchor = STYLE_3D |
| **C — Фото** | `animate_photo`, `animate_pet` | Image-to-video: `first_frame_url` + motion prompt |

### 11.3 Kie.ai API

**createTask:**
```json
POST https://api.kie.ai/api/v1/jobs/createTask
Headers: Authorization: Bearer {KIE_API_KEY}
{
  "model": "bytedance/seedance-2-fast",
  "input": {
    "prompt": "...",
    "negative_prompt": "...",
    "resolution": "720p",
    "aspect_ratio": "9:16",
    "duration": 5,
    "generate_audio": false,
    "first_frame_url": "..."  ← только для Group C
  }
}
Response: { "code": 200, "data": { "taskId": "..." } }
```

**recordInfo (polling):**
```json
GET https://api.kie.ai/api/v1/jobs/recordInfo?taskId=...
Response: {
  "data": {
    "state": "success | generating | fail | waiting | queuing",
    "resultJson": "{\"resultUrls\": [\"https://cdn.kie.ai/...\"]}"
  }
}
```

**Настройки:**
- `VIDEO_DURATION=5` сек
- `VIDEO_RESOLUTION=720p`
- `VIDEO_ASPECT_RATIO=9:16` (вертикальный, mobile-first)
- `VIDEO_POLL_INTERVAL=6` сек
- `VIDEO_POLL_TIMEOUT=600` сек (10 мин)
- Для аудио-тайлов: `bytedance/seedance-2` (медленнее, но с аудио)

**Thumbnail:**
```bash
ffmpeg -i vid_xxx.mp4 -vf "select=eq(n\,0),scale=200:-1" -vframes 1 vid_xxx_thumb.jpg
```

---

## 12. Система промптов для изображений

### 12.1 Структура финального промпта

```
[STYLE ANCHOR]
[GPT SCENE]
[LAYOUT]
[TECHNICAL]
```

### 12.2 Стайл пресеты (`prompt_style.py`)

| Ключ | Когда | Начало анкора |
|------|-------|--------------|
| `3d_cartoon` | Персонажи, люди, животные, открытки | `vibrant 3D cartoon render, modern animated feature film look...` |
| `scene_cozy` | Пейзажи, натюрморты (небольшие) | `cozy stylized 3D cartoon render, charming miniature diorama aesthetic...` |
| `scene_epic` | Горы, океаны, эпичные пейзажи | `epic stylized 3D cartoon landscape render, dramatic depth and scale...` |
| `anime` | Японский стиль | `anime illustration style, clean cel shading...` |
| `realistic` | Фото-реализм (когда запросят) | `hyperrealistic photographic render...` |

### 12.3 TECHNICAL block (общий для всех)

```
Technical: warm cinematic lighting with soft rim light and gentle sun rays,
soft natural shadows, glossy smooth cartoon materials with rich surface texture
(not flat, not matte plastic), ray-traced global illumination, high quality 3D render,
8k resolution, crisp sharp details, clean anti-aliased edges,
soft depth of field with gentle background bokeh
```

### 12.4 VIS-слои для открыток (`card_prompts.py`)

Каждый тайл-открытка получает специфические блоки:

```python
VIS_2_PALETTE = PALETTES[tile_id]          # именованная палитра (birthday: "warm coral, cream, gold")
VIS_3_EXPRESSION = "warm genuine smile..."  # выражение лица
VIS_4_POSE = "natural lively body posture..." # поза
VIS_5_COMP = "subject large in frame..."   # композиция
VIS_6_LIGHT = LIGHT_MOODS[tile_id]        # освещение
```

### 12.5 IP-нейтрализация

Если пользователь упоминает авторский персонаж, `neutralize_ip()` заменяет его на описание:

```python
"SpongeBob" → "a cheerful yellow cartoon sea-sponge character"
"Winnie the Pooh" → "a round friendly yellow cartoon bear"
"Mickey Mouse" → "a cheerful cartoon mouse with round ears"
"Шрек" → "a friendly large green ogre character"
...40+ персонажей в словаре
```

Дополнительно `strip_brands()` убирает торговые марки через regex:
`Disney|Pixar|DreamWorks|Marvel|Nintendo|Pokemon|...`

---

## 13. Система промптов для видео

### 13.1 Формула видео-промпта

```
seedance_prompt = ANCHOR + ". " + MOTION (+ " Audio: ..." если нужно аудио)
```

**Anchor** — описывает сцену/персонажа (статика)  
**Motion** — описывает движение, анимацию

### 13.2 Стайл блоки видео

```python
STYLE_3D           = "vibrant 3D cartoon render, big expressive friendly eyes..."
STYLE_3D_SCENE_COZY = "cozy stylized 3D cartoon render, miniature diorama aesthetic..."
STYLE_3D_SCENE_EPIC = "epic stylized 3D cartoon landscape render, dramatic depth..."
TECHNICAL = "Technical: warm cinematic lighting..."
NEGATIVE = "distorted anatomy, photorealistic horror..."
```

### 13.3 Детерминированные строители для каждого тайла

Каждый видео-тайл имеет свою функцию-строитель:

| Тайл | Функция | Словари |
|------|---------|--------|
| `living_nature` | `_build_living_nature()` | `_NATURE_PLACE`, `_NATURE_TIME`, `_NATURE_SPECIAL`, `_NATURE_MOOD` |
| `morning_video` | `_build_morning_video()` | `_MORNING_SCENE`, `_MORNING_SPECIAL`, `_MORNING_AUDIO` |
| `inspiring_video` | `_build_inspiring_video()` | `_INSPIRING_SCENE`, `_INSPIRING_SPECIAL` |
| `video_greeting` | `_build_video_greeting()` | `_OCCASION_DATA` (birthday, new year, etc.) |
| `cute_animal_video` | `_build_cute_animal_video()` | `_ANIMAL_MAP`, `_ANIMAL_ACTION`, `_ANIMAL_PLACE` |
| `cartoon_character_video` | `_build_cartoon_character_video()` | `_CHAR_HERO`, `_CHAR_ACTION`, `_CHAR_STYLE` |
| `animate_pet/photo` | `build_photo_motion_prompt()` | фиксированный motion для photo-to-video |

**Как работает строитель:**
```python
# Пример для cute_animal_video
answers = {"animal": "Cat", "action": "Sleeping", "place": "At home", "mood": "Tender"}

animal_desc = _ANIMAL_MAP.get(answers["animal"].lower(), "a cute cartoon animal")
# "Cat" → "adorable kitten"

action_desc = _ANIMAL_ACTION.get(answers["action"].lower(), "sitting calmly")
# "Sleeping" → "sleeping peacefully, curled up softly"

place_desc = _ANIMAL_PLACE.get(answers["place"].lower(), "in a cozy cartoon scene")
# "At home" → "at home on a cozy blanket"

anchor = f"{STYLE_3D}, {animal_desc} {action_desc} {place_desc}"
motion = "subtle gentle breathing motion, eyes slowly blinking..."
```

### 13.4 Free-text видео (LLM-путь)

Если нет тайла (пользователь написал "хочу видео с котиком"):
```python
board = await video_prompts.build_storyboard(tile_id=None, free_text="видео с котиком")
# → вызывает LLM через _FREE_TEXT_SYSTEM промпт
# → GPT-4o-mini возвращает JSON: {anchor, motion, audio_enabled, ...}
# → neutralize_ip(combined) применяется к тексту
```

### 13.5 Photo-to-video (Group C)

```python
# animate_pet / animate_photo
motion_prompt = build_photo_motion_prompt(answers)
payload = {
    "prompt": motion_prompt,
    "first_frame_url": photo_url,   # ← URL загруженного фото
    ...
}
```

> ⚠️ **КРИТИЧЕСКИЙ БАГ (P0):** `animate_pet` передаёт `first_frame_url`, но только когда `photo_url` в запросе не `None`. Если фронт не передал `photo_url` — генерируется случайный кот. Плюс само фото должно быть публично доступным по URL для Seedance, а локальный upload `/uploads/...` доступен только изнутри сервера.

---

## 14. Чат-ассистент

```
POST /api/chat { message, history }
```

Arteki — персонаж-ассистент. Использует `gpt-4o-mini` с системным промптом `_CHAT_SYSTEM`.

**Поведение:**
- Всегда отвечает по-английски
- Аудитория: 40–60 лет, не техносавантные
- Знает каталог (Images, Postcards, Announcements, Videos)
- Предлагает подходящий тайл по описанию
- Задаёт уточняющие вопросы
- Не делает генерации сам (только направляет через UI)

**Стоимость:** ~$0.0001 / сообщение (gpt-4o-mini)

**История:** frontend передаёт последние 20 сообщений как `history`

**Fallback при ошибке GPT:**
```python
"I'm having a little trouble right now. Please try again in a moment!"
```

---

## 15. Push-уведомления

Web Push уведомления для видео-генерации (пока пользователь ушёл со страницы).

### Backend

```
GET /api/push/vapid-public-key    → { publicKey: "..." }
POST /api/push/subscribe          → сохраняет подписку в Redis: push:sub:{user_id}
DELETE /api/push/subscribe        → удаляет подписку
```

`push_service.send_push_to_user(user_id, title, body, url)`:
```python
sub = await redis.get(f"push:sub:{user_id}")
webpush(sub, data=json.dumps({title, body, url}), vapid_private_key=..., vapid_claims=...)
```

### Frontend (`usePushNotifications.ts`)

```javascript
// При старте видео-генерации:
subscribeToPush()
  → Notification.requestPermission()
  → navigator.serviceWorker.register('/sw.js')
  → registration.pushManager.subscribe({ applicationServerKey: vapidPublicKey })
  → POST /api/push/subscribe { subscription }
```

### Service Worker (`public/sw.js`)

```javascript
self.addEventListener('push', event => {
  const data = event.data.json();
  self.registration.showNotification(data.title, {
    body: data.body,
    icon: '/assets/icon-192.png',
    actions: [{ action: 'open', title: 'Watch video' }]
  });
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  clients.openWindow(data.url || '/generate');
});
```

> ⚠️ Текст уведомления `"🎬 Видео готово!"` — на русском, хотя весь UI на английском.

---

## 16. Floating Generation Indicator

Плавающая кнопка в правом нижнем углу — показывает, что идёт генерация.

**Условия показа:**
- Только на странице `/generate`
- Только когда `genLabel !== null` (активная генерация)

**Стейт и персистентность:**
```javascript
// При старте генерации:
localStorage.setItem("arteki_gen_active", JSON.stringify({
  id: res.id,         // gen_xxx
  label: genLabel,    // "Cute animal video"
  isVideo: true,
  startedAt: Date.now()
}));

// При монтировании страницы:
const saved = JSON.parse(localStorage.getItem("arteki_gen_active"));
const age = Date.now() - saved.startedAt;
if (age < (saved.isVideo ? 20*60*1000 : 5*60*1000)) {
  // Восстановить состояние + запустить polling
}

// При завершении / ошибке:
localStorage.removeItem("arteki_gen_active");
```

**Таймеры:**
- Видео: max возраст 20 мин
- Изображение: max возраст 5 мин

---

## 17. API — все эндпоинты

### Auth

| Метод | URL | Auth | Описание |
|-------|-----|------|---------|
| POST | `/api/auth/magic-link` | — | Создать magic link |
| GET | `/api/auth/verify?token=` | — | Verify + set cookie |
| GET | `/api/auth/me` | optional | Текущий пользователь |
| DELETE | `/api/auth/me` | — | Logout |
| PATCH | `/api/auth/profile` | ✓ | Обновить имя |
| DELETE | `/api/auth/profile` | ✓ | Удалить аккаунт |
| GET | `/api/auth/boostify/login` | — | OAuth2 start |
| GET | `/api/auth/boostify/callback` | — | OAuth2 callback |

### Catalog & Wallet

| Метод | URL | Auth | Описание |
|-------|-----|------|---------|
| GET | `/api/tiles` | — | Все тайлы по категориям |
| GET | `/api/tiles/featured` | — | Избранные тайлы |
| GET | `/api/balance` | ✓ | Баланс TEKI |

### Generation

| Метод | URL | Auth | Описание |
|-------|-----|------|---------|
| POST | `/api/uploads` | ✓ | Загрузить reference фото |
| POST | `/api/generate` | ✓ | Запустить генерацию |
| GET | `/api/generations` | optional | История генераций |
| GET | `/api/generations/{id}` | ✓ | Получить одну (polling) |
| POST | `/api/generations` | ✓ | Создать queued запись |
| POST | `/api/generations/{id}/share` | ✓ | Создать публичную ссылку |
| GET | `/api/share/{share_id}` | — | Публичный просмотр |

### Chat

| Метод | URL | Auth | Описание |
|-------|-----|------|---------|
| POST | `/api/chat` | — | Сообщение Arteki |

### Push

| Метод | URL | Auth | Описание |
|-------|-----|------|---------|
| GET | `/api/push/vapid-public-key` | — | VAPID public key |
| POST | `/api/push/subscribe` | ✓ | Подписаться на push |
| DELETE | `/api/push/subscribe` | ✓ | Отписаться |

### Other

| Метод | URL | Описание |
|-------|-----|---------|
| GET | `/health` | Healthcheck |
| POST | `/api/webhooks/boostify` | Boostify webhook |
| GET | `/uploads/{filename}` | Static files (FastAPI StaticFiles) |

---

## 18. Конфигурация (.env)

| Переменная | Значение (prod) | Описание |
|-----------|----------------|---------|
| `CORS_ORIGINS` | `http://193.149.190.155` | Разрешённые frontend origins |
| `PUBLIC_BASE_URL` | `http://193.149.190.155` | Базовый URL для upload-ссылок |
| `FRONTEND_URL` | `http://193.149.190.155` | Для redirect после auth |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis |
| `SIGNUP_TEKI_BALANCE` | `50` | Начальный баланс |
| `OPENAI_API_KEY` | *** | GPT-4o-mini + gpt-image-1 |
| `OPENAI_MODEL` | `gpt-4o-mini` | Модель для чата и промптов |
| `OPENAI_IMAGE_MODEL` | `gpt-image-1` | Модель для генерации |
| `OPENAI_IMAGE_SIZE` | `1024x1024` | Размер изображения |
| `OPENAI_IMAGE_QUALITY` | `medium` | Качество |
| `IMAGE_PROVIDER` | `openai` | `openai` или `pollinations` |
| `KIE_API_KEY` | *** | kie.ai (Seedance) |
| `KIE_VIDEO_MODEL` | `bytedance/seedance-2-fast` | Быстрая видеомодель |
| `KIE_VIDEO_AUDIO_MODEL` | `bytedance/seedance-2` | С аудио |
| `VIDEO_DURATION` | `5` | Длительность видео в сек |
| `VIDEO_RESOLUTION` | `720p` | |
| `VIDEO_ASPECT_RATIO` | `9:16` | Вертикальный |
| `VIDEO_POLL_TIMEOUT` | `600` | Max ожидание (сек) |
| `RATE_LIMIT_PER_HOUR` | `30` | Лимит генераций в час |
| `IMAGE_TEKI_COST` | `1` | |
| `VIDEO_TEKI_COST` | `2` | |
| `VAPID_PRIVATE_KEY` | *** | Web Push |
| `VAPID_PUBLIC_KEY` | *** | Web Push |
| `VAPID_EMAIL` | `mailto:hello@arteki.ai` | |
| `BOOSTIFY_MOCK` | `true` | Mock-режим оплаты |

---

## 19. Деплой и сервисы

### Systemd unit: `arteki-backend`

```
/opt/gen-backend → uvicorn app.main:app --host 0.0.0.0 --port 8000
Python venv: /opt/gen-backend/.venv
WorkingDirectory: /opt/gen-backend
Restart: always
```

### Systemd unit: `arteki-frontend`

```
/opt/gen-frontend → npm run start (Next.js production)
Port: 3000
Restart: always
```

### Nginx config

```nginx
server {
  listen 80;
  server_name 193.149.190.155;

  location /api/ {
    proxy_pass http://127.0.0.1:8000;
  }
  location /uploads/ {
    proxy_pass http://127.0.0.1:8000;
  }
  location / {
    proxy_pass http://127.0.0.1:3000;
  }
}
```

### Git-репозитории

- Backend: `https://github.com/intersson-sir/arteki-gen-backend.git`
- Frontend: отдельный репозиторий

### Управление

```bash
systemctl restart arteki-backend
systemctl restart arteki-frontend
systemctl status arteki-backend

journalctl -u arteki-backend -f    # live logs
```

---

## 20. Известные баги и P0/P1 проблемы

### 🔴 P0 — `animate_pet` полностью сломан

**Проблема:** Тайл `animate_pet` должен анимировать фото питомца пользователя. Но Seedance получает только motion-промпт, без реального фото. Итог: вместо питомца пользователя генерируется случайный AI-кот.

**Техническая причина:** `run_video_pipeline()` в `video_gen.py` проверяет `if photo_url and tile_id in {"animate_photo", "animate_pet"}`, но URL `/uploads/img_xxx.png` не является публично доступным для Seedance (нужен `https://...`), и часто `photo_url` не передаётся фронтом вообще.

**Нужно:** 1) Убедиться что фронт всегда передаёт `photo_url` для needs_photo тайлов. 2) URL должен быть `PUBLIC_BASE_URL + /uploads/...` — но сейчас `PUBLIC_BASE_URL=http://193.149.190.155` (HTTP, не HTTPS). Seedance может отказать.

---

### 🔴 P1 — Builder key mismatch (42% опций игнорируются)

**Проблема:** В `tiles_data.py` вопросы тайлов имеют опции вида `"Mountains"`, `"Sea"`, `"Fog"` и т.д. В `video_prompts.py` словари-маппинги используют ключи вида `"горы"`, `"море"`, `"туман"` или `"mountains"` (без capital letter). Пользователь выбирает `"Mountains"` → `_NATURE_PLACE.get("mountains".lower())` → надо `"mountains"` → **OK, если`.lower()` применяется**. Но `_INSPIRING_SCENE` содержит `"sunny landscape"` вместо `"sunny"`. Т.е. проблема не в регистре, а в том, что опции в тайлах (`"Mountains"`, `"Sunny"`) не совпадают с ключами словарей в video_prompts (`"sunny landscape"`, `"mountains"`).

Smoke-тест показал: **23 из 55 опций** (42%) не находят совпадения в builder-словарях → fallback на дефолтный пейзаж.

**Нужно:** Выровнять ключи в `video_prompts.py` под точные строки из `tiles_data.py` (с `.strip().lower()` сравнением).

---

### 🟡 P2 — Персонаж-приключенец выглядит как малыш

**Проблема:** `cartoon_character_video` с опцией "Human → Friendly adventurer" генерирует пухлого малыша, не взрослого приключенца. Стиль `STYLE_3D` с `"soft rounded chunky shapes"` доминирует.

**Нужно:** В `_build_cartoon_character_video()` для взрослых героев добавить `"adult proportions, heroic posture, confident strong character"` и убрать baby-образный дескриптор.

---

### 🟡 P3 — Birthday видео-поздравление иногда без шариков

**Проблема:** Два видео с одним тайлом `video_greeting + Birthday` дают разные сцены (у одного шарики есть, у другого нет). `_OCCASION_DATA["birthday"]` не детерминирован.

**Нужно:** Добавить `"birthday balloons and confetti"` как обязательный элемент сцены дня рождения.

---

### 🟡 P3 — Открытки заполняют весь кадр (нет места под текст)

**Проблема:** `graduation` и некоторые другие открытки генерируют персонажа по всему кадру. `LAYOUT_BLOCK` с `"generous negative space in upper third"` не всегда выполняется gpt-image-1.

**Нужно:** Усилить дескриптор пустого места: `"IMPORTANT: leave the TOP 30% of the image completely empty and clean for text overlay"`.

---

### 🟡 P4 — Текст-оверлей на видео не рендерится

**Проблема:** `video_greeting`, `morning_video`, `inspiring_video` имеют поле `text_overlay` в промпте (`VideoStoryboard.text_overlay`). Backend его сохраняет в промпт, но фронт не рендерит поверх видео никакого CSS-текста.

**Нужно:** Аналогично `CardTextOverlay.tsx` для изображений — сделать компонент `VideoTextOverlay`, который получает `text_overlay` из `Generation` и рисует его поверх видео-плеера.

---

### 🟡 P5 — `dall-e-3` fallback мёртв

**Проблема:** При ошибке `gpt-image-1` (HTTP 400/403/404) код пробует переключиться на `dall-e-3`. Но текущий OpenAI аккаунт не имеет доступа к `dall-e-3` → fallback тоже упадёт.

**Нужно:** Убрать dall-e-3 fallback и вместо него переключаться на Pollinations: `_fetch_to_uploads(build_image_url(prompt))`.

---

### 🟡 P6 — Push-уведомление на русском

**Проблема:** `push_service.send_push_to_user(title="🎬 Видео готово!", body=f"«{tile_label}» готово!")` — текст на русском.

**Нужно:** `"🎬 Your video is ready!"` и `f'"{tile_label}" is ready — tap to watch'`.

---

### Сводная таблица

| # | Тип | Проблема | Статус |
|---|-----|---------|--------|
| P0 | animate_pet сломан — фото не передаётся в Seedance | ❌ Не исправлено |
| P1 | 42% builder keys не матчатся → неправильный контент | ❌ Не исправлено |
| P2 | Персонаж-приключенец выглядит как малыш | ❌ Не исправлено |
| P3 | Birthday сцена нестабильна | ❌ Не исправлено |
| P3 | Открытки не оставляют место под текст | ❌ Не исправлено |
| P4 | Текст-оверлей на видео не рендерится | ❌ Не исправлено |
| P5 | dall-e-3 fallback мёртв | ❌ Не исправлено |
| P6 | Push текст на русском | ❌ Не исправлено |
