# Безопасность связи «приложение ↔ сервер»

Документ описывает модель безопасности TOONTOON iOS: что уже сделано, что делает
клиент, что — сервер, и честные ограничения.

## TL;DR
- ✅ **Сервер теперь работает по HTTPS** (self-signed, порт 443) — трафик
  зашифрован. HTTP (80) оставлен рабочим, ничего не сломано.
- ✅ **Приложение пиннит сертификат сервера** (SPKI) → доверяет ТОЛЬКО нашему
  серверу, MITM/перехват невозможны даже без домена.
- ✅ **Провайдерские ключи** (OpenAI/kie.ai/Boostyfi) в апку не попадают — только на сервере.
- ✅ **Вся конфигурация апки** — в одном месте: `AppConfig.swift` + `APISecurity.swift`
  (+ секреты в git-ignored `Config.xcconfig`).
- ⚠️ **App-key и подпись запросов** — слой «поднять планку» против ботов, но
  секрет в приложении извлекаем. Настоящая подлинность клиента = Apple App Attest.
- ⏭️ Появится домен → перейти на Let's Encrypt (настоящий cert), убрать ATS-исключение.

## Что относится к апке (отдельные файлы)
| Файл | Назначение |
|---|---|
| `ToontoonApp/App/AppConfig.swift` | Единая точка: адрес сервера, ключи, пин |
| `ToontoonApp/Networking/APISecurity.swift` | Пиннинг, app-key, подпись, запрет http |
| `Config.xcconfig` (git-ignored) | Реальные значения (URL, ключи, пин) |
| `Config.example.xcconfig` | Шаблон с плейсхолдерами |

---

## 1. Транспорт (шифрование) — СДЕЛАНО на сервере
Nginx получил дополнительный блок на 443 (`/etc/nginx/sites-available/toontoon-ssl`),
self-signed сертификат с SAN на IP (`/etc/nginx/ssl/toontoon.{crt,key}`, срок 825 дней).
Существующий блок на 80 не тронут → веб продолжает работать.

**Пин для приложения (SPKI sha256, base64):**
```
E99T0fsMyx9voq24TqdzSf6t09e0Xi7P0aUjf3iKknc=
```
Уже прописан в `Config.example.xcconfig` (`TOONTOON_PINNED_SPKI`).

**Откат (если что-то пойдёт не так):**
```
sudo rm /etc/nginx/sites-enabled/toontoon-ssl && sudo nginx -t && sudo systemctl reload nginx
```

**Пересоздать пин** (если меняли сертификат):
```
openssl x509 -in /etc/nginx/ssl/toontoon.crt -pubkey -noout \
 | openssl pkey -pubin -outform der \
 | openssl dgst -sha256 -binary | openssl enc -base64
```

## 2. Пиннинг сертификата — СДЕЛАНО в апке
`APISecurity.validate()` сверяет SPKI сертификата сервера с `TOONTOON_PINNED_SPKI`.
Подключено в `APIClient` через `URLSessionDelegate` (challenge server-trust).
Если пин не совпал — соединение отвергается. Пустой пин → пиннинг выключен (dev).

## 3. App-key + подпись запросов — СДЕЛАНО в апке, на сервере ОПЦИОНАЛЬНО
Клиент на каждый запрос шлёт:
- `X-Toontoon-App-Key: <TOONTOON_APP_KEY>` — общий ключ приложения.
- `X-Toontoon-Timestamp: <unix>` и `X-Toontoon-Signature: <hmac>` — подпись.

**Каноническая строка для подписи** (сервер должен собрать ТОЧНО так же):
```
{METHOD}\n{PATH}\n{TIMESTAMP}\n{sha256_hex(body)}
signature = hex( HMAC_SHA256(key = TOONTOON_APP_SECRET, msg = canonical) )
```
(Примечание: multipart-загрузка `/uploads` пока подписывается по пустому телу —
если включаете строгую проверку, исключите этот роут или доработайте клиент.)

### Серверная часть (для второго Claude) — ВКЛючать ОСТОРОЖНО
⚠️ Важно: веб-фронт НЕ шлёт эти заголовки. Если включить обязательную проверку
глобально — веб перестанет работать. Поэтому:
- держать проверку **выключенной по умолчанию** (env-флаг), включать только когда
  готовы, и/или применять её лишь к «мобильным» маршрутам, либо научить веб слать ключ.
- на сервере уже есть готовые помощники: `app/core/security.py`
  (`sign_payload`/`verify_signature`, HMAC-SHA256) — переиспользовать их.

Набросок middleware (FastAPI), enforced только при `APP_KEY_REQUIRED=true`:
```python
# .env:  APP_KEY_REQUIRED=false   APP_KEY=<...>   APP_SECRET=<...>
import time, hashlib, hmac
from fastapi import Request
from starlette.responses import JSONResponse

@app.middleware("http")
async def app_key_guard(request: Request, call_next):
    if not settings.app_key_required:
        return await call_next(request)           # выключено → ничего не проверяем
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    key = request.headers.get("X-Toontoon-App-Key")
    ts  = request.headers.get("X-Toontoon-Timestamp")
    sig = request.headers.get("X-Toontoon-Signature")
    if key != settings.app_key or not ts or not sig:
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    if abs(time.time() - int(ts)) > 300:          # ±5 мин против replay
        return JSONResponse({"detail": "stale"}, status_code=403)
    body = await request.body()
    canonical = f"{request.method}\n{request.url.path}\n{ts}\n{hashlib.sha256(body).hexdigest()}"
    expected = hmac.new(settings.app_secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return JSONResponse({"detail": "bad signature"}, status_code=403)
    return await call_next(request)
```

## 4. Что уже защищает сервер (по факту)
- Сессии серверные (Redis), опаковый токен `secrets.token_urlsafe(32)`.
- Rate limiting по пользователю (`core/rate_limit.py`, Redis). **Рекомендация:**
  добавить лимит и по IP (против анонимного перебора).
- Webhooks Boostyfi проверяются по HMAC-подписи.
- Провайдерские секреты только на сервере.

## Честные ограничения (не «никто не взломает»)
- **Ключ/секрет в приложении извлекаемы** реверс-инжинирингом. App-key/подпись
  отсекают ботов и случайные запросы, но не защищённого противника.
- **Настоящая подлинность клиента** = Apple **App Attest** / DeviceCheck
  (криптографическое подтверждение, что запрос от подлинной копии вашей апки на
  реальном устройстве). Рекомендуется добавить перед публичным релизом.
- **Self-signed** годится, пока нет домена. Он безопасен ровно за счёт пиннинга.
  При смене сервера/сертификата НЕ ЗАБУДЬ обновить `TOONTOON_PINNED_SPKI` в апке,
  иначе приложение перестанет соединяться.

## Рекомендованные следующие шаги (по приоритету)
1. (Сделано) HTTPS + пиннинг.
2. Задать `TOONTOON_APP_KEY` / `TOONTOON_APP_SECRET` (свои случайные строки) в апке и
   на сервере; включить middleware, когда веб научится слать ключ или для mobile-роутов.
3. Rate limit по IP.
4. Купить домен → Let's Encrypt → убрать ATS-исключение, `secure=true` для cookie.
5. Apple App Attest для сильной проверки клиента.
6. Регулярные бэкапы Redis, `fail2ban`/лимиты на nginx, закрыть лишние порты.
