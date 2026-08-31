# 09 — API-безопасность и Rate Limiting

> Проверки уровня API: ограничение частоты, пагинация, GraphQL, WebSocket, HTTP-методы, версии,
> HTTP request smuggling. Основано на OWASP API Security Top 10 (2023).

**Легенда:** 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low · ⚪ Info
**OWASP API:** API1–API10:2023 · **CWE:** 307, 770, 400, 444

---

## Категории
1. [Rate Limiting и защита от abuse](#1-rate-limiting-и-защита-от-abuse)
2. [Pagination и фильтрация](#2-pagination-и-фильтрация)
3. [GraphQL](#3-graphql)
4. [WebSocket](#4-websocket)
5. [HTTP-методы, версии, метаданные](#5-http-методы-версии-метаданные)
6. [HTTP Request Smuggling](#6-http-request-smuggling)
7. [OWASP API Top 10 — быстрый проход](#7-owasp-api-top-10--быстрый-проход)

---

## 1. Rate Limiting и защита от abuse

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| API-1.1 | Login | 🟠 | Нет лимита → brute-force | 5–10/мин на IP+аккаунт |
| API-1.2 | Register | 🟡 | Mass-регистрация | Лимит + CAPTCHA |
| API-1.3 | Password reset | 🟡 | Email flooding | 3–5/час на email |
| API-1.4 | Signature/claim generation | 🟠 | Спам подписей → нагрузка на blockchain | Лимит + cooldown |
| API-1.5 | Webhook endpoints | 🟡 | DoS/амплификация через служебный эндпоинт | Лимит по ключу/IP |
| API-1.6 | Глобальный лимит | 🟡 | Нет API-wide лимита | Per user/IP лимит |
| API-1.7 | Лимит устойчив к обходу | 🟠 | Обход через `X-Forwarded-For` | Реальный IP от доверенного прокси |
| API-1.8 | Дорогие эндпоинты | 🟡 | Экспорт/поиск/агрегации без лимита | Отдельные строгие лимиты |
| API-1.9 | Storage лимитера | 🟢 | In-memory лимит не работает на нескольких инстансах | Redis-backed лимитер |

> 💡 Быстрый митигейт без передеплоя: rate-limit на reverse-proxy (Caddy `rate_limit`) или **fail2ban
> поверх access-логов** прокси (банит IP по повторяющимся 4xx на `/login`). Не трогает приложение.

---

## 2. Pagination и фильтрация

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| API-2.1 | Max page size | 🟡 | `?page_size=999999` → дамп БД | Серверный `max_page_size` |
| API-2.2 | Ordering injection | 🟡 | `?ordering=password`/`-is_staff` → раскрытие | Whitelist полей сортировки |
| API-2.3 | Фильтр-injection | 🟡 | Произвольные фильтры/операторы от клиента | Whitelist полей и операторов |
| API-2.4 | Cursor stability | 🟢 | Нестабильная пагинация | Стабильный cursor-based |
| API-2.5 | Раскрытие лишних полей | 🟠 | Serializer отдаёт внутренние поля (см. mass assignment reverse) | Явный список полей вывода |

---

## 3. GraphQL

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| API-3.1 | Introspection в проде | 🟡 | Открыт `__schema` → карта API | Отключить в production |
| API-3.2 | Depth limit | 🟠 | Глубоко вложенные запросы → DoS | Ограничение глубины |
| API-3.3 | Query complexity | 🟠 | Дорогие запросы → DoS | Cost analysis / лимит сложности |
| API-3.4 | Batching abuse | 🟠 | Массовые алиасы/батчи → обход rate-limit | Лимит на батч/алиасы |
| API-3.5 | Field-level authz | 🟠 | Права только на резолвере верхнего уровня | Авторизация на уровне полей |
| API-3.6 | Утечка ошибок | 🟡 | Verbose GraphQL-ошибки | Generic-сообщения в проде |

---

## 4. WebSocket

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| API-4.1 | Auth на handshake | 🟠 | WS без аутентификации | Проверять токен при подключении |
| API-4.2 | Origin check | 🟠 | Cross-Site WebSocket Hijacking | Проверять `Origin` |
| API-4.3 | Rate limiting сообщений | 🟡 | Флуд сообщениями | Лимит на сообщения/подключения |
| API-4.4 | Авторизация на сообщения | 🟠 | Права проверяются только при connect | Проверять на каждое действие |
| API-4.5 | Валидация payload | 🟡 | Сырые сообщения без схемы | Схема сообщений |

---

## 5. HTTP-методы, версии, метаданные

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| API-5.1 | Method override | 🟡 | `X-HTTP-Method-Override: DELETE` на GET | Не поддерживать / блокировать |
| API-5.2 | Deprecated версии | 🟠 | `/v1/` без актуальной auth — забытый бэкдор | Аудит и закрытие старых версий |
| API-5.3 | Verbose ошибки | 🟠 | Stack trace/traceback в JSON при 500 | Generic-ошибки (см. [23](./23-error-handling.md)) |
| API-5.4 | OPTIONS/TRACE | 🟢 | Включены лишние методы | Отключить TRACE, ограничить OPTIONS |
| API-5.5 | Content-Type enforcement | 🟡 | Приём любого content-type | Строгий парсинг по типу |
| API-5.6 | Тестовые эндпоинты в проде | 🟡 | `/debug`, `/test`, `X-Environment: test` публичны | Убрать/закрыть по IP |

> ⚠️ **Урок из практики:** тестовый инстанс на отдельном поддомене был публично доступен и отдавал
> заголовок `X-Environment: test`. Тестовые контуры — за VPN/IP-allowlist, без раскрывающих заголовков.

---

## 6. HTTP Request Smuggling

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| API-6.1 | CL.TE / TE.CL рассинхрон | 🟠 | Конфликт `Content-Length` + `Transfer-Encoding` между прокси и app | Обновить прокси; строго отклонять конфликт |
| API-6.2 | H2C smuggling | 🟠 | Проброс h2c upgrade через прокси → обход | Запретить h2c upgrade на прокси |
| API-6.3 | Дублирующиеся заголовки | 🟡 | Несколько `Content-Length`/`Host` | Нормализация/отклонение |

> ⚠️ **Урок из практики:** встречался CL.TE smuggling между reverse-proxy и app-сервером. Митигейт — обновить образ прокси
> (`caddy:2-alpine`) до свежего патча (pull + `up -d`, **не** пересборка приложения) и проверить PoC.

---

## 7. OWASP API Top 10 — быстрый проход

| API # | Название | Файл-ссылка |
|-------|----------|-------------|
| API1 | Broken Object Level Authorization (BOLA/IDOR) | [02](./02-authorization-idor.md) |
| API2 | Broken Authentication | [01](./01-authentication.md) |
| API3 | Broken Object Property Level Authorization (mass assignment) | [02](./02-authorization-idor.md) |
| API4 | Unrestricted Resource Consumption (rate limit/DoS) | этот файл §1 |
| API5 | Broken Function Level Authorization | [02](./02-authorization-idor.md) §4 |
| API6 | Unrestricted Access to Sensitive Business Flows | [17](./17-business-logic.md) |
| API7 | Server Side Request Forgery | [11](./11-ssrf.md) |
| API8 | Security Misconfiguration | [08](./08-csrf-cors-headers.md), [19](./19-infrastructure-docker-k8s.md) |
| API9 | Improper Inventory Management (версии/эндпоинты) | этот файл §5 |
| API10 | Unsafe Consumption of APIs (внешние API) | [11](./11-ssrf.md), [21](./21-dependency-supply-chain.md) |

---

## Быстрые команды проверки

```bash
rg -i "limiter|ratelimit|throttle|Flask-Limiter|django_ratelimit" 
rg -i "introspection|graphene|strawberry|apollo" 
rg -i "websocket|channels|socket\.io|ws://" 
rg -i "X-HTTP-Method-Override|method_override" 
rg -i "X-Forwarded-For|X-Real-IP|remote_addr|CF-Connecting-IP" 
rg -i "page_size|max_page_size|ordering" 
```
