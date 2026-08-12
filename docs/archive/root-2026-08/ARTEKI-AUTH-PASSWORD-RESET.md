# ARTEKI — сброс пароля (forgot / reset password)

Фича реализована и на сервере, и в приложении, **работает end-to-end уже сейчас**
без почтового сервиса — по тому же принципу, что и magic-link: токен сброса
возвращается прямо в ответе (dev). Сделано 14.07.2026.

---

## Как работает (поток)

```
LoginView → «Forgot password?» → ForgotPasswordView
  1) email  → POST /api/auth/forgot-password
              сервер кладёт reset-токен в Redis (TTL 60 мин) и (в dev) вернёт его
              в ответе → клиент подставляет токен в поле автоматически
  2) токен + новый пароль (≥8, repeat) → POST /api/auth/reset-password
              сервер меняет Argon2-хэш, токен гасится (одноразовый)
  3) success → «Back to log in» → вход новым паролем
```

---

## Сервер (`/opt/gen-backend`)

**Ручки (`app/routers/auth.py`):**

`POST /api/auth/forgot-password`
```jsonc
// тело
{ "email": "user@example.com" }
// ответ — ВСЕГДА 200 (не раскрываем, есть ли аккаунт)
{ "ok": true }
// для email+password-аккаунта дополнительно (пока нет почты):
{ "ok": true, "devToken": "…", "devLink": "/reset-password?token=…" }
```
- Токен: `new_token()`, Redis-ключ `reset:{token}`, TTL `password_reset_ttl_minutes=60`.
- Rate-limit: `forgot:email` 3/15мин + `forgot:ip` 10/1ч.

`POST /api/auth/reset-password`
```jsonc
{ "token": "…", "new_password": "…(min 8)" }
// 200 { "ok": true }  |  400 { "detail": "invalid_or_expired" }
```
- Меняет `password_hash` (Argon2), токен одноразовый. Rate-limit `reset:ip` 20/15мин.

**Файлы, что затронуты (аддитивно):**
- `app/config.py` — `password_reset_ttl_minutes` + property `..._seconds`.
- `app/models/user.py` — `ForgotPasswordRequest`, `ResetPasswordRequest`.
- `app/services/auth_service.py` — `create_reset_token` / `consume_reset_token`
  (`reset:{token}`), зеркало magic-token.
- `app/routers/auth.py` — два роута + импорты.

**Проверено (temp-инстанс + прод после рестарта):** register → forgot(devToken)
→ reset(200) → reset-повтор(400) → login старым(401) → login новым(200) →
forgot неизвестного email(200 без токена). Существующие ручки не задеты.

---

## Клиент (`intersson-sir/arteki-ios`)

- `Networking/Models.swift` — `ForgotPasswordResponse { ok, devToken?, devLink? }`.
- `Networking/APIClient.swift` — `forgotPassword(email)`, `resetPassword(token,newPassword)`.
- `Auth/AuthManager.swift` — `requestPasswordReset`, `resetPassword`,
  `@Published resetDevToken` (для авто-подстановки токена в dev).
- `Features/Auth/ForgotPasswordView.swift` — 3-фазный экран (request/reset/done),
  стиль `AuthComponents` (1:1 с веб-картой).
- `Features/Auth/LoginView.swift` — ссылка «Forgot password?».

Аналитика: события `password_reset_requested`, `password_reset_completed`
(без PII).

---

## ❗️ Что сделать при подключении почты (§3 в `BACKEND-TODO.md`)
1. В `/forgot-password` слать письмо со ссылкой `devLink`-вида и **перестать
   возвращать `devToken`/`devLink`** (их наличие раскрывает существование
   аккаунта — ок только для dev).
2. В приложении: убрать авто-подстановку токена (`resetDevToken`), оставить экран
   ввода токена/пароля из письма; текст «email isn’t set up yet…» заменить на
   «мы отправили ссылку на почту».
3. Опц.: инвалидировать активные сессии пользователя после смены пароля
   (сейчас нет индекса user→sessions).
