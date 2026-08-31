# 11 — SSRF (Server-Side Request Forgery)

> Когда сервер делает HTTP-запрос по URL, который контролирует пользователь. Позволяет достучаться до
> внутренней сети, cloud-metadata, служебных сервисов. Особенно опасен в микросервисной/контейнерной
> архитектуре, где внутри сети нет аутентификации.

**Легенда:** 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low · ⚪ Info
**OWASP:** A10:2021 / API7:2023 · **ASVS:** V12.6 · **CWE:** 918

---

## Категории
1. [Источники SSRF](#1-источники-ssrf)
2. [Блокировка внутренних адресов](#2-блокировка-внутренних-адресов)
3. [Обходы фильтров](#3-обходы-фильтров)
4. [Сетевые контрмеры](#4-сетевые-контрмеры)

---

## 1. Источники SSRF

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| SSRF-1.1 | URL из user input в запросах | 🔴 | `requests.get(user_url)`, `httpx`, `aiohttp`, `fetch` серверный | Валидация + allowlist |
| SSRF-1.2 | `callback_url` вебхука | 🟠 | Клиент задаёт URL, сервер шлёт POST → внутр. сеть | Строгая валидация (см. §2) |
| SSRF-1.3 | Загрузка по URL (аватар/превью) | 🟠 | Сервер качает картинку по URL | Та же валидация, что и callback |
| SSRF-1.4 | Webhook/import из URL | 🟠 | Импорт данных по внешнему URL | Allowlist доменов |
| SSRF-1.5 | PDF/скриншот/рендер сервисы | 🟠 | Headless-браузер по user URL | Изоляция + фильтрация |
| SSRF-1.6 | XML/XXE → SSRF | 🟠 | Внешние entity в XML | См. [05](./05-injections.md) §6 |
| SSRF-1.7 | Link preview / unfurl | 🟠 | Разворачивание ссылок сервером | Фильтрация + таймаут |

> ⚠️ **Урок из практики:** `callback_url` валидировался только по схеме (`http/https`), без фильтрации
> приватных IP. Мерчант мог указать `http://redis:6379`, `http://mariadb:3306`, `http://169.254.169.254`
> и заставить сервер обращаться во внутреннюю сеть.

---

## 2. Блокировка внутренних адресов

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| SSRF-2.1 | Loopback | 🟠 | `127.0.0.1`, `::1`, `localhost` | Блокировать |
| SSRF-2.2 | Private ranges | 🟠 | `10.x`, `172.16–31.x`, `192.168.x`, `fc00::/7` | Блокировать |
| SSRF-2.3 | Link-local / metadata | 🔴 | `169.254.169.254` (AWS/GCP/Azure metadata) | Блокировать явно |
| SSRF-2.4 | Внутренние hostname | 🟠 | Имена контейнеров/сервисов (`redis`, `mariadb`) | Резолвить и проверять IP |
| SSRF-2.5 | Резолв до запроса | 🟠 | Проверка по hostname, но коннект по IP | Резолвить, проверить IP, коннектиться к нему же |
| SSRF-2.6 | Protocol restriction | 🟡 | `file://`, `gopher://`, `dict://`, `ftp://` | Только `https` (или `http` явно) |

### Пример валидации

```python
import ipaddress, socket
from urllib.parse import urlparse

def validate_outbound_url(url, allow_http=False):
    p = urlparse(url)
    schemes = ("http", "https") if allow_http else ("https",)
    if p.scheme not in schemes:
        raise ValueError("scheme not allowed")
    host = p.hostname
    if not host:
        raise ValueError("no host")
    for *_ , sa in socket.getaddrinfo(host, None):
        ip = ipaddress.ip_address(sa[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("blocked internal target")
```

---

## 3. Обходы фильтров

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| SSRF-3.1 | DNS rebinding | 🟠 | TTL 0: резолв при проверке ≠ при коннекте | Pin IP; резолв один раз, коннект к тому же IP |
| SSRF-3.2 | Redirect following | 🟠 | 302 на internal обходит проверку | Не следовать / валидировать каждый hop |
| SSRF-3.3 | Альтернативные форматы IP | 🟡 | `0x7f.0.0.1`, `2130706433`, `[::ffff:127.0.0.1]` | Нормализация IP перед проверкой |
| SSRF-3.4 | Enclosed alphanumerics/unicode | 🟢 | Обфускация hostname | Нормализация |
| SSRF-3.5 | Открытый прокси в приложении | 🟡 | Эндпоинт-прокси без ограничений | Allowlist назначений |

---

## 4. Сетевые контрмеры

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| SSRF-4.1 | Egress filtering | 🟠 | Контейнер может ходить куда угодно | Ограничить исходящие подключения |
| SSRF-4.2 | Метадата-сервис защищён | 🟠 | IMDSv1 доступен | IMDSv2 (hop-limit), блок 169.254.169.254 |
| SSRF-4.3 | Сегментация сети | 🟡 | Web видит БД/брокер напрямую | Отдельные сети, network policies |
| SSRF-4.4 | Исходящий прокси с allowlist | 🟡 | Прямые исходящие из app | Прокси с allowlist доменов |
| SSRF-4.5 | Внутренние сервисы с auth | 🟠 | Внутри сети нет аутентификации | Auth даже во внутренней сети (zero-trust) |

---

## Быстрые команды проверки

```bash
# HTTP-запросы с потенциально user-controlled URL
rg "requests\.(get|post|put|delete|head|request)\(" --type py
rg "httpx\.(get|post|AsyncClient)|aiohttp\.(ClientSession|request)" --type py
rg "urllib\.request|urlopen\(" --type py
rg "fetch\(|axios\.(get|post)\(|http\.(get|request)\(" 

# callback/webhook URL
rg -i "callback_url|webhook_url|notify_url|redirect_uri|image_url|avatar_url" 

# Валидация приватных диапазонов (должна быть!)
rg -i "is_private|is_loopback|169\.254|127\.0\.0\.1|localhost|ipaddress" 

# allow_redirects
rg "allow_redirects\s*=\s*True|follow.?redirects" 
```
