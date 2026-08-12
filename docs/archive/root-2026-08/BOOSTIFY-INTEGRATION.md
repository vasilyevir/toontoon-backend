# ARTEKI × Boostify — Integration Specification

> **Для команды Boostify.** Этот документ описывает как ARTEKI реализует авторизацию через Boostify, как отображает баланс TEKI, и как проводит списания. В конце — предложения по двум вариантам реализации «живого» баланса и что нужно сделать с каждой стороны.

---

## 1. Общая схема

```
Browser                ARTEKI backend               Boostify
  │                        │                            │
  │  GET /api/auth/         │                            │
  │  boostify/login         │                            │
  │────────────────────────▶│                            │
  │                         │  redirect ──────────────▶ │
  │                         │                   /oauth/authorize
  │◀────────────────────────│                            │
  │  302 → Boostify UI      │                            │
  │                         │                            │
  │  (user authenticates)   │                            │
  │                         │                            │
  │  GET /api/auth/          │  POST /oauth/token ──────▶│
  │  boostify/callback       │◀──── tokens + userinfo ───│
  │──────────────────────────▶                           │
  │                         │                            │
  │  302 → /generate         │                            │
  │◀────────────────────────│                            │
  │  Set-Cookie: arteki-session=<sid>                    │
```

---

## 2. OAuth-авторизация — что уже реализовано на стороне ARTEKI

### 2.1 Конфигурация (`.env`)

| Переменная | Пример | Описание |
|---|---|---|
| `BOOSTIFY_BASE_URL` | `https://api.boostify.io` | Base URL всех API Boostify |
| `BOOSTIFY_CLIENT_ID` | `arteki` | OAuth2 client_id, выдаётся Boostify |
| `BOOSTIFY_CLIENT_SECRET` | `secret` | OAuth2 client_secret |
| `BOOSTIFY_REDIRECT_URI` | `https://arteki.ai/api/auth/boostify/callback` | Redirect после авторизации |
| `BOOSTIFY_WEBHOOK_SECRET` | `secret` | HMAC-ключ для верификации вебхуков |
| `BOOSTIFY_MOCK` | `false` | `true` = mock-режим (без реального Boostify) |

### 2.2 Эндпоинты ARTEKI (backend)

```
GET  /api/auth/boostify/login      # Redirect → Boostify /oauth/authorize
GET  /api/auth/boostify/callback   # Принимает code+state, обменивает на токены
```

### 2.3 Authorize URL (шаг 1)

ARTEKI строит URL вида:
```
{BOOSTIFY_BASE_URL}/oauth/authorize
  ?client_id={CLIENT_ID}
  &redirect_uri={REDIRECT_URI}
  &response_type=code
  &scope=openid email profile wallet
  &state={csrf_token}
```

**Требуется от Boostify:** поддержка `scope=wallet` (нужен доступ к балансу через access_token).

### 2.4 Token Exchange (шаг 2)

```
POST {BOOSTIFY_BASE_URL}/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
code={code}
redirect_uri={REDIRECT_URI}
client_id={CLIENT_ID}
client_secret={CLIENT_SECRET}
```

**Ожидаемый ответ:**
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "expires_in": 3600,
  "user": {
    "sub": "boostify_user_id_string",
    "email": "user@example.com",
    "name": "User Name",
    "avatar": "https://cdn.boostify.io/avatars/..."
  }
}
```

> Поле `user` (userinfo) должно быть в ответе токена — это необязательный стандарт, но ARTEKI ожидает его здесь, чтобы сэкономить RTT. Либо Boostify может реализовать `/userinfo` отдельно — мы поддержим оба варианта.

### 2.5 Token Refresh

```
POST {BOOSTIFY_BASE_URL}/oauth/token
grant_type=refresh_token
refresh_token={refresh_token}
client_id={CLIENT_ID}
client_secret={CLIENT_SECRET}
```

Ответ аналогичен — новый `access_token` + обновлённый `refresh_token`.

---

## 3. Как ARTEKI хранит авторизацию

После callback ARTEKI создаёт **серверную сессию** в Redis:

```
Redis key: session:{sid}
TTL: 30 дней
Поля:
  - sid                         (cookie value)
  - user_id                     (внутренний ARTEKI UUID)
  - provider = "boostify"
  - boostify_access_token
  - boostify_refresh_token
  - boostify_access_expires_at  (epoch seconds)
```

В браузер устанавливается `HttpOnly` cookie `arteki-session={sid}`. Всё остальное — на бэкенде. Boostify access_token **никогда не попадает в браузер**.

При каждом запросе к ARTEKI API происходит:
1. Читаем `sid` из cookie
2. Достаём Session из Redis
3. Если `boostify_access_expires_at` < now → автоматически рефрешим токен через Boostify
4. Используем актуальный `access_token` для запросов к Boostify API

---

## 4. Баланс TEKI — как пользователь его видит

```
Шапка страницы (ARTEKI UI):
┌─────────────────────────────────────────────────────┐
│  🟣 Arteki Studio      [45 TEKI] [Upgrade]  [👤 Me] │
└─────────────────────────────────────────────────────┘
```

**Откуда цифра `45`?**

При загрузке страницы `/generate` фронтенд вызывает:
```
GET /api/catalog/balance
→ { "available": 45, "locked": 1000 }
```

ARTEKI backend делает:
```python
# wallet.py
async def get_balance(user, session):
    if user.provider == "boostify":
        token = await ensure_boostify_token(session)
        return await boostify.get_balance(token)  # запрос к Boostify
    return Balance(available=user.teki_balance)   # magic-link: локальный счёт
```

То есть для Boostify-пользователей баланс всегда **live** — берётся напрямую от Boostify через access_token.

**Что нужно от Boostify:**
```
GET {BOOSTIFY_BASE_URL}/user/balance
Authorization: Bearer {access_token}

→ 200 OK
{
  "available": 45,
  "locked": 1000
}
```

Поле `locked` — опционально, отображается отдельно (если есть будущая функциональность), `available` — основное число в шапке.

---

## 5. Списание TEKI — два варианта интеграции

### Вариант A: Two-Phase Payment через Boostify API (рекомендуется, текущая реализация)

Это **двухфазный платёж** — резерв → подтверждение/отмена. Так мы не списываем деньги за неуспешную генерацию.

**Схема:**
```
ARTEKI backend                        Boostify
     │                                    │
     │  POST /payment/create              │
     │  { user_id, amount, reason }       │
     │───────────────────────────────────▶│
     │◀─── { payment_id, status: "pending" }
     │                                    │
     │  [генерация выполнена ✓]           │
     │                                    │
     │  POST /payment/confirm             │
     │  { payment_id }                    │
     │───────────────────────────────────▶│
     │◀─── { status: "confirmed" }        │
     │                                    │
     │  [ошибка генерации ✗]              │
     │                                    │
     │  POST /payment/cancel              │
     │  { payment_id }                    │
     │───────────────────────────────────▶│
     │◀─── { status: "cancelled" }        │
```

**Эндпоинты Boostify которые нужны:**

```
POST {BOOSTIFY_BASE_URL}/payment/create
Authorization: Bearer {access_token}
Body:
{
  "user_id": "boostify_user_id",
  "amount": 2,
  "reason": "arteki:video_generate"
}
Response:
{
  "payment_id": "pay_abc123",
  "status": "pending"
}

---

POST {BOOSTIFY_BASE_URL}/payment/confirm
Authorization: Bearer {access_token}
Body: { "payment_id": "pay_abc123" }
Response: { "payment_id": "pay_abc123", "status": "confirmed" }

---

POST {BOOSTIFY_BASE_URL}/payment/cancel
Authorization: Bearer {access_token}
Body: { "payment_id": "pay_abc123" }
Response: { "payment_id": "pay_abc123", "status": "cancelled" }
```

**Значения поля `reason`** (строки, которые ARTEKI передаёт):
| Reason | Описание |
|---|---|
| `arteki:image_generate` | Генерация картинки/открытки |
| `arteki:video_generate` | Генерация видео |

**Стоимость:**
| Тип | Сейчас |
|---|---|
| Image / Postcard | 1 TEKI |
| Video | 2 TEKI |

---

### Вариант B: Boostify списывает сам, ARTEKI получает вебхук (альтернатива)

Если у Boostify уже есть своя UI/логика списаний, ARTEKI может просто **запрашивать разрешение** у Boostify, а Boostify присылает результат вебхуком.

```
ARTEKI backend                        Boostify
     │                                    │
     │  Просит Boostify зарезервировать  │
     │  N TEKI для user_id               │
     │───────────────────────────────────▶│
     │◀─── { approved: true }             │
     │                                    │
     │  [генерация завершилась]            │
     │                                    │
     │  Уведомляет Boostify о результате  │
     │───────────────────────────────────▶│
     │                                    │
     │  [Boostify отправляет вебхук]       │
Boostify ──▶ POST /api/webhooks/boostify │
             ARTEKI инвалидирует кэш баланса
```

**ARTEKI уже поддерживает вебхуки от Boostify:**
```
POST /api/webhooks/boostify
Headers: X-Boostify-Signature: {HMAC-SHA256 подпись тела}
Body:
{
  "event": "payment.confirmed",   // или "payment.cancelled"
  "payment_id": "pay_abc123",
  "user_id": "boostify_user_id",
  "amount": 2
}
```

Подпись считается как `HMAC-SHA256(secret=BOOSTIFY_WEBHOOK_SECRET, body=raw_bytes)`.

---

### Сравнение вариантов

| | **Вариант A** (API) | **Вариант B** (Webhook) |
|---|---|---|
| Реализован в ARTEKI | ✅ Да (текущий mock) | ✅ Да (вебхук endpoint) |
| Нужно от Boostify | 3 API эндпоинта | Подтверждение факта расхода |
| Latency списания | Синхронно | Async (вебхук) |
| Гарантия списания | Strong (двухфаза) | Depends on retry |
| Подходит для | Рекомендуется | Если Boostify контролирует UI |

**Рекомендация: Вариант A.** Он уже полностью реализован на стороне ARTEKI. Достаточно включить `BOOSTIFY_MOCK=false` и указать production URL.

---

## 6. История транзакций

Пользователь может запросить историю операций:
```
GET /api/transactions
Cookie: arteki-session=...
```

Для Boostify-пользователей ARTEKI проксирует запрос:
```
GET {BOOSTIFY_BASE_URL}/transactions
Authorization: Bearer {access_token}

→ 200 OK
[
  {
    "payment_id": "pay_abc123",
    "amount": -2,
    "reason": "arteki:video_generate",
    "created_at": 1750000000
  },
  ...
]
```

---

## 7. Что нужно реализовать Boostify

### 7.1 OAuth-сервер
- [x] `GET /oauth/authorize` — стандартный authorization code flow
- [x] `POST /oauth/token` — exchange code + refresh token
- [ ] `userinfo` в ответе `/oauth/token` (либо отдельный `GET /userinfo`) с полями: `sub`, `email`, `name`, `avatar`
- [ ] Scope `wallet` — разрешает доступ к balance/payment эндпоинтам

### 7.2 Wallet API
- [ ] `GET /user/balance` → `{ available, locked }`
- [ ] `POST /payment/create` → `{ payment_id, status: "pending" }`
- [ ] `POST /payment/confirm` → `{ payment_id, status: "confirmed" }`
- [ ] `POST /payment/cancel` → `{ payment_id, status: "cancelled" }`
- [ ] `GET /transactions` → `[{ payment_id, amount, reason, created_at }]`

### 7.3 Webhooks (опционально, для Варианта B или push-инвалидации кэша)
- [ ] `POST {ARTEKI_URL}/api/webhooks/boostify` с HMAC-SHA256 подписью
- [ ] Events: `payment.confirmed`, `payment.cancelled`

---

## 8. Что нужно от Boostify для запуска

Для перехода из `BOOSTIFY_MOCK=true` в production нужны:

1. **Client credentials**: `client_id` и `client_secret` для ARTEKI
2. **Production URLs**: `BOOSTIFY_BASE_URL`
3. **Redirect URI allowlist**: добавить `https://arteki.ai/api/auth/boostify/callback`
4. **Webhook secret**: общий `BOOSTIFY_WEBHOOK_SECRET` для HMAC-SHA256
5. **Scope `wallet`** активирован для ARTEKI client_id

После получения — просто обновляем `.env` на сервере и перезапускаем бэкенд. Весь код уже готов.

---

## 9. Схема данных пользователя ARTEKI (справка для Boostify)

```
User (Redis):
  id:                  "usr_abc123"           # ARTEKI internal ID
  provider:            "boostify"
  email:               "user@example.com"
  name:                "User Name"
  avatar:              "https://..."
  boostify_user_id:    "boostify_sub_string"  # sub из Boostify userinfo
  teki_balance:        0                      # не используется для Boostify-юзеров
  created_at:          "2026-01-01T00:00:00Z"
```

Ключ поиска по Boostify: `user:boostify:{boostify_user_id}` → ARTEKI user_id.

---

## 10. Locked vs Available TEKI — открытый вопрос

В текущем моке есть два поля:
- `available` — сколько можно тратить прямо сейчас
- `locked` — заблокированные токены (не тратятся)

**Вопрос для Boostify:** можно ли тратить `locked` токены внутри продукта ARTEKI? Если да — `available` в UI будет показывать `available + locked`. Если нет — показываем только `available`.

В ARTEKI UI сейчас показывается только `available`:
```
[45 TEKI]  ← это balance.available
```

---

*Документ подготовлен: ARTEKI backend team, June 2026.*  
*По вопросам: @arteki-dev*
