from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from environment / .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "TOONTOON API"
    # Единственное, что решает этот флаг, — молчат ли проверки при старте
    # (ниже: expose_dev_tokens, accept_storekit_test_root). Больше он не гейтит
    # ничего, и поэтому умолчание здесь false: с true обе проверки были глухи
    # по умолчанию, то есть охраняли только тех, кто и так задал переменную.
    # Разработчик включает его себе в `.env` явно.
    debug: bool = False
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    public_base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"
    auth_success_redirect: str = "/generate"

    # Mobile app-key + HMAC request signing (see docs/APP-KEY-SETUP.md).
    # DISABLED by default — flipping app_key_required to true rejects any /api/*
    # request that isn't signed by the app, so enable ONLY once mobile is verified
    # and web/webhooks are excluded or also signing. Values must match the app's
    # Config.xcconfig (APP_KEY / APP_SECRET).
    app_key: str = ""
    app_secret: str = ""
    app_key_required: bool = False
    app_sig_max_skew_seconds: int = 300
    # Paths under /api that are NEVER signature-checked (server-to-server or
    # signed-over-empty-body): uploads (multipart) + webhooks (external callers).
    app_key_exempt_prefixes: str = "/api/uploads,/api/webhooks"

    @property
    def app_key_exempt_list(self) -> list[str]:
        return [p.strip() for p in self.app_key_exempt_prefixes.split(",") if p.strip()]

    # Redis — sessions, cache, rate limits, locks. No longer a system of record.
    redis_url: str = "redis://localhost:6379/0"
    use_fake_redis: bool = False

    # PostgreSQL — everything that must survive a restart (app/db).
    # Port 5433 locally: 5432 is usually taken by another project's container.
    database_url: str = "postgresql+asyncpg://toontoon:toontoon@localhost:5433/toontoon"
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 5

    # Object storage (app/storage). "local" writes to ./uploads and is meant for
    # development only — it cannot enforce private access. Production uses "s3",
    # which is MinIO in our cluster and works unchanged with any S3-compatible
    # provider if we ever move.
    storage_backend: str = "s3"  # s3 | local
    s3_endpoint_url: str = "http://localhost:9100"  # MinIO; empty for real AWS
    s3_region: str = "us-east-1"
    s3_bucket: str = "toontoon-dev"
    s3_access_key: str = "toontoon"
    s3_secret_key: str = "toontoon-dev-secret"
    # Results are private: clients get a short-lived signed link, never a raw
    # object URL. Long enough to load a gallery screen, short enough that a
    # leaked link is worthless tomorrow.
    s3_signed_url_ttl_seconds: int = 900
    # Thumbnails for the history grid — the full-size image must never be what
    # a 3-across grid downloads.
    thumbnail_max_side: int = 512
    thumbnail_quality: int = 82

    # Sessions
    session_cookie_name: str = "toontoon-session"
    session_ttl_days: int = 30
    magic_link_ttl_minutes: int = 15
    password_reset_ttl_minutes: int = 60
    session_cookie_samesite: str = "lax"
    session_cookie_secure: bool = False
    signup_toontoon_balance: int = 30

    # ── Экономика ────────────────────────────────────────────────────────────
    # Значения взяты из разбора Glam AI (docs/ECONOMY.md) как стартовая точка.
    # Всё это параметры сервера: менять их не должно стоить релиза приложения.
    #
    # Ежедневные награды: дни 1–5 по 10, шестой 20, седьмой 30 — ровно 100
    # за неделю, столько же, сколько бесплатный тариф даёт целиком. Стрик
    # обнуляется при пропуске дня.
    daily_reward_schedule: str = "10,10,10,10,10,20,30"
    # Потолок несгораемой корзины. Награды копятся, и без потолка неактивный
    # пользователь накопил бы за месяц залп дорогих генераций.
    free_balance_cap: int = 300
    # Недельная квота бесплатного тарифа — она же сумма наград за неделю.
    free_weekly_quota: int = 100

    # Возврат токенов в ответе API вместо письма. Пока в кластере нет SMTP,
    # это единственный способ пройти magic-link и сброс пароля.
    #
    # Умолчание false, и это важнее, чем кажется. Раньше здесь стояло true:
    # развёртывание, где переменную забыли, отдавало токен сброса пароля к
    # любому аккаунту в ответе на открытый запрос. Забыть переменную — обычное
    # дело; помнить о ней должен тот, кому она нужна, то есть разработчик на
    # своей машине, а не тот, кто выкатывает. `.env` включает её явно.
    #
    # Обратное — включить в проде — ловит проверка при старте: DEBUG выключен,
    # а это включено. Умолчание и проверка закрывают разные ошибки: забывчивость
    # и намеренность.
    expose_dev_tokens: bool = False

    # Сколько последних сообщений уходит в модель (CH-20). Настройка сервера,
    # а не константа: подобрать это можно только на живых разговорах, и менять
    # ради подбора не должно стоить релиза.
    chat_context_messages: int = 20

    @property
    def daily_reward_list(self) -> list[int]:
        return [int(x) for x in self.daily_reward_schedule.split(",") if x.strip()]

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    # Image generation via OpenAI Images API.
    # Модель, которой рисует адаптер openai_images. Автопадения на dall-e-3
    # здесь больше нет: при отказе OpenAI реестр уводит запрос к следующему
    # провайдеру, а не подменяет модель внутри одного.
    openai_image_model: str = "gpt-image-1"
    # Ближайший вертикальный размер, который принимает gpt-image-1 (2:3).
    openai_image_size: str = "1024x1536"
    openai_image_quality: str = "medium"  # gpt-image-1: low/medium/high/auto; dall-e-3: standard/hd
    openai_image_timeout: float = 55.0

    @property
    def openai_enabled(self) -> bool:
        return bool(self.openai_api_key.strip())

    # Generation
    rate_limit_per_hour: int = 30
    image_toontoon_cost: int = 1
    video_toontoon_cost: int = 2
    # Какая модель исполняет операцию — решает реестр провайдеров в базе
    # (таблица generation_providers), а не настройка: включение модели и порядок
    # фолбэка не должны требовать релиза.

    # ─── OpenRouter ──────────────────────────────────────────────────────────
    # Витрина, а не модель: один ключ и один эндпоинт открывают четыре десятка
    # генераторов от восьми вендоров — Gemini, GPT Image, FLUX, Seedream,
    # Recraft, Riverflow, Qwen, Grok. Для сравнения это главное: чтобы замер
    # чего-то стоил, кандидатов надо гонять на одном промпте и одних лицах, а
    # не собирать пять аккаунтов с пятью схемами авторизации.
    #
    # Что модель принимает на вход, известно заранее: GET /api/v1/images/models
    # отдаёт на каждую `input_references` — сколько референсных снимков она
    # берёт. Ноль означает, что фотографию приложить некуда, и такой моделью
    # обещание «ты в этом стиле» не выполнить.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_image_model: str = "google/gemini-3.1-flash-image"
    # Вертикаль по умолчанию: продукт мобильный, результат смотрят с
    # телефона, и квадрат там выглядит обрезанным.
    openrouter_aspect_ratio: str = "9:16"
    openrouter_resolution: str = "1K"
    openrouter_output_format: str = "png"
    # Ступень качества у тех, кто её принимает (семейство GPT Image): auto,
    # low, medium, high. Это не только детализация, но и цена: ступень задаёт
    # число токенов на выходе, а платим мы за токены. Разница между краями
    # шкалы — больше чем в десять раз, поэтому значение вынесено в настройку,
    # а не зашито.
    # Не `auto`: он молча выбирает между low и medium, и себестоимость
    # кадра скачет втрое. Средняя ступень — то, на чём проверено, что
    # стиль выполняется целиком.
    openrouter_quality: str = "medium"
    # Текстовая модель — той же витриной, что и картинки.
    #
    # Раньше текст ходил напрямую в OpenAI, и когда там кончились деньги,
    # сборщик промптов молча умер: запрос падал с 429, а приложение продолжало
    # работать на механическом раскладывании слов по слотам. Молча — и есть
    # худшее в этой истории.
    #
    # gpt-4o-mini: 0.15 доллара за миллион входных токенов. Разбор фразы — это
    # сотни токенов, то есть сотые доли цента на фоне четырёх центов за кадр.
    openrouter_text_model: str = "openai/gpt-4o-mini"
    # Модель только для разбора фразы по слотам, отдельно от сборщика промптов.
    #
    # Разделено потому, что задачи разные и мерятся разным. Сборщик пишет абзац,
    # и плохо написанный абзац видно глазами на кадре. Разбор решает, о чём
    # разговор промолчит: его ошибку видно только по тому, что человека
    # переспросили или записали ему не то, — и мерить её приходится разметкой
    # (`scripts/eval_slots.py`).
    #
    # Выбрана замером на двадцати фразах в двух прогонах: gemini-2.5-flash не
    # соврал ни разу и взял 93% сказанного за 0.9 секунды. Прежний gpt-4o-mini
    # вдвое дешевле, но соврал дважды на сорок; flash-lite ещё дешевле и не
    # переводит русский; gpt-5-nano честен, но берёт 59% и думает по девять
    # секунд — в разговоре это ответ на уже отвеченный вопрос. Разница в цене
    # здесь — 27 центов на тысячу разборов против 12, то есть три сотых цента на
    # разбор при четырёх центах за кадр: экономить тут не на чем.
    #
    # Идентификатор витрины. Без её ключа настройка молча не действует — разбор
    # уйдёт на общую модель.
    slot_extraction_model: str = "google/gemini-2.5-flash"
    # Сколько снимков профиля отдавать модели как референсы.
    #
    # Один. Тройка стояла здесь по вере — «лишние ракурсы помогают сходству», —
    # и замер 25 августа 2026 её опроверг: три лица, три варианта, по шесть
    # повторов, сходство меряется ArcFace, а не мнением судьи.
    #
    #     1 реф.: медиана 0.449  среднее 0.505  провалов 1/18
    #     3 реф.: медиана 0.396  среднее 0.401  провалов 3/18
    #     5 реф.: медиана 0.522  среднее 0.479  провалов 2/18
    #
    # Тройка проиграла по всем трём меркам и не выиграла ни на одном лице.
    # Один против пяти — без победителя: у пяти лучше медиана, но вытянута она
    # одним лицом из трёх, а у одного меньше провалов, выше среднее, и он на
    # четыре-восемь секунд быстрее.
    #
    # Важная оговорка к выводу: набор референсов упорядочен по чёткости лица —
    # и здесь, и в `references()`, — так что «больше снимков» на деле означает
    # «добавляем всё более слабые». Замер говорит не «количество вредно», а
    # «слабый снимок отнимает больше, чем добавляет лишний ракурс».
    #
    # Рисованному кадру три снимка вредили и раньше: тот же постер с тремя
    # референсами дважды пришёл фотографией, с одним — дважды аниме-постером.
    # Решает это `_reference_take`, здесь только потолок — и ветка там нужна
    # по-прежнему, чтобы рисованный путь не уехал вслед за этой цифрой, если
    # её однажды поднимут обратно.
    #
    # Профиль при этом хранит все пятнадцать: цифра меняет отбор, а не сборку.
    profile_reference_count: int = 1
    openrouter_request_timeout: float = 180.0

    # Video generation (kie.ai / ByteDance Seedance).
    # ВЫКЛЮЧЕНО в первой версии: все шесть направлений онбординга — про фото,
    # видео в них не упомянуто ни разу, а стоит оно дороже всего ($1.21–1.52
    # за пятисекундный клип). Код остаётся, маршрут закрыт флагом.
    video_enabled: bool = False
    kie_api_key: str = ""
    kie_base_url: str = "https://api.kie.ai"
    kie_video_model: str = "bytedance/seedance-2-fast"
    # Audio-capable model used for nature kinemagraphs (Seedance 2.0 with generate_audio).
    kie_video_audio_model: str = "bytedance/seedance-2"
    video_frame_count: int = 4
    video_resolution: str = "720p"
    video_aspect_ratio: str = "9:16"
    video_duration: int = 5
    video_poll_interval: float = 6.0
    # bytedance/seedance-2 (audio-capable model, used for all text-overlay
    # tiles: morning/inspiring/greeting) occasionally needs more than 10 min.
    video_poll_timeout: float = 900.0

    @property
    def kie_enabled(self) -> bool:
        return bool(self.kie_api_key.strip())

    # Web Push (VAPID)
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_email: str = "mailto:hello@toontoon.ai"

    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_private_key.strip() and self.vapid_public_key.strip())

    # Cloudinary — video text overlay (bakes text_overlay into the rendered mp4).
    # Leave cloud_name empty to skip overlay (videos delivered without text).
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""

    @property
    def cloudinary_enabled(self) -> bool:
        return bool(
            self.cloudinary_cloud_name.strip()
            and self.cloudinary_api_key.strip()
            and self.cloudinary_api_secret.strip()
        )

    # Google OAuth (Sign in with Google — web + native app). Empty by default:
    # the routes exist and respond 503 "not configured" instead of 404 until
    # real credentials are set (Google Cloud Console → OAuth client ID, Web
    # application type — used for BOTH web and app, since the app opens the
    # same browser-based consent screen via ASWebAuthenticationSession).
    google_client_id: str = ""
    google_client_secret: str = ""
    # Leave empty to auto-derive as {public_base_url}/api/auth/google/callback.
    google_redirect_uri: str = ""

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id.strip() and self.google_client_secret.strip())

    @property
    def google_redirect_uri_effective(self) -> str:
        return self.google_redirect_uri.strip() or f"{self.public_base_url}/api/auth/google/callback"

    # Sign in with Apple. Unlike Google/Boostyfi this needs NO client secret
    # on our side — the app talks to Apple directly and hands us a signed
    # identity_token (JWT) that we verify against Apple's public JWKS. We
    # only need to know our own audience(s) to check the `aud` claim: the
    # iOS app's Bundle ID (native flow) and/or a Services ID (web/Android
    # "Sign in with Apple" button, if ever added). Set at least one to enable.
    # Принимать чеки, подписанные локальным корнем StoreKit из Xcode.
    #
    # Настоящую песочницу Apple без платного членства не открыть, а проверить
    # весь путь узнавания надо раньше. Локальная конфигурация Xcode выдаёт
    # НАСТОЯЩИЙ подписанный чек той же формы — только корень у него свой,
    # тестовый, и лежит он внутри самого Xcode.
    #
    # В проде ОБЯЗАН быть false: с ним любой, кто достанет этот общедоступный
    # сертификат, выпишет себе подписку сам. Приложение при старте кричит, если
    # `debug` выключён, а это включено.
    accept_storekit_test_root: bool = False
    apple_bundle_id: str = ""
    apple_service_id: str = ""

    @property
    def apple_enabled(self) -> bool:
        return bool(self.apple_bundle_id.strip() or self.apple_service_id.strip())

    @property
    def apple_audiences(self) -> list[str]:
        return [a for a in (self.apple_bundle_id.strip(), self.apple_service_id.strip()) if a]

    # Native-app custom URL scheme. After a browser-based OAuth flow (Google)
    # started with ?platform=app, the callback redirects here instead of the
    # web frontend so the app (ASWebAuthenticationSession) can capture the
    # session token from the URL.
    app_deep_link_scheme: str = "toontoon"

    # Boostyfi (OAuth + Wallet)
    boostify_mock: bool = True
    boostify_base_url: str = "https://api.boostyfi.com/api/v1"
    boostify_client_id: str = "toontoon"
    boostify_client_secret: str = "change-me"
    boostify_redirect_uri: str = "http://localhost:8000/api/auth/boostify/callback"
    boostify_webhook_secret: str = "change-me-too"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def session_ttl_seconds(self) -> int:
        return self.session_ttl_days * 24 * 60 * 60

    @property
    def magic_link_ttl_seconds(self) -> int:
        return self.magic_link_ttl_minutes * 60

    @property
    def password_reset_ttl_seconds(self) -> int:
        return self.password_reset_ttl_minutes * 60


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
