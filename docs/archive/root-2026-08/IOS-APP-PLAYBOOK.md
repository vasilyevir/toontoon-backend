# Универсальный playbook: нативное iOS-приложение 1:1 с веб-продуктом

Переиспользуемый гайд для сборки следующих приложений по той же схеме, что и ARTEKI.
Подходит для любого продукта вида **«есть веб-версия (Next.js/React) + бэкенд-API,
нужно нативное iOS-приложение, повторяющее веб 1:1»**. Копируй этот файл в новый
репозиторий и иди по шагам. Всё проверено на реальном проекте.

> Философия: **приложение — тонкий клиент.** Вся логика/генерация/данные — на
> сервере. Приложение = дизайн 1:1 + сеть + состояние экранов. Секреты провайдеров
> НИКОГДА не в приложении. Дизайн/код — из репо, данные — с сервера, секреты — из
> локального git-ignored конфига.

---

## 0. Стек (что берём и почему)

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
(для растеризации ассетов через Playwright/Chrome), `curl`/`python3` (проверка API).

---

## 1. Структура репозитория (универсальная)

```
project.yml                     # XcodeGen: таргет, iOS16, Config.xcconfig, excludes
Config.example.xcconfig         # ШАБЛОН (в git) — плейсхолдеры, без значений
Config.xcconfig                 # РЕАЛЬНЫЕ значения (в .gitignore!) — URL, ключи, пин
.gitignore                      # Config.xcconfig, *.env, *.p8, DerivedData, .DS_Store …
IOS-APP-PLAYBOOK.md             # этот файл
docs/                           # вся дока (см. §12)
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
    ...
  Resources/
    Fonts/                      # .ttf (в git если лицензия позволяет, иначе локально)
    Images/                     # исходные SVG/PNG (excluded из таргета)
    Assets.xcassets             # сгенерированные imagesets (из растеризации)
    PrivacyInfo.xcprivacy       # privacy-manifest (обязателен для App Store)
```

---

## 2. XcodeGen (`project.yml`) — скелет

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

---

## 3. Конфигурация и секреты (критично для безопасности)

**Правило:** значения приходят из `Config.xcconfig` → Info.plist → `AppConfig`. В
коде — только плейсхолдеры/чтение. Провайдерские ключи (OpenAI/платёжки/…) —
ТОЛЬКО на сервере, в приложении их нет.

`AppConfig.swift` — единая точка:
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

**Секреты и токены:** GitHub-токены/ключи НИКОГДА не оставлять в чате/коде/репо;
если засветил — сразу отозвать. Реальные значения — только в git-ignored файлах.

---

## 4. Дизайн-система (Theme.swift) — фундамент «1:1 с вебом»

Сначала снимаем токены с веба (Tailwind config / CSS-переменные / девтулзы) и
переносим точь-в-точь:
```swift
enum ArtekiColor { static let bg = Color(hex: 0x0A0A0A); /* … все цвета из веба */ }
enum Spacing { static let s: CGFloat = 8; static let base: CGFloat = 16; /* … */ }
enum Radius  { static let s: CGFloat = 8; static let l: CGFloat = 16; static let pill: CGFloat = 100 }
enum AppFont { static func sans(_ s: CGFloat, _ w: Font.Weight = .regular) -> Font {
    .custom("Font Family Name", size: s).weight(w) } }
```
**Правило проекта:** ТОЛЬКО токены из Theme, никаких «своих» цветов/отступов —
тогда любой новый экран автоматически в едином стиле.

Шрифт: положить `.ttf` в `Resources/Fonts`, прописать в `Info.plist` `UIAppFonts`,
использовать точное имя семейства (`.custom("…")`).

---

## 5. Слой сети (APIClient)

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
   редирект в `URLSessionTaskDelegate`), храним значение в Keychain, шлём как
   `Cookie:` заголовок.
2. **Токен в JSON** (email+пароль): `{user, session_token}` → токен в Keychain →
   `Cookie`/`Authorization` на каждый запрос.

---

## 6. Безопасность канала (APISecurity) — «связь через ключи»

Всё в одном файле `Networking/APISecurity.swift`:
1. **TLS cert pinning** — сверяем SPKI сертификата сервера с пином из конфига
   (`urlSession(_:didReceive:)` → server-trust challenge). Доверяем ТОЛЬКО нашему
   серверу; работает даже с self-signed (без домена), MITM невозможен.
   Пин снять: `openssl s_client -connect host:443 | openssl x509 -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64`.
2. **App-key заголовок** — дешёвый барьер от чужих ботов.
3. **HMAC-подпись** каждого запроса: `sig = HMAC_SHA256(secret, "METHOD\nPATH\nTS\nsha256(body)")`,
   шлём `X-App-Key/X-Timestamp/X-Signature`. Сервер собирает канон 1:1 и сверяет.
4. **Запрет http в релизе** (`precondition(scheme=="https")`).

> Честно: секрет зашит в бинарник → извлекаем реверсом. Это «поднять планку», не
> абсолют. Настоящая подлинность клиента = **Apple App Attest** (к релизу).

Self-signed по IP: держи ATS-исключение в Info.plist для этого хоста; при
переопределении server-trust в делегате self-signed принимается. К релизу: домен +
Let's Encrypt → убрать ATS, обновить/снять пин, `secure=true` cookie.

---

## 7. Авторизация (полный набор экранов)

Единый набор: `AuthComponents` (карта/поле/кнопка/close — 1:1 с вебом) →
переиспользуется в `LoginView`/`RegisterView`/`VerificationView`/`ForgotPasswordView`.

- **email+пароль**: register→`{user, token}`, login→`{user, token}`.
- **Сброс пароля** (работает и без почты в dev): `forgot-password` возвращает
  reset-токен прямо в ответе → клиент подставляет; `reset-password` меняет хэш.
- **Верификация email**: экран-заглушка, пока нет почты на сервере (любой ввод
  пропускает); заменить на реальный вызов, когда сервер подключит ESP.
- `AuthManager: ObservableObject` держит `user`, `isAuthenticated`, ошибки; `RootView`
  роутит: `Landing → Login/Register → (Verification) → Main`.
- iOS 16: `Text.foregroundStyle` — **iOS 17 only**; в контексте Text (конкатенация
  `+`, `prompt:`) используй `.foregroundColor`.

---

## 8. Аналитика (глубокая + проходит App Review)

**Что требует Apple:** без сквозного трекинга между приложениями → **ATT-баннер не
нужен**; обязателен `PrivacyInfo.xcprivacy` + Privacy Nutrition Labels; никакого
fingerprinting; согласие (GDPR) до сбора. Провайдеры-рекламные (Facebook) — не берём.

**Архитектура (SDK-агностик, чтобы менять провайдера без переписывания):**
- `protocol AnalyticsClient { track/setUserProperties/resetIdentity }`.
- `Analytics` — фасад: буферизует до согласия, добавляет конверт (`session_id`,
  `app_version`, анонимный `anon_id`), гейтит через `ConsentManager`.
- `ServerAnalyticsClient` — свой `/events` (дефолт, 0 третьих лиц, идеально для ревью).
- `PostHogClient`/`TelemetryDeckClient` — адаптеры под `#if canImport(...)`
  (проект собирается без пакета; включение = SPM-пакет + ключ в Config).
- `CompositeAnalyticsClient` — шлёт событие сразу в несколько бэкендов.
- `AnalyticsEvent` — типобезопасный каталог (snake_case, `object_action`, **без PII
  и содержимого промптов**).

**Что трекать (полная воронка поведения):**
- `screen_view` со всех экранов (`.trackScreen("name")` в `.onAppear`) + `app_opened`.
- Ключевая воронка продукта (пример для генератора): `category_selected →
  item_selected → …_confirmed → …_started → …_succeeded(+duration_ms)/failed`,
  плюс экономика (`spent`, `insufficient_balance`), ретеншн по `anon_id`.
- Auth: register/login success/failed, logout, password_reset_*, profile-изменения.

**Рекомендация провайдера:** свой `/events` (база) + **PostHog** (self-host →
данные у нас; воронки/retention/session replay) для глубины. Минимум усилий +
гарантия ревью — **TelemetryDeck** (privacy-first).

---

## 9. Фичи (MVVM) и типовой «chat-engine»

Каждый экран: `FeatureView` + `FeatureViewModel: ObservableObject` (`@MainActor`,
`@Published`). Вью тонкая, вся логика/сеть — во ViewModel.

Если продукт — диалоговый (генерация через чат), удобен паттерн:
`messages: [Bubble]` (типы: userText/userImages/aiText/aiImage/aiVideo/error) +
`flow: enum` (интерактивная карточка в конце: pick → answer → confirm → generating)
+ локальные `sessions` (история). Видео: создавай `AVPlayer` ОДИН раз (`@State`),
loop через один observer, **снимай observer в `.onDisappear`** (иначе утечка).

---

## 10. Ассеты (pipeline растеризации)

Xcode плохо рендерит сложные SVG (градиенты, чёрные подложки). Поэтому:
1. Забираем официальные ассеты веба (SVG/PNG/webp/лого).
2. **SVG → PNG** через headless Chrome (Playwright, канал system Chrome) — точный
   рендер как в браузере.
3. **webp → png** через `sips`.
4. Складываем как **single-scale universal imagesets** в `Assets.xcassets` (имена
   без расширения = как в коде: `icon-x`, `logo`, …).
Скрипты растеризации держать в scratchpad/tools, перегенерировать при смене ассетов.

---

## 11. Build / Run / Verify loop (без Xcode GUI)

```bash
xcodegen generate                                   # 1. сгенерить .xcodeproj из project.yml
xcodebuild -project AppName.xcodeproj -scheme AppName \
  -configuration Debug -sdk iphonesimulator \
  -destination 'platform=iOS Simulator,name=iPhone 17' \
  -derivedDataPath ./DD build                        # 2. собрать
xcrun simctl boot "iPhone 17"; open -a Simulator     # 3. поднять симулятор
xcrun simctl install "iPhone 17" ./DD/Build/Products/Debug-iphonesimulator/AppName.app
xcrun simctl launch  "iPhone 17" com.company.appname # 4. запустить
xcrun simctl io "iPhone 17" screenshot out.png       # 5. скриншот → визуальная проверка
```
**Dev-хуки** (только симулятор) через `SIMCTL_CHILD_*` env: сид сессии
(`APP_DEV_SESSION=<cookie>`), прыжок на экран (`APP_DEV_SCREEN=profile|…`) — чтобы
скриншотить состояния без тапов. Читаем в `AuthManager.bootstrap()` / роутере.

Получить сессию для сида: `POST /auth/…` → достать cookie/токен из ответа.

> SourceKit в редакторе часто даёт ЛОЖНЫЕ «Cannot find type X» (whole-module) —
> ориентируйся на реальный вывод `xcodebuild`, а не на диагностику редактора.

---

## 12. Документация проекта (держать в `docs/`)

`START-HERE` (с чего начать), `DESIGN`/`MOBILE-DESIGN-FULL` (токены+вёрстка 1:1),
`API`/`openapi.json` (контракт бэка), `AUTH-*` (экраны/логика входа),
`SECURITY`+`APP-KEY-SETUP` (пиннинг/подпись/что сделать серверу), `ANALYTICS-*`
(аудит+каталог событий+выбор провайдера), `INTEGRATIONS` (как включить SDK),
`BACKEND-TODO` (чего не хватает на сервере — с телами запросов/ответов),
`APP-READINESS` (готовность по слоям), `WHAT-ELSE-TODO` (стандарты + бэклог),
`WORKLOG-YYYY-MM-DD` (что сделано за день).

---

## 13. Пошаговый план для НОВОГО проекта

1. **Забрать спеку и ассеты веба** (репо фронта, дизайн-токены, скриншоты моб-версии).
   Снять контракт API (`openapi.json`/девтулзы) → `docs/API.md`.
2. **Скелет:** `project.yml`, структура папок, `Config.example.xcconfig`, `.gitignore`
   (проверь, что `Config.xcconfig` реально игнорится!), `AppConfig`, `Info.plist`.
3. **Дизайн-система:** перенести токены и шрифт → `Theme.swift`. Растеризовать ассеты.
4. **Сеть:** `Models` (snake_case) + `APIClient` (эндпоинты по `API.md`) + `APIError`.
5. **Безопасность:** `APISecurity` (пиннинг+подпись), снять пин сервера, включить https.
6. **Авторизация:** `AuthManager` + экраны Auth (login/register/reset/verification) +
   роутер в `App.swift`.
7. **Фичи 1:1:** экран за экраном (`View` + `ViewModel`), сверяясь со скриншотами веба.
8. **Аналитика:** фасад + свой `/events` + `screen_view` везде + воронка продукта +
   `PrivacyInfo.xcprivacy` + `ConsentView`.
9. **Прогон:** build→install→launch→screenshot; сверить пиксели; dev-хуки для состояний.
10. **Дока + пуш:** заполнить `docs/`, `BACKEND-TODO` для сервера, запушить.
11. **К релизу:** домен+Let's Encrypt, App Attest, privacy-labels, Apple Developer,
    иконка, реальная верификация email (когда сервер даст почту).

---

## 14. Частые грабли → решения (из реального опыта)

| Симптом | Причина / решение |
|---|---|
| «Missing bundle ID» при install | свой Info.plist без стандартных bundle-ключей → добавить вручную |
| Требует AppIcon | `ASSETCATALOG_COMPILER_APPICON_NAME=""` пока нет иконки |
| Сложный SVG чёрный/кривой | растеризовать SVG→PNG через headless Chrome |
| `AsyncImage` не грузит | сервер отдаёт относительный путь → `AppConfig.mediaURL()` |
| Декод падает на enum-поле | перечислить ВСЕ значения enum (или устойчивый декод) |
| `Text.foregroundStyle` не собирается на iOS16 | это iOS17-only в Text-контексте → `.foregroundColor` |
| Видео прыгает/течёт | `AVPlayer` пересоздаётся на ре-рендер → создать 1 раз в `@State`, observer снять в `.onDisappear` |
| `Config.xcconfig` попал в git | inline-комментарий в `.gitignore` сломал паттерн → комментарий на отдельной строке + `git rm --cached` |
| self-signed не коннектится | переопределить server-trust в делегате (пиннинг) + ATS-исключение на хост |
| Редактор красит «Cannot find type» | ложные SourceKit-диагностики → верить `xcodebuild` |
| `//` в .xcconfig обрезается | экранировать как `/$()/` |

---

## 15. Что делает КТО (границы ответственности)

- **Приложение (этот репо):** UI 1:1, сеть, состояние экранов, безопасность канала
  (клиентская половина), аналитика-клиент. Никаких провайдерских секретов.
- **Сервер:** вся генерация/логика/данные, провайдерские ключи в `.env`, auth-хэши,
  rate-limit, почта (ESP) для верификации/сброса, серверная половина app-key
  (middleware), домен+сертификат. Чего не хватает — вести в `BACKEND-TODO.md`.
- **Владелец (разово, вне кода):** Apple Developer + Bundle ID, `Config.xcconfig` с
  реальными ключами, шрифт `.ttf`, домен+HTTPS, отзыв засвеченных токенов.

---

> Держась этого playbook, следующее приложение собирается по накатанной: тот же
> стек, та же структура, те же стандарты — меняются только токены дизайна, контракт
> API и набор фич. Всё остальное переиспользуется.
