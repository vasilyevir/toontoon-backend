# ARTEKI iOS — все интеграции и как их подключать

Единая точка: что подключается, какими ключами, и что уже работает без ключей.
Все значения — в `Config.xcconfig` (git-ignored) → Info.plist → `AppConfig.swift`.
В коде секретов нет. Обновляется при добавлении новых интеграций.

## Как приложение тянет данные с сервера
- Весь обмен — через `Networking/APIClient.swift` на **`ARTEKI_BASE_URL`**
  (`https://193.149.190.155`, `/api/*`), по HTTPS с **пиннингом** (`APISecurity`).
- Контракт всех ручек — `API.md` + `openapi.json`. Модели — `Models.swift`.
- Данные, которые тянет апка: каталог тайлов, баланс TEKI, генерации (image/
  video с поллингом), профиль, транзакции; отправляет: регистрацию/вход,
  генерации, загрузку фото, **аналитические события** (`/api/events`).

## Таблица ключей конфигурации (Config.xcconfig)
| Ключ | Что включает | Обязателен? | Где взять |
|---|---|---|---|
| `ARTEKI_BASE_URL` | Адрес сервера | да (есть дефолт) | наш сервер |
| `ARTEKI_PINNED_SPKI` | Пиннинг TLS-сертификата | рекоменд. | `SECURITY.md` (уже вписан) |
| `ARTEKI_APP_KEY` | Заголовок app-key (барьер от ботов) | опц. | придумать, продублировать на сервере |
| `ARTEKI_APP_SECRET` | Подпись запросов (HMAC) | опц. | придумать, продублировать на сервере |
| `ARTEKI_TELEMETRYDECK_APP_ID` | Продуктовая аналитика TelemetryDeck | опц. | telemetrydeck.com |
| `ARTEKI_SENTRY_DSN` | Мониторинг крашей/ошибок | опц. | sentry.io |

## Статус интеграций
| Интеграция | Статус | Активация |
|---|---|---|
| **Backend API (HTTPS+пиннинг)** | ✅ работает | ничего (дефолтный BASE_URL + пин) |
| **Auth email+пароль** | ✅ работает (сервер задеплоен) | ничего |
| **Своя аналитика `/api/events`** | ✅ работает сейчас | ничего — клиент по умолчанию |
| **App-key / подпись запросов** | 🟡 клиент готов | задать `ARTEKI_APP_KEY/SECRET` + включить на сервере (см. SECURITY.md §3) |
| **TelemetryDeck** | 🟡 адаптер готов | ① SPM `github.com/TelemetryDeck/SwiftSDK` ② `ARTEKI_TELEMETRYDECK_APP_ID` |
| **Sentry (краши)** | 🟡 адаптер готов | ① SPM `github.com/getsentry/sentry-cocoa` (продукт «Sentry») ② `ARTEKI_SENTRY_DSN` |

> 🟡 = код в репозитории готов и обёрнут в `#if canImport(...)`, проект
> собирается и без пакета. Подключение = добавить SPM-пакет + вставить ключ.

## Пошаговая активация (когда будешь готов)
**TelemetryDeck:** Xcode → Add Packages → `https://github.com/TelemetryDeck/SwiftSDK`
→ создать app на telemetrydeck.com → `ARTEKI_TELEMETRYDECK_APP_ID = <id>`.
Приложение само начнёт слать события в TelemetryDeck вместо `/api/events`.

**Sentry:** Xcode → Add Packages → `https://github.com/getsentry/sentry-cocoa`
(добавить продукт «Sentry») → создать проект на sentry.io → `ARTEKI_SENTRY_DSN =`
(экранировать `//` как `/$()/`). Краши/ошибки поедут в Sentry.

**App-key/подпись:** задать `ARTEKI_APP_KEY` и `ARTEKI_APP_SECRET`, продублировать
на сервере и включить middleware (`SECURITY.md §3`, по умолчанию выключено).

## Приватность (общая для всех аналитик)
- Ничего не отправляется без согласия (`ConsentManager`, экран `ConsentView`).
- Без PII/содержимого промптов. `PrivacyInfo.xcprivacy` — tracking=false.
- ATT-баннер не нужен (first-party). Nutrition Labels держать синхронно.

## Куда смотреть дальше
Полный список задач и стандартов — `WHAT-ELSE-TODO.md`.
Аналитика подробно — `ANALYTICS-AUDIT.md`. Безопасность — `SECURITY.md`.
