# 07 — XSS и клиентские инъекции

> Cross-Site Scripting: выполнение чужого JS в браузере жертвы. Крадёт токены, выполняет действия
> от имени пользователя, дефейсит. Три типа: Reflected, Stored, DOM-based.

**Легенда:** 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low · ⚪ Info
**OWASP:** A03:2021 · **ASVS:** V5.3 · **CWE:** 79, 80, 116, 1021

---

## Категории
1. [Опасные API вставки HTML](#1-опасные-api-вставки-html)
2. [DOM-based XSS](#2-dom-based-xss)
3. [Stored / Reflected XSS](#3-stored--reflected-xss)
4. [Content Security Policy](#4-content-security-policy)
5. [Clickjacking и связанное](#5-clickjacking-и-связанное)
6. [Санитизация внешнего контента](#6-санитизация-внешнего-контента)

---

## 1. Опасные API вставки HTML

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| XSS-1.1 | `dangerouslySetInnerHTML` | 🔴 | React с user input внутри | Только sanitized (DOMPurify) или статичный контент |
| XSS-1.2 | `innerHTML`/`outerHTML` | 🟠 | Прямое присваивание с данными | `textContent` / фреймворк-DOM |
| XSS-1.3 | `document.write` | 🟠 | Запись динамики в документ | Убрать |
| XSS-1.4 | `v-html` (Vue) / `[innerHTML]` (Angular) | 🟠 | Байндинг сырого HTML | Санитайзер / избегать |
| XSS-1.5 | `eval`/`new Function` | 🔴 | Выполнение строк | Убрать полностью |
| XSS-1.6 | jQuery `.html()`/`.append()` | 🟠 | С недоверенными данными | `.text()` / экранирование |
| XSS-1.7 | `insertAdjacentHTML` | 🟠 | С user input | Экранирование/санитизация |

---

## 2. DOM-based XSS

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| XSS-2.1 | URL-параметры в DOM | 🟠 | `location.search`/`hash` → innerHTML | Санитизация перед вставкой |
| XSS-2.2 | `location`/`document.referrer` | 🟠 | Источники в опасные sink | Валидация/экранирование |
| XSS-2.3 | `postMessage` handler | 🟠 | `onmessage` без проверки `origin` | Проверять `event.origin` + схему данных |
| XSS-2.4 | `window.name`, storage | 🟡 | Данные из storage в DOM | Обрабатывать как недоверенные |
| XSS-2.5 | Открытый редирект → XSS | 🟠 | `javascript:` в href/redirect | Только `http(s)`, whitelist |
| XSS-2.6 | Templating на клиенте | 🟠 | Клиентские шаблоны с сырыми данными | Автоэкранирование |

---

## 3. Stored / Reflected XSS

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| XSS-3.1 | Отражение input в HTML | 🟠 | Поисковый запрос/ошибка возвращается без экранирования | Контекстное экранирование |
| XSS-3.2 | Хранимый контент | 🔴 | Профиль/комментарий/имя с `<script>` | Санитизация на выводе |
| XSS-3.3 | Экранирование по контексту | 🟠 | HTML vs атрибут vs JS vs URL контекст | Правильный энкодер под контекст |
| XSS-3.4 | Автоэкранирование шаблонов | 🟠 | `autoescape=False`, `\|safe`, `mark_safe` | Включить autoescape, убрать `safe` с user input |
| XSS-3.5 | JSON в HTML | 🟠 | Встраивание JSON в `<script>` без экранирования `</` | Экранировать `<`, `/`, U+2028/2029 |

---

## 4. Content Security Policy

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| XSS-4.1 | CSP присутствует | 🟠 | Нет заголовка CSP | Внедрить (`middleware`/reverse-proxy) |
| XSS-4.2 | Без `unsafe-inline`/`unsafe-eval` | 🟠 | `script-src 'unsafe-inline'` сводит CSP на нет | Nonce/hash-based CSP |
| XSS-4.3 | `default-src 'self'` | 🟡 | Слишком широкие источники | Начать с `default-src 'self'` |
| XSS-4.4 | `object-src 'none'`, `base-uri` | 🟡 | Отсутствуют → обходы | Задать явно |
| XSS-4.5 | CSP report-uri | 🟢 | Нет отчётности о нарушениях | `report-to`/`report-uri` |
| XSS-4.6 | Trusted Types | 🟢 | Нет защиты DOM sink | `require-trusted-types-for 'script'` |

---

## 5. Clickjacking и связанное

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| XSS-5.1 | `X-Frame-Options` | 🟠 | Нет → clickjacking | `DENY`/`SAMEORIGIN` |
| XSS-5.2 | `frame-ancestors` (CSP) | 🟠 | Отсутствует | `frame-ancestors 'none'`/'self' |
| XSS-5.3 | `X-Content-Type-Options` | 🟡 | Нет → MIME sniffing | `nosniff` |
| XSS-5.4 | `target="_blank"` + noopener | 🟢 | Tab-napping | `rel="noopener noreferrer"` |
| XSS-5.5 | `window.open` untrusted | 🟡 | Open redirect/phishing | Валидировать URL |

> ⚠️ **Урок из практики:** на одном из доменов (API) отсутствовал `X-Frame-Options`/`frame-ancestors` → clickjacking.
> Задавай защиту от фрейминга **на всех** vhost, а не только на основном.

---

## 6. Санитизация внешнего контента

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| XSS-6.1 | CMS-контент | 🟠 | Headless CMS (напр. Contentful/WordPress) может содержать `<script>` | Санитизация на рендере |
| XSS-6.2 | Markdown → HTML | 🟠 | `marked`/`markdown` без sanitize → XSS | DOMPurify после рендера |
| XSS-6.3 | SVG контент | 🟡 | SVG с `<script>` | Санитизация SVG / не inline |
| XSS-6.4 | Rich-text editor | 🟠 | WYSIWYG-вывод без фильтрации | Allowlist тегов/атрибутов |
| XSS-6.5 | Данные третьих API | 🟡 | Ответы внешних API в DOM | Обрабатывать как недоверенные |

---

## Быстрые команды проверки

```bash
# Опасные sink
rg "dangerouslySetInnerHTML|v-html|innerHTML|outerHTML|document\.write|insertAdjacentHTML"
rg "\beval\(|new Function\(|setTimeout\(['\"]|setInterval\(['\"]"

# Небезопасное экранирование (шаблоны)
rg "\|safe|mark_safe|autoescape\s*=\s*False|\{\{\{" 

# postMessage без origin
rg "addEventListener\(['\"]message" -A5 | rg -L "origin"

# CSP / заголовки
rg -i "content-security-policy|x-frame-options|frame-ancestors" 

# Markdown/HTML без sanitize
rg "marked\(|markdownToHtml|dompurify|sanitize" 
```
