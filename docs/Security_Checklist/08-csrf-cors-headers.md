# 08 — CSRF, CORS и Security Headers

> Межсайтовые атаки на запросы (CSRF), политика доступа между источниками (CORS) и защитные
> HTTP-заголовки. Ошибки здесь позволяют чужим сайтам действовать от имени пользователя или читать
> ответы API.

**Легенда:** 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low · ⚪ Info
**OWASP:** A01/A05 · **ASVS:** V4.2, V14.4, V14.5 · **CWE:** 352, 942, 693, 1021

---

## Категории
1. [CSRF](#1-csrf)
2. [CORS](#2-cors)
3. [Security Headers](#3-security-headers)
4. [Host / Origin валидация](#4-host--origin-валидация)

---

## 1. CSRF

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| CSRF-1.1 | CSRF-токен на формах | 🟠 | State-changing формы без токена | CSRF-токен (double-submit / synchronizer) |
| CSRF-1.2 | `SameSite` cookie | 🟠 | Нет `SameSite` → CSRF на cookie-auth | `Strict`/`Lax` (см. [03](./03-session-management.md)) |
| CSRF-1.3 | Нет state-changing GET | 🟠 | Изменение состояния по GET | Только POST/PUT/DELETE для изменений |
| CSRF-1.4 | `CSRF_TRUSTED_ORIGINS` | 🟡 | Слишком широкий список | Только доверенные домены |
| CSRF-1.5 | Проверка токена на сервере | 🟠 | Токен есть, но не проверяется | Валидация на бэкенде |
| CSRF-1.6 | JSON/API CSRF | 🟡 | Cookie-auth + принимает form-content-type | Требовать `Content-Type: application/json` + токен |
| CSRF-1.7 | Поддомены и общий eTLD+1 | 🟡 | `admin.`/`api.` на одном домене ослабляют SameSite | Разделение доменов / доп. защита |

> ℹ️ Для чисто header-based auth (Bearer в `Authorization`, не cookie) CSRF-риск ниже — но проверь,
> что нет параллельного cookie-приёма.

---

## 2. CORS

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| CORS-2.1 | Не `Allow-Origin: *` с креденшелами | 🟠 | `*` + `Allow-Credentials: true` (или auth-заголовки) | Явный whitelist origin |
| CORS-2.2 | `CORS_ALLOW_ALL_ORIGINS` | 🟠 | `True` → любой сайт делает запросы | Whitelist доверенных |
| CORS-2.3 | Reflected origin | 🟠 | Origin из запроса копируется в ответ без проверки | Сверять с allowlist |
| CORS-2.4 | Слабая проверка origin | 🟠 | `startswith`/`in` вместо точного совпадения (`evil-mysite.com`) | Точное совпадение из списка |
| CORS-2.5 | `Allow-Methods`/`Headers` | 🟡 | Избыточно разрешённые методы/заголовки | Минимально необходимые |
| CORS-2.6 | `null` origin | 🟡 | Разрешён `Origin: null` | Не доверять `null` |
| CORS-2.7 | Preflight кэширование | 🟢 | Слишком долгий `Access-Control-Max-Age` | Разумное значение |

> ⚠️ **Урок из практики:** `Access-Control-Allow-Origin: *` на API с auth-заголовками. Даже если auth в
> заголовке (не cookie), `*` избыточно разрешает любой сайт читать ответы. Заменить на allowlist.

---

## 3. Security Headers

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| HDR-3.1 | HSTS | 🟠 | Нет `Strict-Transport-Security` → SSL-strip | `max-age=31536000; includeSubDomains; preload` |
| HDR-3.2 | CSP | 🟠 | Нет Content-Security-Policy | См. [07](./07-xss.md) §4 |
| HDR-3.3 | `X-Content-Type-Options` | 🟡 | Нет `nosniff` | `nosniff` |
| HDR-3.4 | `X-Frame-Options`/frame-ancestors | 🟠 | Отсутствует | `DENY`/`SAMEORIGIN` |
| HDR-3.5 | `Referrer-Policy` | 🟢 | Утечка URL в Referer | `strict-origin-when-cross-origin` |
| HDR-3.6 | `Permissions-Policy` | 🟢 | Не ограничены API браузера | `geolocation=(), microphone=(), camera=()` |
| HDR-3.7 | Скрытие `Server`/`X-Powered-By` | 🟡 | Раскрытие стека/версий | Удалить/замаскировать |
| HDR-3.8 | Единообразие по всем vhost | 🟡 | Заголовки заданы неравномерно | Общий snippet на все домены |
| HDR-3.9 | `Cache-Control` для чувствительного | 🟡 | Чувствительные ответы кэшируются | `no-store` на приватных ответах |
| HDR-3.10 | CORP/COOP/COEP | 🟢 | Нет изоляции для чувствительных страниц | Задать при необходимости |

### Готовый snippet (Caddy)

```caddy
(secure_headers) {
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
        Content-Security-Policy "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'"
        Permissions-Policy "geolocation=(), microphone=(), camera=()"
        -Server
    }
}
# import secure_headers  внутри каждого site-блока
```

---

## 4. Host / Origin валидация

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| HOST-4.1 | `ALLOWED_HOSTS` | 🟡 | `["*"]` → Host header injection | Явный список хостов |
| HOST-4.2 | Host header в ссылках | 🟠 | Password-reset ссылка из Host-заголовка → poisoning | Фиксированный базовый URL |
| HOST-4.3 | Absolute redirects | 🟡 | Redirect по user-controlled host | Только относительные / whitelist |
| HOST-4.4 | Cache poisoning через Host | 🟡 | Кэш ключуется без учёта Host | Корректный cache key |

---

## Быстрые команды проверки

```bash
# CORS
rg -i "CORS_ALLOW_ALL_ORIGINS|Access-Control-Allow-Origin|allow_origins" 
rg -i "cors" -A3 | rg -i "\*|origin"

# CSRF
rg -i "csrf|CSRF_TRUSTED_ORIGINS|csrf_exempt|@csrf" 
rg -i "SESSION_COOKIE_SAMESITE|SameSite"

# Заголовки
rg -i "strict-transport-security|content-security-policy|x-frame-options|allowed_hosts"

# Проверка «живьём»
curl -sI https://target/ | grep -iE "strict-transport|content-security|x-frame|x-content-type|access-control"
```
