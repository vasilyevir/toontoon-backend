# ARTEKI iOS — Аналитика: полный аудит и план внедрения

Большой аудит: что можно собирать, **что пройдёт ревью Apple**, какие SDK взять,
какие события трекать именно в ARTEKI, как внедрить и что запрещено.

> **Рамка (важно):** цель — легальная **продуктовая аналитика с согласием
> пользователя**, которая проходит App Store и не нарушает GDPR/CCPA. Обход ATT,
> скрытый трекинг и фингерпринтинг Apple **банит** (риск снятия из App Store +
> штрафы регуляторов). Ниже — как собрать максимум данных легально.

---

## 1. Что хотим понять о поведении (цели аналитики ARTEKI)
- **Активация:** доходит ли новый юзер от лендинга до первой генерации.
- **Воронки генерации:** тайл → вопросы → подтверждение → результат; где отваливаются.
- **Экономика TEKI:** сколько тратят, на что, упираются ли в баланс (402).
- **Успех/ошибки генерации:** доля success/fail, тайминги (особенно видео).
- **Ретеншн:** возвраты D1/D7/D30, частота, любимые тайлы/категории.
- **Технические:** краши, зависания, ошибки API (429/402/503), скорость экранов.
- **Монетизация (потом):** конверсия в оплату/Upgrade.

---

## 2. Правила Apple, которые определяют «что пройдёт ревью»

### 2.1 App Tracking Transparency (ATT)
- **Tracking по Apple** = связывание данных пользователя/устройства из твоего
  приложения с данными **других компаний** (их апп/сайтов) ради рекламы/атрибуции,
  ИЛИ передача данных data-брокерам, ИЛИ использование **IDFA**.
- Такое требует **prompt ATT** (`ATTrackingManager`) — и большинство юзеров жмёт «No».
- **First-party продуктовая аналитика** (события внутри своего приложения, не
  сливаемые третьим лицам ради рекламы) **НЕ требует ATT-prompt**. Можно
  использовать **IDFV** (одинаков для апп одного вендора), но нельзя комбинировать
  его с чужими данными для меж-аппового трекинга.
- **Вывод для ARTEKI:** делаем first-party аналитику → **ATT-prompt не нужен**,
  если не подключаем рекламные/атрибуционные SDK и не шлём IDFA.

### 2.2 Privacy Manifest (`PrivacyInfo.xcprivacy`) — ОБЯЗАТЕЛЕН
- С **1 мая 2024** приложения без корректного privacy-manifest **отклоняются**.
- Содержит:
  - `NSPrivacyCollectedDataTypes` — какие типы данных собираем и зачем.
  - `NSPrivacyTrackingDomains` — домены, участвующие в трекинге (если tracking=true).
  - `NSPrivacyTracking` (Bool).
  - `NSPrivacyAccessedAPITypes` — **Required Reason APIs** (UserDefaults, system
    boot time, file timestamp, disk space) + коды причин.
- Сторонние SDK обязаны поставлять **свой** privacy-manifest и **подпись SDK**
  (список «signed SDKs» Apple). Analytics-SDK почти все в этом списке.

### 2.3 Privacy Nutrition Labels (карточка App Store)
- В App Store Connect декларируешь: какие данные собираешь, связаны ли с
  личностью, используются ли для трекинга. Должно **совпадать** с фактическим сбором.

### 2.4 Запрет фингерпринтинга
- Apple **прямо запрещает** идентификацию устройства через отпечаток (набор
  сигналов) для обхода ATT. Это частая причина реджекта/бана. **Не делаем.**

### 2.5 GDPR / CCPA и тренд 2025
- Для EU нужна **юридическая основа** (обычно согласие) на аналитику; право на
  отзыв и удаление.
- Тренд ужесточения: французский CNIL в 2025 занял позицию, что согласие нужно
  **даже для first-party** трекинга (для рекламных целей). Для чисто продуктовой
  аналитики — минимизация + прозрачность; безопаснее показать **выбор** (opt-in/out).

### 2.6 Прочее
- **Минимизация данных**: собирать только то, что реально используешь.
- **Дети/чувствительное**: не собирать спец-категории; у ARTEKI аудитория 40–60,
  но всё равно — без здоровья/политики/точной геолокации.
- **Контент пользователя** (тексты промптов, фото) — это чувствительно: либо не
  логировать содержимое, либо только с явного согласия и без персональной привязки.

---

## 3. Что можно собирать БЕЗ ATT (first-party) vs что требует ATT

| Данные | Без ATT (можно) | Требует ATT / нельзя |
|---|---|---|
| События в апке (экран, клик, генерация) | ✅ | — |
| Свойства события (тип тайла, стоимость, success/fail) | ✅ | — |
| Анонимный/псевдонимный id установки (свой UUID / IDFV) | ✅ | — |
| Модель устройства, iOS-версия, локаль, версия апки | ✅ | — |
| Краши/производительность | ✅ | — |
| Приблизительная гео по стране (из IP на сервере) | ✅ (грубо) | точная геолокация — отдельное разрешение |
| **IDFA / рекламная атрибуция** | ❌ | ✅ только с ATT |
| Слияние с данными других компаний ради рекламы | ❌ | ✅ только с ATT (лучше не надо) |
| Фингерпринт устройства | ❌ запрещено | ❌ запрещено |
| Содержимое промптов/фото с привязкой к личности | ❌ | только с явным согласием, лучше анонимно |

---

## 4. Сравнение аналитических SDK (для нашего кейса)

| SDK | Privacy-first | Нужен ATT? | Consent-баннер | Self-host | Session replay | Craш/perf | Feature flags | Цена | Примечание |
|---|---|---|---|---|---|---|---|---|---|
| **TelemetryDeck** | ★★★ (двойной хеш, без PII) | Нет | **Не нужен** | (managed, EU) | Нет | базово | Нет | есть free tier | Заточен под Apple, минимальный privacy-manifest, «passes Apple» проще всего |
| **PostHog** | ★★ (гибко, можно self-host) | Нет (product analytics) | желателен | ✅ (свой сервер) | ✅ | ✅ (errors) | ✅ | free tier / self-host бесплатно | Много фич; для EU лучше self-host/EU-cloud |
| **Aptabase** | ★★★ (open-source, анонимно) | Нет | обычно не нужен | ✅ | Нет | базово | Нет | open-source | Лёгкий, приватный, простой |
| **Firebase / GA4** | ★ | Нет (без рекламных модулей) | да (EU) | Нет (Google) | Нет | ✅ (Crashlytics) | ✅ | free | Данные у Google, для EU спорно, тяжелее комплаенс |
| **Amplitude** | ★★ | Нет | да | Нет (EU-резиденция есть) | частично | Нет | ✅ | free tier | Мощные воронки/ретеншн; нужен consent-менеджмент |
| **Mixpanel** | ★★ | Нет | да | Нет | Нет | Нет | ограниченно | free tier | Хорош для продуктовых воронок |
| **Sentry** | ★★ | Нет | обычно нет | ✅ | нет (replay для web) | ★★★ (краши/perf) | Нет | free tier | Лучший для стабильности/ошибок, **в дополнение** к продуктовой аналитике |

## 5. Рекомендация для ARTEKI
**Двухслойно, приватно, «passes Apple» без ATT-баннера:**
1. **Продуктовая аналитика:** **TelemetryDeck** (проще всего пройти ревью, без
   consent-баннера, без PII) — или **PostHog self-host**, если нужны воронки/
   session replay/feature flags и полный контроль данных на своём сервере
   (`193.149.190.155`).
2. **Стабильность:** **Sentry** для крашей/ошибок/производительности.
3. Никаких рекламных/атрибуционных SDK сейчас → **ATT-prompt не нужен**.

**Выбор по цели:** нужен максимум данных и свой сервер → **PostHog (self-host) +
Sentry**. Нужен максимум приватности и минимум возни с ревью → **TelemetryDeck +
Sentry**.

---

## 6. Каталог событий ARTEKI (собираем максимум легально)
Именование: `snake_case`, `object_action`. У каждого события — общие свойства
(см. §7) + специфичные.

### Онбординг / лендинг
- `app_opened` {is_first_launch}
- `landing_viewed`
- `landing_cta_tapped` {cta: get_started|sign_in}
- `onboarding_step_viewed` {step: 1..3, key: prompt|templates|history}
- `onboarding_completed` / `onboarding_skipped` {at_step}

### Авторизация
- `auth_screen_viewed` {screen: login|register|verification}
- `register_submitted` / `register_succeeded` / `register_failed` {reason: email_taken|weak_password|rate_limited|network}
- `login_submitted` / `login_succeeded` / `login_failed` {reason: invalid_credentials|rate_limited|network}
- `verification_continued` (stub)
- `logout` / `account_deleted`

### Каталог / выбор
- `home_viewed`
- `category_selected` {category: image|postcard|announcement|video}
- `tile_selected` {tile_id, category, cost, needs_photo}
- `freeform_prompt_started` {mode: image|video}
- `chat_message_sent` {length_bucket} (⚠️ БЕЗ текста промпта по умолчанию)

### Воронка вопросов
- `question_viewed` {tile_id, question_id, index, total}
- `question_answered` {tile_id, question_id, option_kind: preset|custom}
- `photo_upload_started` / `photo_upload_succeeded` / `photo_upload_failed`

### Генерация (ключевое)
- `generation_confirmed` {type: image|video, tile_id, cost}
- `generation_started` {type, tile_id, is_video}
- `generation_succeeded` {type, tile_id, duration_ms, is_video}
- `generation_failed` {type, tile_id, reason: insufficient_teki_402|rate_limit_429|generator_503|timeout|network, duration_ms}
- `generation_retry_tapped` {type, tile_id}
- `video_poll_tick` {gen_id, elapsed_s} (агрегировать, не спамить)

### Результат / шеринг / галерея
- `result_viewed` {type, tile_id}
- `result_zoomed`
- `result_downloaded` {type}
- `result_shared` {type}
- `history_opened` / `history_item_opened`
- `new_chat_started`

### Кошелёк / монетизация
- `balance_viewed` {balance}
- `teki_spent` {amount, reason: image_generate|video_generate, balance_after}
- `insufficient_teki` {needed, balance}
- `upgrade_tapped` (когда появится оплата)

### Профиль
- `profile_opened`, `name_changed`, `avatar_changed`

### Технические / стабильность
- `api_error` {endpoint, status, code}
- `screen_view` {screen} (авто по появлению экрана)
- `app_backgrounded` / `app_foregrounded`
- краши/perf — через Sentry автоматически.

---

## 7. Общие свойства и модель данных
**User/device properties (без PII):** anon_id (свой UUID в Keychain), app_version,
build, os_version, device_model, locale, is_authenticated, provider (email),
teki_balance_bucket, install_date, days_since_install.
**Event envelope:** event_name, timestamp, session_id, anon_id, screen, properties{}.
**Сессия:** новый session_id при `foreground` после ≥30 мин фона.
**Ретеншн/когорты:** по install_date + anon_id (без личной привязки).

Метрики, которые из этого получим: activation rate (register→first
generation), funnel completion по шагам, generation success rate, среднее время
видео, TEKI-экономика, D1/D7/D30 retention, топ тайлов/категорий, error rate по эндпоинтам.

---

## 8. Согласие и приватность в приложении
- **Consent-gate:** при первом запуске — короткий экран/тумблер «Помогать
  улучшать приложение (анонимная аналитика)». По умолчанию для EU — **opt-in**;
  вне EU можно opt-out. Хранить выбор, уважать отзыв.
- **Не показывать ATT-prompt** (он не нужен для first-party) — иначе лишний отказ.
- **Privacy Manifest** заполнить: собираемые типы (Product Interaction,
  Crash Data, Performance Data, возможно Device ID=анонимный), tracking=false,
  Required Reason APIs (UserDefaults C56D.1 и т.п.).
- **Nutrition Labels** в App Store Connect — синхронно с манифестом.
- **Не логировать** содержимое промптов/фото и email в аналитике по умолчанию.
- Дать в настройках: «Выключить аналитику» и «Удалить мои данные».

---

## 9. Архитектура внедрения (в этом репо)
SDK-независимый слой (готов в коде):
- `Analytics/AnalyticsEvent.swift` — типобезопасный перечень событий + свойства.
- `Analytics/Analytics.swift` — протокол `AnalyticsClient` + фасад `Analytics`
  (буферизует, гейтит по согласию).
- `Analytics/ConsentManager.swift` — согласие (Keychain/UserDefaults), opt-in/out.
- `Analytics/ConsoleAnalyticsClient.swift` — dev-реализация (печатает в консоль).
- Подключение реального SDK — реализовать `AnalyticsClient` (напр.
  `TelemetryDeckClient` или `PostHogClient`) и передать в `Analytics.configure`.
- `Resources/PrivacyInfo.xcprivacy` — шаблон privacy-manifest.

Вызовы: `Analytics.track(.generationSucceeded(type: .video, tileId: id, durationMs: t))`.
Экраны шлют `screen_view` в `.onAppear`.

---

## 10. Чего НЕ делаем (чёрный список — иначе реджект/штраф)
- ❌ Фингерпринтинг устройства для идентификации/обхода ATT.
- ❌ IDFA / рекламная атрибуция без ATT-prompt.
- ❌ Слияние данных с третьими компаниями ради рекламы.
- ❌ Логирование содержимого промптов/фото/паролей/почты в аналитику.
- ❌ Сбор точной геолокации без нужды и разрешения.
- ❌ Скрытый сбор без отражения в Nutrition Labels/манифесте.
- ❌ Игнор отзыва согласия.

---

## 11. План внедрения (шаги) + чек-лист App Store
1. Выбрать SDK (§5) — решение за тобой (вопросы в §13).
2. Добавить SDK-пакет (SPM), реализовать `AnalyticsClient`.
3. Расставить `Analytics.track(...)` по каталогу §6 (начать с воронки генерации).
4. Consent-экран + гейт (§8).
5. Заполнить `PrivacyInfo.xcprivacy` + Nutrition Labels.
6. Проверить: без ATT-баннера, tracking=false, домены SDK (если есть) в манифесте.
7. QA: события приходят, согласие уважается, отключение работает.

**Чек-лист ревью:** privacy manifest есть ✅ · nutrition labels совпадают ✅ ·
ATT только если реально tracking ✅ · SDK из списка подписанных ✅ · нет
фингерпринтинга ✅ · есть отключение аналитики ✅.

---

## 12. Оценка объёма
- Слой аналитики (готов в репо): 0 (сделано).
- Интеграция выбранного SDK + расстановка событий: ~1–1.5 дня.
- Consent-экран + privacy-manifest + nutrition labels: ~0.5 дня.

## 13. Решения от тебя
1. **SDK:** TelemetryDeck (проще ревью, приватно) **или** PostHog self-host
   (максимум данных на своём сервере) — что берём? + Sentry для крашей — да?
2. **Consent:** показывать экран согласия (рекомендую для EU) или сразу включать
   анонимно с opt-out в настройках?
3. **Содержимое промптов:** трекать текст промптов (для продуктовых инсайтов,
   но чувствительно) или только метаданные (длина, тип)? Рекомендую метаданные.
4. Self-host PostHog на `193.149.190.155` — если выбираем PostHog?

## 14. ВНЕДРЕНО: TelemetryDeck (осталось 2 шага)
Выбран TelemetryDeck (приватный, без ATT-баннера, легче всего проходит ревью).
В коде уже всё готово и SDK-независимо:
- `Analytics/TelemetryDeckClient.swift` — адаптер (обёрнут в `#if canImport(TelemetryDeck)`,
  проект собирается и без пакета — тогда работает консольная заглушка).
- `ArtekiApp.init` авто-выбирает TelemetryDeck, если пакет добавлен И задан App ID.
- Config: `ARTEKI_TELEMETRYDECK_APP_ID` (Info.plist ← Config.xcconfig).

**Шаг 1 — добавить пакет:** Xcode → File → Add Package Dependencies →
`https://github.com/TelemetryDeck/SwiftSDK` → добавить в таргет.
**Шаг 2 — App ID:** зарегистрироваться на telemetrydeck.com, создать app,
скопировать App ID в `Config.xcconfig`:
```
ARTEKI_TELEMETRYDECK_APP_ID = <твой-app-id>
```
Готово: события (запуск, вход/регистрация, воронка генерации, трата TEKI)
пойдут в TelemetryDeck после согласия пользователя. ATT-баннер не нужен.
Не забудь Nutrition Labels в App Store Connect синхронно с `PrivacyInfo.xcprivacy`.

## Источники (ресёрч)
- Apple — App Tracking Transparency: https://developer.apple.com/documentation/apptrackingtransparency
- Apple — User Privacy and Data Use: https://developer.apple.com/app-store/user-privacy-and-data-use/
- Apple — Privacy manifest files: https://developer.apple.com/documentation/bundleresources/privacy-manifest-files
- Bitrise — Enforcement of Apple Privacy Manifest (May 1, 2024): https://bitrise.io/blog/post/enforcement-of-apple-privacy-manifest-starting-from-may-1-2024
- TelemetryDeck (Swift, privacy-first): https://telemetrydeck.com/platforms/swift/
- PostHog — GDPR-compliant analytics tools: https://posthog.com/blog/best-gdpr-compliant-analytics-tools
