# Full-stack playbook: нативное iOS-приложение 1:1 с веб-продуктом (клиент + сервер)

Единый переиспользуемый гайд для сборки следующих приложений по той же схеме, что и
TOONTOON. Подходит для любого продукта вида **«есть веб-версия (Next.js/React) +
бэкенд-API, нужно нативное iOS-приложение, повторяющее веб 1:1»**. Копируй файл в
новый проект и иди по шагам. Всё проверено на реальном проекте.

Документ состоит из трёх частей:
- **Часть A — КЛИЕНТ** (SwiftUI-приложение): §A0–A14.
- **Часть B — СЕРВЕР + OPS** (FastAPI-бэкенд, деплой, инфра): §B1–B15.
- **Часть C — ОБЩЕЕ** (контракт, деплой-дисциплина, sizing, роли, грабли,
  пошаговый план): §C1–C7.

> **Философия: приложение — тонкий клиент, сервер — вся правда.** Логика/генерация/
> данные/секреты провайдеров — только на сервере. Приложение = дизайн 1:1 + сеть +
> состояние экранов. Дизайн/код — из репо, данные — с сервера, секреты — из
> локального git-ignored конфига. Клиент и сервер — **две зоны ответственности**,
> синхронизируемые через доки-контракт (§C1, §C4).

---
---

# ЧАСТЬ A — КЛИЕНТ (SwiftUI-приложение)

## A0. Стек (что берём и почему)

| Слой | Выбор | Почему |
|---|---|---|
| UI | **SwiftUI, min iOS 16** | нативно, быстро, декларативно; iOS 16 покрывает ~98% |
| Архитектура | **MVVM** (`*View` + `*ViewModel: ObservableObject`) | тестируемо, ясное разделение |
| Проект | **XcodeGen** (`project.yml`) | нет `.xcodeproj` в git, проект генерируется → нет merge-конфликтов |
| Сборка/прогон | **`xcodebuild` + `xcrun simctl`** (без Xcode GUI) | headless loop: собрал → поставил → запустил → скриншот |
| Сеть | **URLSession + async/await**, тонкий типизированный `APIClient` | без тяжёлых зависимостей |
| Модели | `Codable`, **snake_case под JSON бэка** | без CodingKeys, декодер по умолчанию |
| Безопасность | HTTPS + **cert pinning** + app-key/**HMAC-подпись** | защита канала без домена |
| Аналитика | SDK-агностик фасад + **свой `/events`** + адаптеры (PostHog/TelemetryDeck) | приватность, App Review без ATT |
| Шрифты | вариативный `.ttf` в `Resources/Fonts` + `UIAppFonts` | типографика 1:1 с вебом |
| Ассеты | SVG→PNG через headless Chrome, webp→png | Xcode плохо рендерит сложные SVG |
| Конфиг/секреты | `Config.xcconfig` (**git-ignored**) → Info.plist → `AppConfig` | в коде секретов нет |

**Инструменты на машине:** Xcode 15+, `xcodegen` (`brew install xcodegen`), Node
(растеризация ассетов через Playwright/Chrome), `curl`/`python3` (проверка API).

## A1. Структура репозитория (клиент)

```
project.yml                     # XcodeGen: таргет, iOS16, Config.xcconfig, excludes
Config.example.xcconfig         # ШАБЛОН (в git) — плейсхолдеры, без значений
Config.xcconfig                 # РЕАЛЬНЫЕ значения (в .gitignore!) — URL, ключи, пин
.gitignore                      # Config.xcconfig, *.env, *.p8, DerivedData, .DS_Store …
docs/                           # вся дока (см. §C4)
AppName/
  App/
    AppNameApp.swift            # @main, RootView-роутер, init аналитики/мониторинга
    AppConfig.swift             # ЕДИНАЯ точка конфигурации (адрес, ключи, пин)
    Info.plist                  # bundle-ключи, ATS-исключение, UIAppFonts, проброс конфига
  DesignSystem/
    Theme.swift                 # токены: цвета/отступы/радиусы/шрифт (1:1 с вебом)
  Networking/
    APIClient.swift             # тонкий клиент над URLSession
    APISecurity.swift           # пиннинг + app-key + HMAC подпись + запрет http
    Models.swift                # Codable-модели под контракт бэка
  Auth/
    AuthManager.swift           # состояние авторизации (ObservableObject)
    KeychainStore.swift         # хранение токена сессии
  Analytics/
    Analytics.swift             # фасад + буфер + консент-гейт + SessionTracker
    AnalyticsEvent.swift        # типобезопасный каталог событий (snake_case)
    ConsentManager.swift        # согласие (GDPR), гейтит всё
    ServerAnalyticsClient.swift # свой /events (дефолт, 0 третьих лиц)
    PostHogClient.swift         # адаптер (#if canImport) — включается пакетом+ключом
    CompositeAnalyticsClient.swift # фан-аут в несколько бэкендов
    ScreenTracking.swift        # .trackScreen("…") → screen_view
  Features/
    Auth/                       # Login/Register/Verification/ForgotPassword/Consent + AuthComponents
    <Feature>/                  # экран + <Feature>ViewModel
  Resources/
    Fonts/                      # .ttf (в git если лицензия позволяет, иначе локально)
    Images/                     # исходные SVG/PNG (excluded из таргета)
    Assets.xcassets             # сгенерированные imagesets (из растеризации)
    PrivacyInfo.xcprivacy       # privacy-manifest (обязателен для App Store)
```

## A2. XcodeGen (`project.yml`) — скелет

```yaml
name: AppName
options: { bundleIdPrefix: com.company, deploymentTarget: { iOS: "16.0" }, createIntermediateGroups: true }
configs: { Debug: debug, Release: release }
settings:
  base: { SWIFT_VERSION: "5.0", CODE_SIGNING_REQUIRED: "NO", CODE_SIGNING_ALLOWED: "NO" }  # NO — только для симулятора
targets:
  AppName:
    type: application
    platform: iOS
    deploymentTarget: "16.0"
    configFiles: { Debug: Config.xcconfig, Release: Config.xcconfig }
    sources:
      - path: AppName
        excludes: [ "**/README.md", "Resources/Images/**" ]   # исходные картинки не в таргет
    settings:
      base:
        INFOPLIST_FILE: AppName/App/Info.plist
        PRODUCT_BUNDLE_IDENTIFIER: com.company.appname
        GENERATE_INFOPLIST_FILE: "NO"          # свой Info.plist → нужны bundle-ключи вручную
        ASSETCATALOG_COMPILER_APPICON_NAME: "" # пока нет иконки
        TARGETED_DEVICE_FAMILY: "1"            # iPhone
```

> Свой `Info.plist` при `GENERATE_INFOPLIST_FILE=NO` требует стандартных ключей
> вручную: `CFBundleExecutable/Identifier/Name/PackageType`, `LSRequiresIPhoneOS`,
> `CFBundleShortVersionString/Version`. Иначе install падает с «Missing bundle ID».

## A3. Конфигурация и секреты (клиент)

**Правило:** значения приходят из `Config.xcconfig` → Info.plist → `AppConfig`. В
коде — только плейсхолдеры/чтение. Провайдерские ключи — ТОЛЬКО на сервере.

```swift
enum AppConfig {
    static var baseURL: URL { url("APP_BASE_URL", default: "https://…") }
    static var pinnedSPKISHA256: String? { str("APP_PINNED_SPKI") }
    static var appKey: String? { str("APP_KEY") }
    static var appSecret: String? { str("APP_SECRET") }
    static var posthogKey: String? { str("APP_POSTHOG_KEY") }
    static func mediaURL(_ p: String?) -> URL? { /* относит. пути с сервера → абсолютные */ }
    private static func str(_ k: String) -> String? { (Bundle.main.object(forInfoDictionaryKey: k) as? String)?.nilIfEmpty }
}
```
`Info.plist` пробрасывает каждый ключ: `<key>APP_BASE_URL</key><string>$(APP_BASE_URL)</string>`.

**⚠️ Ловушка `.gitignore`:** НЕ пиши inline-комментарий в той же строке, что и
паттерн — git примет всё как паттерн и файл НЕ будет игнориться. Правильно:
```
# real values — use Config.example.xcconfig
Config.xcconfig
```
В `.xcconfig` `//` в значении экранируй как `/$()/` (иначе обрежет как комментарий):
`APP_BASE_URL = https:/$()/api.example.com`.

**Parity с сервером:** `APP_KEY`/`APP_SECRET` тут == значениям в серверном `.env`.

## A4. Дизайн-система (Theme.swift) — фундамент «1:1 с вебом»

Сначала снимаем токены с веба (Tailwind config / CSS-переменные / девтулзы) и
переносим точь-в-точь:
```swift
enum AppColor { static let bg = Color(hex: 0x0A0A0A); /* … все цвета из веба */ }
enum Spacing  { static let s: CGFloat = 8; static let base: CGFloat = 16; /* … */ }
enum Radius   { static let s: CGFloat = 8; static let l: CGFloat = 16; static let pill: CGFloat = 100 }
enum AppFont  { static func sans(_ s: CGFloat, _ w: Font.Weight = .regular) -> Font {
    .custom("Font Family Name", size: s).weight(w) } }
```
**Правило проекта:** ТОЛЬКО токены из Theme — тогда любой новый экран автоматически
в едином стиле. Шрифт: `.ttf` в `Resources/Fonts` + `UIAppFonts` + точное имя семейства.

## A5. Слой сети (APIClient)

- Тонкий типизированный клиент над `URLSession` (async/await).
- Единый `makeRequest(path, method, body)` — тут вешаем `Cookie`/токен сессии +
  `APISecurity.assertTransportAllowed` + `APISecurity.decorate` (подпись).
- Единый `send<T: Decodable>` — статусы, декод, `APIError` (http/transport/decoding).
- Модели `Codable` в **snake_case** = имена полей JSON бэка (без CodingKeys).
- **Медиа с сервера часто приходит относительными путями** (`/uploads/x.png`) —
  всегда резолвить через `AppConfig.mediaURL()` перед `AsyncImage`/`AVPlayer`.
- Enum-поля бэка (`provider`, `status`, …) — **перечислить ВСЕ значения**, иначе
  декод падает (частый баг). Или сделать декод устойчивым к неизвестным.

Аутентификация — два рабочих паттерна:
1. **Cookie-сессия** (httpOnly): ловим `Set-Cookie` на редиректе (блокируем
   редирект в `URLSessionTaskDelegate`), храним значение в Keychain, шлём как `Cookie:`.
2. **Токен в JSON** (email+пароль): `{user, session_token}` → токен в Keychain →
   `Cookie`/`Authorization` на каждый запрос. ← предпочтительно для мобилки.

## A6. Безопасность канала (APISecurity, клиентская половина)

Всё в одном файле `Networking/APISecurity.swift`:
1. **TLS cert pinning** — сверяем SPKI сертификата сервера с пином из конфига
   (`urlSession(_:didReceive:)` → server-trust challenge). Доверяем ТОЛЬКО нашему
   серверу; работает даже с self-signed (без домена), MITM невозможен.
   Пин снять: `openssl s_client -connect host:443 | openssl x509 -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64`.
2. **App-key заголовок** — дешёвый барьер от чужих ботов.
3. **HMAC-подпись** каждого запроса: `sig = HMAC_SHA256(secret, "METHOD\nPATH\nTS\nsha256(body)")`,
   шлём `X-App-Key/X-Timestamp/X-Signature`. Сервер собирает канон 1:1 (§B5).
4. **Запрет http в релизе** (`precondition(scheme=="https")`).

> Честно: секрет зашит в бинарник → извлекаем реверсом. Это «поднять планку», не
> абсолют. Настоящая подлинность клиента = **Apple App Attest** (к релизу).

Self-signed по IP: держи ATS-исключение в Info.plist для этого хоста; при
переопределении server-trust в делегате self-signed принимается.

## A7. Авторизация (полный набор экранов)

Единый набор: `AuthComponents` (карта/поле/кнопка/close — 1:1 с вебом) →
переиспользуется в `LoginView`/`RegisterView`/`VerificationView`/`ForgotPasswordView`.

- **email+пароль**: register→`{user, token}`, login→`{user, token}`.
- **Сброс пароля** (работает и без почты в dev): `forgot-password` возвращает
  reset-токен прямо в ответе → клиент подставляет; `reset-password` меняет хэш.
- **Верификация email**: экран-заглушка, пока нет почты на сервере; заменить на
  реальный вызов, когда сервер подключит ESP.
- `AuthManager: ObservableObject` держит `user`, `isAuthenticated`, ошибки; `RootView`
  роутит: `Landing → Login/Register → (Verification) → Main`.
- iOS 16: `Text.foregroundStyle` — **iOS 17 only**; в контексте Text (`+`, `prompt:`)
  используй `.foregroundColor`. `.presentationBackground` — iOS 16.4+ (не для 16.0).

## A8. Аналитика (клиент; глубокая + проходит App Review)

**Apple:** без сквозного трекинга между приложениями → **ATT-баннер не нужен**;
обязателен `PrivacyInfo.xcprivacy` + Privacy Nutrition Labels; никакого
fingerprinting; согласие (GDPR) до сбора; рекламных провайдеров не берём.

**Архитектура (SDK-агностик):**
- `protocol AnalyticsClient { track/setUserProperties/resetIdentity }`.
- `Analytics` — фасад: буферизует до согласия, добавляет конверт (`session_id`,
  `app_version`, анонимный `anon_id`), гейтит через `ConsentManager`.
- `ServerAnalyticsClient` — свой `/events` (дефолт, 0 третьих лиц).
- `PostHogClient`/`TelemetryDeckClient`/`SentryMonitoring` — адаптеры под
  `#if canImport(...)` (проект собирается без пакета; включение = SPM-пакет + ключ).
- `CompositeAnalyticsClient` — событие сразу в несколько бэкендов.
- `AnalyticsEvent` — типобезопасный каталог (snake_case, `object_action`, **без PII
  и содержимого промптов**).

**Что трекать:** `screen_view` со всех экранов + `app_opened`; воронка продукта
(`…_selected → …_confirmed → …_started → …_succeeded(+duration_ms)/failed`) + экономика
(`spent`, `insufficient_balance`) + auth (register/login success/failed, logout,
password_reset_*, profile-изменения) + ретеншн по `anon_id`.

## A9. Фичи (MVVM) и типовой «chat-engine»

Каждый экран: `FeatureView` + `FeatureViewModel: ObservableObject` (`@MainActor`,
`@Published`). Вью тонкая, логика/сеть — во ViewModel.

Диалоговый продукт (генерация через чат): `messages: [Bubble]`
(userText/userImages/aiText/aiImage/aiVideo/error) + `flow: enum`
(pick → answer → confirm → generating) + локальные `sessions` (история). **Видео:
создавай `AVPlayer` ОДИН раз (`@State`/`StateObject`), loop через один observer,
observer снимай в `deinit`/`.onDisappear`** (иначе рестарт на ре-рендер + утечка).

## A10. Ассеты (pipeline растеризации)

Xcode плохо рендерит сложные SVG (градиенты, чёрные подложки). Поэтому:
1. Забираем официальные ассеты веба (SVG/PNG/webp/лого).
2. **SVG → PNG** через headless Chrome (Playwright, канал system Chrome) — точный рендер.
3. **webp → png** через `sips`.
4. Складываем как **single-scale universal imagesets** в `Assets.xcassets` (имена
   без расширения = как в коде: `icon-x`, `logo`, …).

## A11. Build / Run / Verify loop (без Xcode GUI)

```bash
xcodegen generate                                   # 1. .xcodeproj из project.yml
xcodebuild -project AppName.xcodeproj -scheme AppName \
  -configuration Debug -sdk iphonesimulator \
  -destination 'platform=iOS Simulator,name=iPhone 17' \
  -derivedDataPath ./DD build                        # 2. собрать
xcrun simctl boot "iPhone 17"; open -a Simulator     # 3. симулятор
xcrun simctl install "iPhone 17" ./DD/Build/Products/Debug-iphonesimulator/AppName.app
xcrun simctl launch  "iPhone 17" com.company.appname # 4. запустить
xcrun simctl io "iPhone 17" screenshot out.png       # 5. скриншот → визуальная проверка
```
**Dev-хуки** (только симулятор) через `SIMCTL_CHILD_*`: сид сессии
(`APP_DEV_SESSION=<cookie>`), прыжок на экран (`APP_DEV_SCREEN=profile|…`) — читать в
`AuthManager.bootstrap()`/роутере, чтобы скриншотить состояния без тапов.

> SourceKit в редакторе часто даёт ЛОЖНЫЕ «Cannot find type X» — ориентируйся на
> реальный вывод `xcodebuild`, а не на диагностику редактора.

## A12–A14. (см. общие §C — контракт, роли, грабли, план)

---
---

# ЧАСТЬ B — СЕРВЕР + OPS (FastAPI-бэкенд)

## B1. Стек сервера (что берём и почему)

| Слой | Выбор | Почему |
|---|---|---|
| API | **FastAPI + uvicorn** (async) | типизация (pydantic), автосхема OpenAPI, async |
| Хранилище сессий/кэша/лимитов | **Redis** | быстрые TTL-ключи: сессии, токены, rate-limit |
| БД (если нужна) | Postgres / Redis-as-store | по объёму; для MVP хватало Redis |
| Конфиг | **pydantic-settings** (`.env`) | типобезопасно, `extra="ignore"` |
| Пароли | **Argon2id** (`argon2-cffi`) | современный KDF, не bcrypt-легаси |
| Прокси/TLS | **nginx** (+ Let's Encrypt к релизу) | TLS-терминация, `X-Forwarded-For` |
| Процесс | **systemd unit** (`*.service`) | автозапуск, `restart`, журнал |
| Подпись запросов | **hmac + hashlib** (stdlib) | без зависимостей, канон 1:1 с клиентом |

## B2. Структура бэкенда (универсальная)

```
app/
  main.py                 # FastAPI(), lifespan, CORS, middleware, include_router, /health
  config.py               # Settings(BaseSettings) — ВСЕ настройки/секреты из .env
  redis_client.py         # connect/disconnect/get_client
  deps.py                 # Depends: required_context / optional_context (auth-контекст)
  cookies.py              # set/clear session cookie (SameSite, secure, httpOnly)
  core/
    security.py           # hash_password/verify_password (Argon2), sign_payload/verify_signature, new_token/new_id
    rate_limit.py         # Redis fixed-window: hit(key, limit, window) -> (allowed, remaining)
  middleware/
    app_key.py            # ASGI: app-key + HMAC проверка (см. §B5)
  models/                 # pydantic-модели запросов/ответов (snake_case!)
  services/               # бизнес-логика (auth_service, wallet, providers…)
  routers/                # эндпоинты по доменам (auth, profile, generate, events, webhooks…)
.env                      # РЕАЛЬНЫЕ секреты (в .gitignore!)
.env.example              # шаблон без значений (в git)
```
Роутер тонкий (валидация + вызов сервиса), логика — в `services/`, общее — в `core/`.
Имена полей моделей = **snake_case JSON** (parity с клиентскими `Codable`).

## B3. Конфиг и секреты (parity с приложением)

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    openai_api_key: str = ""      # секреты провайдеров — ТОЛЬКО тут
    app_key: str = ""             # parity с app Config.xcconfig
    app_secret: str = ""
    app_key_required: bool = False
```
`APP_KEY`/`APP_SECRET` в `.env` == `TOONTOON_APP_KEY`/`TOONTOON_APP_SECRET` в клиентском
`Config.xcconfig` (оба git-ignored, значения байт-в-байт). Провайдерские ключи —
только на сервере. Засветился секрет → **сразу ротация** (§C5).

## B4. Auth — серверный blueprint

**Мобилка:** возвращай токен сессии **в JSON body**, cookie ставь для веба — один
эндпоинт на оба клиента:
```python
@router.post("/register", response_model=AuthResult, status_code=201)
async def register(body, request, response):
    ...  # Argon2 hash, one-per-email
    session = await auth_service.create_session(user)
    set_session_cookie(response, session.sid)                                 # веб
    return AuthResult(user=PublicUser.from_user(user), session_token=session.sid)  # апп
```
**Паттерн «dev-токен вместо письма»** (без ESP/домена): magic-link → `{ok, devLink}`;
сброс пароля → `/forgot-password` всегда `200`, при наличии аккаунта добавь
`{devToken, devLink}`, `/reset-password {token, new_password}` меняет Argon2-хэш
(Redis-ключ `reset:{token}`, TTL ~1ч, одноразовый). ❗️ При подключении почты —
**перестать возвращать `devToken`** (раскрывает существование аккаунта), пометь
`TODO(email)`. Enum провайдера/статусов держи стабильным; новое значение → обнови
и клиентский enum (§C1).

## B5. App-key + HMAC middleware (серверная половина)

Канон **байт-в-байт с клиентом** (§A6):
```
canonical = METHOD "\n" PATH "\n" TIMESTAMP "\n" sha256_hex(body)
signature = hex(HMAC_SHA256(APP_SECRET, canonical))    # headers: X-App-Key/X-Timestamp/X-Signature
```
`PATH` — с `/api`, без query; `TIMESTAMP` — unix-сек строкой; `body` пустое для GET и
multipart → **исключай `/uploads`**; внешние вебхуки не подписаны нашим ключом →
**исключай `/webhooks`**.

**Реализуй ЧИСТЫМ ASGI-middleware, НЕ `BaseHTTPMiddleware`** (у последнего проблемы
с re-read тела). Буферизуем тело → проверяем → «реплеим» через кастомный `receive`:
```python
class AppKeyMiddleware:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not settings.app_key_required:
            return await self.app(scope, receive, send)
        path = scope["path"]
        if not path.startswith("/api/") or self._exempt(path):
            return await self.app(scope, receive, send)
        body = b""
        while True:
            m = await receive(); body += m.get("body", b"")
            if not m.get("more_body"): break
        if err := self._verify(scope, path, body):
            return await JSONResponse({"detail": err}, status_code=401)(scope, receive, send)
        done = {"v": False}
        async def replay():
            if not done["v"]: done["v"]=True; return {"type":"http.request","body":body,"more_body":False}
            return await receive()
        await self.app(scope, replay, send)
```
`_verify`: сверь `app_key` (`hmac.compare_digest`), наличие ts/sig, свежесть ts
(±300с против replay), затем `hmac.compare_digest(expected, sig)`.

**Рубильник `APP_KEY_REQUIRED=false` по умолчанию** — выключенный middleware
пропускает всё без изменений (ноль влияния на прод). Включать `true` **нельзя**,
пока веб-фронт не подписывает те же общие роуты (`/login`, `/generate`), иначе веб
ляжет.

## B6. Rate limiting (Redis fixed-window)

`core/rate_limit.py`: `hit(key, limit, window) -> (allowed, remaining)` (`INCR`+`EXPIRE`).
Лимитируй **по IP и по email/user**:
```python
ok_ip, _    = await rate_limit.hit(f"login:ip:{client_ip(req)}", 20, 900)
ok_email, _ = await rate_limit.hit(f"login:email:{email}", 8, 900)
```
`client_ip` за nginx: `X-Real-IP` / первый `X-Forwarded-For` / peer. Ставь на
`register/login/forgot-password/reset-password/magic-link/events/generate`.

## B7. First-party аналитика (`/events`)

```python
@router.post("/events")            # тело: {anon_id, events:[{name, ts, props}]}
async def ingest(body, request):
    ok,_ = await rate_limit.hit(f"events:ip:{client_ip(request)}", 120, 60)
    ...  # писать в Redis-стрим/лог/БД; НИКАКОГО PII
```
Дефолт-аналитика без третьих лиц (идеально для App Review). Продуктовую глубину
добавляет PostHog поверх — **но см. §C3 про sizing.**

## B8. Медиа/загрузки

`POST /uploads` (multipart) → сохранить → вернуть URL (часто относительный
`/uploads/x.png` → клиент резолвит через `AppConfig.mediaURL()`). Роут исключён из
HMAC (§B5).

## B9–B15. (см. общие §C — контракт, деплой, sizing, роли, релиз, грабли)

---
---

# ЧАСТЬ C — ОБЩЕЕ (контракт, ops, роли, грабли, план)

## C1. Контракт-parity app ↔ server (частый источник багов)

Чек-лист при каждом изменении стыка:
- [ ] **Имена полей** = snake_case, совпадают с клиентскими `Codable`.
- [ ] **Enum-значения** (`provider/status/type`) — при добавлении обнови ОБЕ стороны.
- [ ] **Формы ответа** совпадают (реальный баг: `/transactions` отдавал `created_at`
      строкой + `type/prompt`, а клиент ждал `reason/status/Int` → падал декод).
- [ ] **HMAC-канон** байт-в-байт (METHOD/PATH-с-/api/TS/sha256(body)). Проверь
      независимым скриптом: собери подпись на Python, дёрни прод.
- [ ] **Пустое тело** = `sha256("")` — GET и uploads.
- [ ] `openapi.json` экспортирован и лежит в обоих репо.

## C2. ⭐ Безопасный деплой на ЖИВОЙ прод (главная ops-дисциплина)

Правило: **изменения аддитивные и обратимые; проверяй ДО рестарта прод-сервиса.**
1. **Аддитивно:** новые ручки/поля/флаги, дефолты не меняют текущее поведение
   (напр. `APP_KEY_REQUIRED=false`). Существующие контракты не трогать.
2. **Import-check:** `python -c "import app.main"`.
3. **Тест на ВРЕМЕННОМ порту** (прод не трогаем):
   ```bash
   APP_KEY_REQUIRED=true uvicorn app.main:app --port 8099 &
   # E2E: новые ручки + пара существующих; для HMAC — подпись на Python
   pkill -f "port 8099"
   ```
4. **Рестарт прода** только после зелёного теста: `systemctl restart <svc>`.
5. **Verify:** `systemctl is-active`, `/health` 200, **существующая** ручка 200
   (регресс), **новая** — ожидаемо, `journalctl -u <svc> -n 5` — нет трейсбеков.
6. **Откат:** держи изменения в git/патче; если что — revert + restart.

Реальный E2E (сброс пароля): register → forgot(devToken) → reset(200) → повтор(400)
→ login старым(401) → login новым(200) → forgot неизвестного(200 без токена).

> Никогда не «правь на проде и рестартни вслепую». Temp-порт + verify спасают от даунтайма.

## C3. ⭐ Sizing аналитики/observability (не урони прод «инструментом»)

Прежде чем self-host PostHog/Sentry/ClickHouse — **проверь ресурсы:**
```bash
free -h    # RAM + SWAP
nproc      # ядра
df -h /    # диск
docker --version
```
- **PostHog hobby** = ClickHouse+Postgres+Redis+Kafka+воркеры → мин **4 ГБ**, комфортно 8–16.
- **Sentry self-host** ≈ **16 ГБ**.
- Маленький бокс (3.8 ГБ, `swap=0`, 2 ядра, где уже бэкенд+фронт) → `docker compose up`
  = **OOM и падение прода.** Не делай вслепую.

| Бокс | Аналитика |
|---|---|
| Маленький (<8 ГБ) | **Cloud free-tier** (PostHog Cloud / sentry.io) — 0 нагрузки, ключ сразу |
| Отдельный крупный | self-host ок (данные у себя) |
| Любой | базовый свой `/events` уже работает — не блокер |

## C4. Документация проекта (`docs/`) + контракт-handoff

`START-HERE`, `DESIGN`/`MOBILE-DESIGN-FULL` (токены+вёрстка 1:1), `API`/`openapi.json`,
`AUTH-*`, `SECURITY`+`APP-KEY-SETUP`, `ANALYTICS-*`, `POSTHOG-SELFHOST`, `INTEGRATIONS`,
`BACKEND-TODO`, `APP-READINESS`, `WHAT-ELSE-TODO`, `WORKLOG-YYYY-MM-DD`, этот playbook.

**`BACKEND-TODO.md`** — контракт-handoff app→server: чего не хватает на сервере, **с
телами запросов/ответов** (метод+путь, тело, коды, rate-limit, что клиент ждёт).
Сверяй с реальным кодом сервера, не с догадками; исправляй неточности.

**Координация двух агентов/репо:** клиент и сервер — разные репо/сессии; общий язык —
доки. Синхронные `BACKEND-TODO`/`APP-READINESS`/`APP-KEY-SETUP`/`openapi.json` держать в
обоих репо/`/opt`. Каждый пушит в свой репо; секреты между сессиями — вне чата.

## C5. Роли (кто что делает)

- **Приложение (client-репо):** UI 1:1, сеть, состояние экранов, клиент-безопасность,
  аналитика-клиент. Никаких провайдерских секретов.
- **Сервер (backend-репо):** генерация/логика/данные, провайдерские ключи в `.env`,
  auth-хэши, rate-limit, почта (ESP), серверная половина app-key (middleware),
  домен+сертификат. Чего не хватает — в `BACKEND-TODO.md`.
- **Владелец (разово, вне кода):** Apple Developer + Bundle ID, `Config.xcconfig` с
  реальными ключами, шрифт `.ttf`, домен+HTTPS, отзыв засвеченных токенов, аккаунты
  аналитики (PostHog Cloud/sentry.io).

## C6. Грабли → решения (клиент + сервер, из реального опыта)

| Симптом | Причина / решение |
|---|---|
| «Missing bundle ID» при install | свой Info.plist без стандартных bundle-ключей → добавить вручную |
| Требует AppIcon | `ASSETCATALOG_COMPILER_APPICON_NAME=""` пока нет иконки |
| Сложный SVG чёрный/кривой | растеризовать SVG→PNG через headless Chrome |
| `AsyncImage` не грузит | сервер отдаёт относительный путь → `AppConfig.mediaURL()` |
| Декод падает на enum-поле | перечислить ВСЕ значения enum (или устойчивый декод) + §C1 |
| `Text.foregroundStyle` не собирается на iOS16 | iOS17-only в Text-контексте → `.foregroundColor` |
| `.presentationBackground` не собирается | iOS 16.4+ → рисуй оверлей вручную для 16.0 |
| Видео прыгает/течёт | `AVPlayer` пересоздаётся на ре-рендер → создать 1 раз, observer снять в `deinit`/`.onDisappear` |
| `Config.xcconfig` попал в git | inline-комментарий в `.gitignore` сломал паттерн → комментарий отдельной строкой + `git rm --cached` |
| `//` в .xcconfig обрезается | экранировать как `/$()/` |
| self-signed не коннектится | переопределить server-trust в делегате (пиннинг) + ATS-исключение |
| Редактор красит «Cannot find type» | ложные SourceKit-диагностики → верить `xcodebuild` |
| Тело запроса «пустое» в middleware / хендлер висит | `BaseHTTPMiddleware` ломает re-read → **чистый ASGI** + replay тела |
| HMAC не сходится | канон не байт-в-байт (PATH без `/api`, регистр METHOD, ts не строкой) → сверить скриптом |
| `/uploads` под HMAC ломается | multipart подписан по пустому телу → исключить `/uploads` |
| Вебхуки отваливаются при `required=true` | своя подпись → исключить `/webhooks` |
| Прод лёг после «инструмента» | self-host тяжёлого стека на маленьком боксе → §C3, cloud free-tier |
| Мобилка не держит cookie | вернуть `session_token` в JSON, а не только Set-Cookie |
| Неверный client IP в лимитах | за nginx читать `X-Real-IP`/`X-Forwarded-For`, не peer |
| Даунтайм при правках | не тестировал до рестарта → §C2 temp-порт + verify |
| Письмо не уходит (нет ESP) | dev-токен в ответе (magic-link/reset); убрать при подключении почты |

## C7. Пошаговый план для НОВОГО проекта (клиент + сервер вместе)

**Подготовка**
1. **Снять спеку веба:** репо фронта, дизайн-токены, скриншоты моб-версии; контракт
   API (`openapi.json`/девтулзы) → `docs/API.md` + `docs/BACKEND-TODO.md`.

**Сервер (§B)**
2. Скелет `app/{main,config,redis_client,deps,cookies}`, `core/{security,rate_limit}`,
   `.env.example`, `.gitignore` (проверь, что `.env` игнорится), systemd-unit, nginx.
3. Auth: Argon2, `register/login` (token в JSON + cookie), сессии в Redis, dev-токен
   для magic-link/reset, стабильный enum провайдера.
4. Rate-limit по ip+email; домены-роутеры/сервисы под фичи; `/uploads`; `/events`.
5. App-key middleware (чистый ASGI, флаг `false`), канон 1:1 с клиентом; исключить
   `/uploads`+`/webhooks`; задокументировать в `APP-KEY-SETUP.md`.
6. **Деплой по §C2:** import-check → temp-порт E2E → restart → verify → journal.
7. **Аналитика-инфра по §C3:** проверить ресурсы; маленький бокс → cloud free-tier,
   свой `/events` как база.

**Клиент (§A)**
8. Скелет: `project.yml`, папки, `Config.example.xcconfig`, `.gitignore` (проверь!),
   `AppConfig`, `Info.plist`.
9. Дизайн-система: токены + шрифт → `Theme.swift`; растеризовать ассеты (§A10).
10. Сеть: `Models` (snake_case) + `APIClient` (эндпоинты по `API.md`) + `APIError`.
11. Безопасность: `APISecurity` (пиннинг+подпись), снять пин сервера, включить https.
12. Авторизация: `AuthManager` + экраны Auth (login/register/reset/verification) +
    роутер в `App.swift`.
13. Фичи 1:1: экран за экраном (`View` + `ViewModel`), сверяясь со скриншотами веба.
14. Аналитика: фасад + свой `/events` + `screen_view` везде + воронка + `PrivacyInfo`
    + `ConsentView`.
15. Прогон (§A11): build→install→launch→screenshot; сверить пиксели; dev-хуки.

**Финал**
16. Заполнить `docs/`, синхронизировать `BACKEND-TODO`/`APP-READINESS` в оба репо/`/opt`,
    запушить каждый в свой репо.
17. **К релизу:** домен+Let's Encrypt (убрать ATS, обновить/снять пин, `secure`
    cookie), App Attest (клиент+серверная проверка), IP-лимиты на все auth-ручки,
    почта (ESP) → убрать dev-токены, флип `APP_KEY_REQUIRED=true` после веба, privacy-
    labels, Apple Developer, иконка.

---

> Держась этого playbook, следующее приложение собирается по накатанной: тот же
> стек, та же структура, те же стандарты, та же дисциплина деплоя и sizing — меняются
> только токены дизайна, контракт API и набор фич. Всё остальное переиспользуется.
