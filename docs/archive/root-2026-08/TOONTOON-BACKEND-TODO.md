# TOONTOON iOS — чего не хватает НА СЕРВЕРЕ (ТЗ для бэкенда)

Документ для бэкенд-разработчика. Что приложению уже отдаётся и **работает**, и
что серверу нужно **доделать**, чтобы iOS-клиент стал полностью функциональным.

**Сверено с исходниками бэкенда `/opt/gen-backend` и `https://193.149.190.155/api`
— 14.07.2026.** Базовый транспорт: HTTPS + пиннинг (`SECURITY.md`). Пути под `/api`.

> ⚠️ Правки против первой редакции этого ТЗ (после проверки кода сервера):
> §6 (rate-limit по IP) — **уже реализован** для register/login/events, а не
> отсутствует; §0 `/transactions` — уже отдаёт `payment_id`; §4 — helper'ы
> подписи есть, но middleware ещё не написан (нет ни флага, ни наброска в коде).

---

## 0. Что уже РАБОТАЕТ (сервер отдаёт, клиент потребляет) — не трогать

| Ручка | Метод | Статус | Клиент |
|---|---|---|---|
| `/auth/register` | POST | ✅ `{user, session_token}` | `APIClient.register` (email+пароль) |
| `/auth/login` | POST | ✅ `{user, session_token}` | `APIClient.login` |
| `/auth/me` | GET/DELETE | ✅ | `me()` / `logout()` |
| `/auth/profile` | PATCH/DELETE | ✅ | имя/аватар, удаление аккаунта |
| `/auth/magic-link` + `/auth/verify` | POST/GET | ✅ (легаси, оставить) | старый вход по ссылке |
| `/tiles`, `/tiles/featured`, `/tiles/freeform-question`, `/balance` | GET | ✅ | каталог + баланс |
| `/generate`, `/generations`, `/generations/{id}`, `/generations/{id}/share`, `/uploads` | POST/GET | ✅ | генерация, история, шеринг, фото |
| `/chat` | POST | ✅ | свободный чат |
| `/events` | POST | ✅ | аналитика (`ingestEvents`) |
| `/transactions` | GET | ✅ (401 без токена) | история TOONTOON |

> `user.provider` для email-потока приходит как `"email"` — клиент декодирует
> (enum `AuthProvider.email`). НЕ переименовывать значение.

### Контракт `/transactions` — ✅ уже соответствует клиенту
Сервер (`routers/payments.py`) отдаёт для локальных юзеров:
```json
{ "payment_id": "…", "type": "image", "prompt": "…",
  "amount": -20, "created_at": "2026-07-14T00:00:00Z" }
```
Клиентская модель `Transaction` ожидает `payment_id`, `amount` (отрицательное =
списание), `created_at` (ISO-строка) — обязательны; `type/prompt/reason/status`
опциональны. **Поле уже называется `payment_id`** — ничего менять не нужно.
Для Boostify-юзеров список берётся из Boostify — проверить, что там тоже есть
`payment_id`, `amount`, `created_at` (иначе элемент не декодируется у клиента).

---

## 1. ❌ Верификация email — ручек нет (подтверждено: нет `/auth/verify-email`)

**Сейчас:** экран `VerificationView` в приложении — ЗАГЛУШКА: любой ввод пропускает
дальше, письмо не шлётся. На сервере нет ни отправки письма, ни подтверждения, ни
поля `email_verified`.

**Нужно на сервере:**
1. **Отправка письма при регистрации.** В `/auth/register` после создания юзера —
   одноразовый токен (TTL ~24ч, Redis) + письмо со ссылкой/кодом. Нужен ESP (§3).
2. **`POST /api/auth/verify-email`**
   ```
   Тело:  { "token": "<из письма>" }   // или { "email": "...", "code": "123456" }
   Ответ: 200 { "verified": true }  |  400 { "detail": "invalid_or_expired" }
   ```
   При успехе — добавить и выставить `user.email_verified = true` (поля пока нет).
3. **`POST /api/auth/resend-verification`**
   ```
   Тело:  { "email": "..." }
   Ответ: 200 { "ok": true }          // всегда 200 (не палим наличие email)
   Лимит: 1/60с на email + IP-лимит (см. §6).
   ```
4. **(Опц.) Гейт:** пока `email_verified=false` — пускать в приложение (как сейчас);
   перед публичным релизом сделать обязательным.

**Клиент, когда сервер готов:** заменю `AuthManager.completeVerification()` на
реальный вызов `verify-email` + кнопку «отправить повторно» → `resend-verification`.

---

## 2. ✅ Сброс пароля — СДЕЛАНО (dev-режим, без почты) — 14.07.2026

Реализовано и на сервере, и в приложении. Работает end-to-end уже сейчас: токен
сброса возвращается прямо в ответе (как magic-link), пока не подключена почта.

**Сервер (`routers/auth.py`, протестировано):**
1. **`POST /api/auth/forgot-password`** — тело `{ "email": "..." }` → всегда
   `200 { "ok": true }`; если это email+password-аккаунт, дополнительно
   `{ "devToken": "...", "devLink": "/reset-password?token=..." }`. Токен в Redis,
   ключ `reset:{token}`, TTL 60 мин. Лимиты: `forgot:email` 3/15мин + `forgot:ip`
   10/1ч.
2. **`POST /api/auth/reset-password`** — тело `{ "token", "new_password"(≥8) }` →
   `200 { "ok": true }` | `400 { "detail": "invalid_or_expired" }`. Меняет
   Argon2-хэш, токен одноразовый. Лимит `reset:ip` 20/15мин.

**Клиент:** «Forgot password?» на `LoginView` → `ForgotPasswordView` (email →
токен+новый пароль, токен подставляется автоматически в dev).

**❗️ TODO при подключении почты (§3):** в `/forgot-password` начать РЕАЛЬНО
слать письмо и **перестать возвращать `devToken`/`devLink`** (сейчас их наличие
раскрывает существование аккаунта — приемлемо только в dev). Опц.:
инвалидировать активные сессии юзера после смены пароля (сейчас не делаем —
нет индекса user→sessions).

---

## 3. ❌ Почтовый провайдер (ESP/SMTP) — предпосылка для §1 и §2

Подтверждено: в коде нет ни smtp/resend/postmark/sendgrid/ses. Без отправки писем
§1 и §2 невозможны.
- Подключить ESP (Resend / Postmark / SES / SMTP). Ключ — в серверный `.env`, НЕ в
  приложение.
- Шаблоны: «подтвердите email», «сброс пароля» (ссылка + fallback-код).
- Домен для From-адреса желателен (иначе спам) — связано с §5.

---

## 4. ✅ App-key + HMAC-подпись — middleware НАПИСАН и ЗАДЕПЛОЕН (флаг выключен)

Сделано 14.07.2026. Полностью — `docs/APP-KEY-SETUP.md`.
- `app/middleware/app_key.py` (`AppKeyMiddleware`, чистый ASGI) — канон
  `METHOD\nPATH\nTIMESTAMP\nsha256_hex(body)`, HMAC-SHA256, окно ±300с, исключены
  `/api/uploads` и `/api/webhooks`. Зарегистрирован в `main.py`.
- `app/config.py`: `app_key/app_secret/app_key_required(false)/app_sig_max_skew_seconds/
  app_key_exempt_prefixes`.
- `.env`: `APP_KEY`/`APP_SECRET` заданы, `APP_KEY_REQUIRED=false`.
- Протестировано при `=true`: подписанные проходят, неподписанные/битые/просроченные
  → 401; exempt-роуты не блокируются. При `=false` (сейчас) — ноль влияния на прод.

**❗️ Осталось перед включением (`APP_KEY_REQUIRED=true`):** веб-фронт заголовки НЕ
шлёт, а `/api/auth/login`, `/api/generate` и т.п. используются и вебом → глобальное
включение уронит веб. Решить: научить веб подписывать, либо вынести мобильные
вызовы на отдельные роуты. Пока держим `false`.

---

## 5. 🟡 Домен + Let's Encrypt (к релизу)

Сейчас: self-signed на IP + пиннинг (безопасно, но не для App Store в проде).
1. Домен (напр. `api.toontoon.ai`) → A-запись на `193.149.190.155`.
2. Let's Encrypt (certbot) вместо self-signed.
3. Затем на клиенте: сменить `TOONTOON_BASE_URL` на домен, обновить/убрать
   `TOONTOON_PINNED_SPKI`, убрать ATS-исключение в `Info.plist`; на сервере включить
   `secure=true` для cookie сессии.

---

## 6. ✅/🟡 Rate-limit по IP — БОЛЬШЕЙ ЧАСТЬЮ УЖЕ ЕСТЬ

Подтверждено в `routers/auth.py` + `core/rate_limit.py` (Redis, fixed-window),
`_client_ip()` учитывает `x-real-ip` / `x-forwarded-for`:

| Ручка | Лимит | 
|---|---|
| `/auth/register` | `register:ip` 10 / 1ч |
| `/auth/login` | `login:ip` 20 / 15мин **и** `login:email` 8 / 15мин |
| `/events` | `events:ip` 120 / 60с |
| `/generate` | `gen:{user_id}` (по юзеру) |

| `/auth/forgot-password` | `forgot:ip` 10/1ч + `forgot:email` 3/15мин ✅ (сделано §2) |
| `/auth/reset-password` | `reset:ip` 20/15мин ✅ (сделано §2) |

**Остаточные гэпы:**
- `/auth/magic-link` — IP-лимита нет, добавить (напр. 10/час/IP).
- Будущий `/auth/resend-verification` (§1) — сразу завести IP-лимит при реализации.
- (Опц.) экспоненциальный бэкофф на повторные неудачи логина.

---

## 7. (К релизу) Apple App Attest

Для настоящей подлинности клиента — принимать на сервере attestation/assertion от
Apple App Attest и проверять на чувствительных ручках (`/generate`, `/auth/*`).
Требует Apple Developer capability на клиенте. Детали — `SECURITY.md`.

---

## Итог приоритетов для сервера
1. **Почта (§3)** → **верификация email (§1)**; сброс пароля (§2) уже работает в
   dev — при подключении почты убрать `devToken` и слать письмо.
2. **Мелкий догон rate-limit (§6):** IP-лимит на `magic-link`.
3. **Домен + Let's Encrypt (§5)** — перед публикацией.
4. **App-key middleware (§4)** и **App Attest (§7)** — усиление к релизу.

Всё, что не в этом списке, у приложения уже есть и тянется с сервера успешно.
