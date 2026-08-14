# TOONTOON iOS — START HERE (с чего начать)

Стартовый документ. Прочитай его первым. Здесь: что уже сделано, **что делать
по шагам**, как приложение тянет всё из репозитория и с сервера, и указатель на
остальные доки. Всё необходимое для приложения лежит в этом репозитории
(`intersson-sir/toontoon-ios`), секреты — нет (см. §5).

---

## 1. TL;DR — что это и как устроено

- **Что:** нативное iOS-приложение (SwiftUI, min iOS 16) для TOONTOON — генератор
  картинок/открыток/видео. Дизайн **1:1 с МОБИЛЬНОЙ (compact) версией веба**.
- **Отдельный кодовый репозиторий.** Приложение НЕ содержит логики генерации —
  оно обращается к бэкенду `https://193.149.190.155/api` за всеми данными.
- **Ключи провайдеров (OpenAI/kie.ai/Boostyfi/…) живут ТОЛЬКО на сервере.**
  В приложении их нет и не будет.
- **Соединение защищено:** HTTPS + пиннинг сертификата + app-key + HMAC-подпись
  запросов (см. `SECURITY.md`). Домена пока нет — работаем по IP.

Поток данных: **App (SwiftUI) → APIClient (пиннинг+подпись) → /api (FastAPI) →
Redis + провайдеры.** Всё, что рисуется на экране, приходит с сервера.

---

## 2. Что сделано на сегодня (готово, переиспользовать — не переписывать)

| Блок | Файлы | Статус |
|---|---|---|
| Дизайн-система (токены) | `DesignSystem/Theme.swift` | ✅ |
| Сеть + модели | `Networking/APIClient.swift`, `Models.swift` | ✅ |
| Безопасность (пиннинг/подпись) | `Networking/APISecurity.swift`, `App/AppConfig.swift` | ✅ |
| Авторизация email+пароль | `Auth/*`, `Features/Auth/*` | ✅ |
| Верификация email (заглушка) | `Features/Auth/VerificationView.swift` | ✅ (stub до домена) |
| Экран Generate (чат-ядро) | `Features/Generate/*` | ✅ |
| **Профиль** (аватар/имя/баланс/выход/удаление) | `Features/Profile/ProfileView.swift` | ✅ **сегодня** |
| Аналитика (согласие + свой /events) | `Analytics/*` | ✅ |
| Ассеты (SVG/PNG/лого) | `Resources/Images/*` | ✅ |
| Бэкенд: `/auth/register\|login`, `/events`, `/auth/profile`, `/transactions` | сервер | ✅ |

Полный список оставшегося — `WHAT-ELSE-TODO.md`.

---

## 3. ЧТО ДЕЛАТЬ СПЕРВА (пошагово)

### Шаг 0. Забрать код
```bash
git clone https://github.com/intersson-sir/toontoon-ios.git
cd toontoon-ios
```

### Шаг 1. Создать Xcode-проект (нужен Mac + Xcode 15+)
1. Xcode → **New Project → App**, SwiftUI, **min iOS 16.0**, Bundle ID
   (напр. `com.toontoon.app`).
2. Удали автосозданные `ContentView.swift`/`App.swift` — используем свои из
   `ToontoonApp/`.
3. Перетащи папку **`ToontoonApp/`** в проект (Create groups, добавить в таргет).

### Шаг 2. Секреты — `Config.xcconfig` (НЕ в git)
1. Скопируй пример: `cp Config.example.xcconfig Config.xcconfig`
2. Заполни значения (`APP_KEY`, `APP_SECRET`, при желании TelemetryDeck/Sentry).
   `APP_KEY`/`APP_SECRET` должны совпадать с тем, что настроено на сервере.
3. В Xcode: Project → Info → Configurations → назначь `Config.xcconfig`.
4. Проверь, что `Config.xcconfig` в `.gitignore` (уже добавлен).

### Шаг 3. Шрифт и ассеты
1. Шрифт **Instrument Sans** (.ttf) → положить в `Resources/Fonts/`, добавить в
   таргет и в `Info.plist` (`UIAppFonts`). См. `Resources/Fonts/README`.
2. Все картинки из `Resources/Images/` (assets/ + landing/) → добавить в
   **Assets.xcassets** (имена без расширения = как в коде: `icon-token`,
   `Userpic`, `toontoon-logo`, `Close` и т.д.).

### Шаг 4. Приватность
- Добавь `Resources/PrivacyInfo.xcprivacy` в таргет (Nutrition Labels/manifest).

### Шаг 5. Собрать и запустить
- Собери на симуляторе. Проверь поток: **Landing → Register/Login → Generate →
  меню → Profile**.
- Если сборка ругается на отсутствующий символ — сверься с `Theme.swift`
  (все цвета/отступы/радиусы только оттуда) и §2 стандартов в `WHAT-ELSE-TODO.md`.

### Шаг 6. Дальше — фичи по приоритету (см. §4 ниже)

---

## 4. Приоритет фич (рекомендованный порядок)

1. **Собрать проект (Шаги 1–5)** — увидеть текущее вживую.
2. **Landing** — анимированная карусель из 5 карточек (сейчас статика).
3. **Start-экран «/»** (welcome: prompt-инпут + фильтр-пилюли) + **онбординг-тур**
   (3 шага, тексты в `MOBILE-DESIGN-FULL.md §9`).
4. **Персистентность истории** чатов (сейчас в памяти) + восстановление активной
   генерации.
5. **Список транзакций** в профиле (`GET /transactions` уже в APIClient).
6. **Аналитика** — расставить `screen_view` на всех экранах (см. `ANALYTICS-AUDIT.md §6`);
   опц. активировать TelemetryDeck/Sentry (2 шага каждый, `INTEGRATIONS.md`).
7. **К релизу:** домен + Let's Encrypt (убрать ATS-исключение, `secure` cookie,
   обновить пин), Apple App Attest, «Забыли пароль» + реальная верификация email.

---

## 5. Что в репо и что НЕ в репо (важно про секреты)

**В репозитории (тянем отсюда для приложения):**
- Весь Swift-код `ToontoonApp/`, все ассеты `Resources/Images/`, `PrivacyInfo.xcprivacy`.
- Все доки `docs/*.md` + `docs/openapi.json` (схема API).
- `Config.example.xcconfig` (шаблон без значений).

**НЕ в репозитории (специально исключено `.gitignore`):**
- `Config.xcconfig` — реальные `APP_KEY`/`APP_SECRET`/DSN.
- Любые ключи провайдеров — они только на сервере.
- Шрифт-файлы .ttf (лицензия) — добавляешь локально.

> Правило: приложение получает **данные** с сервера, **код/дизайн** — из репо,
> **секреты** — из локального `Config.xcconfig`. Ничего секретного в git не уходит.

---

## 6. Указатель по докам (`docs/`)

| Файл | О чём |
|---|---|
| **START-HERE.md** | этот файл — с чего начать |
| `APP-READINESS.md` | статус готовности: что готово / заглушки / чего ждём от сервера |
| `MOBILE-DESIGN-FULL.md` | полная вёрстка ВСЕХ экранов 1:1 (моб) |
| `SCREENS.md` / `NAVIGATION.md` | список экранов и навигация/роутинг после логина |
| `DESIGN.md` | дизайн-токены (цвета/отступы/шрифт) |
| `API.md` / `openapi.json` | все ручки бэкенда + схема |
| `AUTH.md` / `AUTH-SCREENS.md` | экраны и логика входа/регистрации |
| `AUTH-EMAIL-PASSWORD-PLAN.md` / `AUTH-SETUP-ANALYSIS.md` | как сделан email+пароль |
| `AUTH-PASSWORD-RESET.md` | сброс пароля (forgot/reset) — сервер + клиент |
| `SECURITY.md` | пиннинг, app-key, HMAC, ATS, план к домену |
| `BACKEND-TODO.md` | ТЗ для бэкенда: что серверу доделать (почта, verify/reset, домен) |
| `ANALYTICS-AUDIT.md` | аудит аналитики + каталог событий |
| `INTEGRATIONS.md` | как включить TelemetryDeck / Sentry (по шагам) |
| `PROMPT-ANIMATION.md` | анимация печатающего плейсхолдера в композере |
| `CHECKLIST-1TO1.md` | чек-лист «пиксель-в-пиксель» с вебом |
| `WHAT-ELSE-TODO.md` | СТАНДАРТЫ (чтобы всё было одинаково) + полный TODO |

---

## 7. Напоминания по безопасности (сделать вне кода)
- [ ] **Отозвать засвеченные в чате GitHub-токены** и держать новые вне чата.
- [ ] `APP_KEY`/`APP_SECRET` на сервере и в `Config.xcconfig` — держать в секрете.
- [ ] Перед публичным релизом: домен + HTTPS-сертификат, App Attest.

> Держась стандартов из `WHAT-ELSE-TODO.md §2`, любой новый экран/фича
> автоматически будут «в том же стиле», что и остальное приложение.
