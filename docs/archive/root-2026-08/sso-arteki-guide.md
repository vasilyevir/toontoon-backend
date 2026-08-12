# Boostyfi × Arteki — Integration Guide

> **Для команды Arteki.** Описывает уже реализованную OAuth2/SSO интеграцию и новые Wallet API эндпоинты, которые будут добавлены специально для Arteki.

---

## 1. Что уже работает

### 1.1 Discovery

```
GET https://api.boostyfi.com/api/v1/.well-known/openid-configuration
GET https://api.boostyfi.com/api/v1/jwks.json
```

JWKS содержит только публичные RSA-ключи (для верификации `access_token` и `id_token`). Токены подписаны **RS256**.

---

### 1.2 OAuth2 Authorization Code Flow

#### Шаг 1 — Redirect на страницу авторизации

```
GET https://api.boostyfi.com/api/v1/oauth/authorize
  ?client_id={CLIENT_ID}
  &redirect_uri={REDIRECT_URI}
  &response_type=code
  &scope=openid email profile wallet
  &code_challenge={CODE_CHALLENGE}
  &code_challenge_method=S256
  &state={CSRF_TOKEN}
```

> **Важно: PKCE обязателен.** Параметры `code_challenge` и `code_challenge_method=S256` — обязательные поля. Запросы без PKCE отклоняются. Вариант `plain` не поддерживается.
>
> Сгенерировать: `code_verifier` = случайная строка 43–128 символов; `code_challenge` = `BASE64URL(SHA256(code_verifier))`.

Сервер редиректит пользователя на страницу входа/подтверждения Boostyfi. После подтверждения — возврат на `redirect_uri`:

```
{REDIRECT_URI}?code={CODE}&state={CSRF_TOKEN}
```

#### Шаг 2 — Обмен кода на токены

```
POST https://api.boostyfi.com/api/v1/oauth/token
Content-Type: application/json

{
  "grant_type": "authorization_code",
  "code": "{CODE}",
  "redirect_uri": "{REDIRECT_URI}",
  "client_id": "{CLIENT_ID}",
  "client_secret": "{CLIENT_SECRET}",
  "code_verifier": "{CODE_VERIFIER}"
}
```

Ответ:

```json
{
  "access_token": "eyJ...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "openid email profile wallet",
  "refresh_token": "eyJ...",
  "id_token": "eyJ..."
}
```

Токены возвращаются только в этом ответе. `id_token` содержит базовые claims пользователя (sub, email, name) — удобно, чтобы не делать отдельный запрос к `/userinfo`.

> **Content-Type:** тело запроса должно быть `application/json` (не `application/x-www-form-urlencoded`).

---

### 1.3 Обновление access_token (Refresh)

```
POST https://api.boostyfi.com/api/v1/oauth/token
Content-Type: application/json

{
  "grant_type": "refresh_token",
  "refresh_token": "{REFRESH_TOKEN}",
  "client_id": "{CLIENT_ID}",
  "client_secret": "{CLIENT_SECRET}"
}
```

Ответ аналогичен — новые `access_token` + `refresh_token`. Старый refresh_token после использования становится недействительным (ротация).

---

### 1.4 Данные пользователя

```
GET https://api.boostyfi.com/api/v1/oauth/userinfo
Authorization: Bearer {ACCESS_TOKEN}
```

Ответ зависит от запрошенных scopes:

```json
{
  "sub": "12345",
  "email": "user@example.com",
  "email_verified": true,
  "name": "User Name",
  "given_name": "User",
  "family_name": "Name",
  "preferred_username": "username"
}
```

---

### 1.5 Отзыв гранта

```
POST https://api.boostyfi.com/api/v1/sso/grants/{grant_id}/revoke
Authorization: Bearer {ACCESS_TOKEN}
```

```json
{ "revoked": true, "grant_id": 42 }
```

---

## 2. Как хранить авторизацию на стороне Arteki

Рекомендуемая схема — серверная сессия в Redis (как описано в вашей спецификации):

```
session:{sid}
  provider: "boostify"
  boostify_access_token: "eyJ..."
  boostify_refresh_token: "eyJ..."
  boostify_access_expires_at: 1750003600
  boostify_user_sub: "12345"
```

При каждом запросе к Boostify API: если `access_expires_at < now()` — сначала рефреш, затем запрос. `access_token` никогда не передаётся в браузер.

---

## 3. Wallet API (новые эндпоинты)

Следующие эндпоинты будут добавлены специально для Arteki.

**Scope:** все Wallet API-запросы требуют `scope=wallet` в access_token.

---

### 3.1 Баланс пользователя

```
GET https://api.boostyfi.com/api/v1/sso/arteki/balance
Authorization: Bearer {ACCESS_TOKEN}
```

Ответ:

```json
{
  "available": "45.0",
  "locked": "1000.0",
  "grant_cap": "500.0",
  "grant_spent": "10.0",
  "grant_remaining": "490.0",
  "grant_expires_at": "2026-07-21T12:00:00Z"
}
```

| Поле | Описание |
|---|---|
| `available` | Токены, доступные для немедленной траты |
| `locked` | Locked IMBA (vesting, доступны через сервис) |
| `grant_cap` | Максимальная сумма, разрешённая пользователем |
| `grant_spent` | Уже потрачено в рамках гранта |
| `grant_remaining` | Остаток лимита (`cap - spent`) |
| `grant_expires_at` | Дата истечения разрешения |

---

### 3.2 Двухфазный платёж — создание резерва

```
POST https://api.boostyfi.com/api/v1/sso/arteki/payment/create
Authorization: Bearer {ACCESS_TOKEN}
Content-Type: application/json

{
  "amount": 2,
  "reason": "arteki:video_generate"
}
```

> **Примечание:** поле `user_id` в теле запроса не требуется и игнорируется — пользователь идентифицируется из `access_token` (`sub` claim).

Ответ:

```json
{
  "payment_id": "pay_abc123",
  "status": "pending",
  "amount": 2,
  "reason": "arteki:video_generate",
  "expires_at": "2026-06-26T12:05:00Z"
}
```

Резерв живёт **5 минут**. Если не подтверждён и не отменён — автоматически переходит в `cancelled`.

Значения `reason`:

| Reason | Описание | Стоимость |
|---|---|---|
| `arteki:image_generate` | Генерация картинки / открытки | 1 TEKI |
| `arteki:video_generate` | Генерация видео | 2 TEKI |

---

### 3.3 Подтверждение платежа

```
POST https://api.boostyfi.com/api/v1/sso/arteki/payment/confirm
Authorization: Bearer {ACCESS_TOKEN}
Content-Type: application/json

{
  "payment_id": "pay_abc123"
}
```

Ответ:

```json
{
  "payment_id": "pay_abc123",
  "status": "confirmed",
  "amount": 2,
  "balance_after": "998.0"
}
```

При подтверждении токены списываются атомарно. Повторный запрос с тем же `payment_id` возвращает сохранённый результат (идемпотентно).

---

### 3.4 Отмена платежа

```
POST https://api.boostyfi.com/api/v1/sso/arteki/payment/cancel
Authorization: Bearer {ACCESS_TOKEN}
Content-Type: application/json

{
  "payment_id": "pay_abc123"
}
```

Ответ:

```json
{
  "payment_id": "pay_abc123",
  "status": "cancelled"
}
```

Резерв снимается, баланс не меняется.

---

### 3.5 История транзакций

```
GET https://api.boostyfi.com/api/v1/sso/arteki/transactions
Authorization: Bearer {ACCESS_TOKEN}
```

Опциональные query-параметры: `limit` (default 20, max 100), `offset`.

Ответ:

```json
[
  {
    "payment_id": "pay_abc123",
    "amount": -2,
    "reason": "arteki:video_generate",
    "status": "confirmed",
    "created_at": 1750000000
  }
]
```

`amount` всегда отрицательный (списание).

---

## 4. Коды ошибок платежей

| HTTP | `error` | Причина |
|---|---|---|
| 400 | `cap_exceeded` | Исчерпан лимит гранта |
| 400 | `insufficient_balance` | Недостаточно токенов на балансе |
| 400 | `insufficient_vesting` | Недостаточно доступных vesting-позиций |
| 400 | `payment_expired` | Резерв истёк (5 минут) |
| 400 | `payment_not_pending` | Платёж уже подтверждён или отменён |
| 400 | `payment_not_found` | Неверный `payment_id` или принадлежит другому пользователю |
| 401 | `grant_revoked` | Пользователь отозвал разрешение |
| 401 | `grant_expired` | Истёк срок гранта |

---

## 5. Webhooks (опционально)

Если Arteki хочет получать push-уведомления при подтверждении/отмене платежей (например, для инвалидации кэша баланса):

```
POST {ARTEKI_WEBHOOK_URL}
Headers:
  X-Boostify-Signature: {HMAC-SHA256}
  X-Boostify-Event: payment.confirmed

Body:
{
  "event": "payment.confirmed",
  "payment_id": "pay_abc123",
  "user_id": "boostify_sub_string",
  "amount": 2,
  "reason": "arteki:video_generate",
  "timestamp": 1750000000
}
```

Подпись: `HMAC-SHA256(key=BOOSTIFY_WEBHOOK_SECRET, message=raw_body_bytes)`, hex-encoded.

Events: `payment.confirmed`, `payment.cancelled`.

---

## 6. Полная схема

```
Browser             ARTEKI backend               Boostify
  │                      │                           │
  │  GET /auth/login      │                           │
  │──────────────────────▶│                           │
  │                       │  302 ──────────────────▶ │
  │                       │          /oauth/authorize │
  │◀──────────────────────│          (PKCE required)  │
  │  302 → Boostify UI    │                           │
  │                       │                           │
  │  (user logs in +      │                           │
  │   sets cap & TTL)     │                           │
  │                       │                           │
  │  GET /auth/callback   │  POST /oauth/token ──────▶│
  │──────────────────────▶│◀── access+refresh+id ─────│
  │  302 → /generate      │                           │
  │◀──────────────────────│                           │
  │  Set-Cookie: sid      │                           │
  │                       │                           │
  │  GET /catalog/balance │  GET /sso/arteki/balance ▶│
  │──────────────────────▶│◀── { available, locked } ─│
  │                       │                           │
  │  POST /generate       │  POST /payment/create ───▶│
  │──────────────────────▶│◀── { payment_id, pending }│
  │                       │                           │
  │  [generation done ✓]  │  POST /payment/confirm ──▶│
  │                       │◀── { confirmed }          │
  │                       │                           │
  │  [generation error ✗] │  POST /payment/cancel ───▶│
  │                       │◀── { cancelled }          │
  │                       │                           │
  │  [token expired]      │  POST /oauth/token ──────▶│
  │                       │  grant_type=refresh_token │
  │                       │◀── { new access_token }───│
```

---

## 7. Что нужно для запуска

**От Boostify → Arteki:**
1. `client_id` и `client_secret` для Arteki
2. Production `BOOSTIFY_BASE_URL`: `https://api.boostyfi.com/api/v1`
3. Scope `wallet` активирован для client_id Arteki

**От Arteki → Boostify:**
1. Финальный `redirect_uri` для allowlist: `https://arteki.ai/api/auth/boostify/callback`
2. `ARTEKI_WEBHOOK_URL` (если нужны webhooks)
3. Подтверждение реализации PKCE на стороне Arteki (обязательно)

**Pending реализации на стороне Boostify:**
- `GET /sso/arteki/balance`
- `POST /sso/arteki/payment/create`
- `POST /sso/arteki/payment/confirm`
- `POST /sso/arteki/payment/cancel`
- `GET /sso/arteki/transactions`

---

## 8. Открытые вопросы

| Вопрос | Статус |
|---|---|
| Mapping `available` vs `locked` TEKI на поля баланса Boostify | Требует уточнения |
| Можно ли тратить `locked` токены внутри Arteki | Требует бизнес-решения |
| TTL резерва платежа (предложено 5 мин) | Требует подтверждения |
| Нужны ли webhooks или достаточно polling баланса | Требует уточнения |