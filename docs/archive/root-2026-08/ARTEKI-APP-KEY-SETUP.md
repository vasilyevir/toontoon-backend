# ARTEKI — App-key + HMAC подпись запросов (setup)

«Связь через ключи»: приложение подписывает каждый запрос, сервер проверяет.
Реализовано и на клиенте, и на сервере. **Задеплоено 14.07.2026, по умолчанию
ВЫКЛючено** (`APP_KEY_REQUIRED=false`) — включать после проверки на живом
мобильном клиенте (см. §5).

Честно о модели угроз: секрет зашит в приложение и извлекаем из бинарника — это
слой «поднять планку» против чужих ботов/скриптов, а не абсолютная подлинность.
Настоящую подлинность даёт Apple App Attest (`SECURITY.md`, к релизу).

---

## 1. Канон подписи (клиент и сервер собирают строку ОДИНАКОВО)

```
canonical = METHOD "\n" PATH "\n" TIMESTAMP "\n" sha256_hex(body)
signature = hex( HMAC_SHA256(key = APP_SECRET, msg = canonical) )
```
- `METHOD` — заглавными (`GET`, `POST`, `PATCH`, `DELETE`).
- `PATH` — путь с префиксом `/api`, без query. Пример: `/api/auth/login`.
- `TIMESTAMP` — unix-секунды строкой.
- `body` — сырое тело; для `GET` пустое; для multipart-загрузок тоже «пустое»
  (клиент подписывает до прикрепления файла) → `/api/uploads` исключён (§4).

**Заголовки запроса:**
```
X-Arteki-App-Key:   <APP_KEY>
X-Arteki-Timestamp: <unix seconds>
X-Arteki-Signature: <hex hmac-sha256>
```

Клиент: `ArtekiApp/Networking/APISecurity.swift → decorate()`.
Сервер: `app/middleware/app_key.py → AppKeyMiddleware`.

---

## 2. Значения ключей

- `APP_KEY` — не секрет (публичный идентификатор приложения), формат `ak_…`.
- `APP_SECRET` — **СЕКРЕТ**, формат `as_…`. В git не уходит.

Живут в двух местах, значения должны совпадать:
| Где | Файл | В git? |
|---|---|---|
| Приложение | `Config.xcconfig` → `ARTEKI_APP_KEY` / `ARTEKI_APP_SECRET` | ❌ (git-ignored) |
| Сервер | `/opt/gen-backend/.env` → `APP_KEY` / `APP_SECRET` | ❌ |

В репозитории — только плейсхолдеры (`Config.example.xcconfig`).

---

## 3. Сервер — что задеплоено

`app/config.py`:
```
app_key: str = ""
app_secret: str = ""
app_key_required: bool = False           # главный рубильник
app_sig_max_skew_seconds: int = 300      # окно против replay (±5 мин)
app_key_exempt_prefixes: str = "/api/uploads,/api/webhooks"
```
`.env` (значения заданы, рубильник выключен):
```
APP_KEY=ak_…
APP_SECRET=as_…
APP_KEY_REQUIRED=false
```
`app/middleware/app_key.py` — чистый ASGI-middleware: буферизует тело, собирает
канон, сверяет `hmac.compare_digest`, проверяет свежесть timestamp. При
`app_key_required=false` пропускает всё без изменений (нулевое влияние на прод).
Зарегистрирован в `app/main.py` (`app.add_middleware(AppKeyMiddleware)`).

**Ответы при включённой проверке (401):** `app key required` / `request signature
required` / `invalid timestamp` / `stale request signature` / `invalid request
signature`.

---

## 4. Что исключено из проверки

`app_key_exempt_prefixes` = `/api/uploads` (multipart, подписан по пустому телу)
и `/api/webhooks` (внешние server-to-server вызовы, напр. Boostify — свою
подпись проверяют сами). Всё остальное под `/api/*` подписывается.

---

## 5. Как включить (когда мобилка проверена)

1. Собрать приложение с непустыми `ARTEKI_APP_KEY`/`ARTEKI_APP_SECRET`
   (= значения из серверного `.env`).
2. Убедиться, что реальные запросы из приложения проходят (в логах нет 401
   `app key required`/`invalid request signature`).
3. ⚠️ **Веб-фронт эти заголовки НЕ шлёт.** `/api/auth/login`, `/api/generate` и
   т.п. используются и вебом. Если включить `APP_KEY_REQUIRED=true` глобально —
   веб ляжет (401). Варианты:
   - оставить `false` (сейчас так) — подпись доступна, но не обязательна; или
   - научить веб слать тот же ключ/подпись; или
   - вынести мобильные вызовы на отдельные роуты и требовать подпись только там.
4. После решения: `APP_KEY_REQUIRED=true` в `.env` → `systemctl restart
   arteki-backend.service`.

**Проверено при `APP_KEY_REQUIRED=true` (temp-инстанс):** неподписанный
`GET /api/tiles` → 401; корректно подписанный → 200; неверная подпись → 401;
подписанный `POST /login` доходит до хендлера (тело хешируется верно); просроченный
timestamp → 401 stale; `/api/uploads` и `/api/webhooks` не блокируются app-key.

---

## 6. Ротация секрета

Сменить `APP_SECRET` в серверном `.env` и в `Config.xcconfig`, пересобрать
приложение, перезапустить сервис. Старые сборки перестанут проходить подпись
(при `required=true`).
