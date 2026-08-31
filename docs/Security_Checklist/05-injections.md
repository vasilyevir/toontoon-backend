# 05 — Инъекции (SQL, NoSQL, Command, LDAP, SSTI, XXE, Deserialization)

> Все классы инъекций: когда недоверенные данные попадают в интерпретатор (SQL, shell, шаблон,
> парсер) как код. Одна инъекция часто = RCE или дамп всей БД.

**Легенда:** 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low · ⚪ Info
**OWASP:** A03:2021 (Injection) · **CWE:** 89, 78, 90, 94, 502, 611, 1336, 943

---

## Категории
1. [SQL Injection](#1-sql-injection)
2. [NoSQL Injection](#2-nosql-injection)
3. [Command Injection](#3-command-injection)
4. [Небезопасная десериализация](#4-небезопасная-десериализация)
5. [Template Injection (SSTI)](#5-template-injection-ssti)
6. [XXE (XML External Entity)](#6-xxe-xml-external-entity)
7. [LDAP / прочие инъекции](#7-ldap--прочие-инъекции)
8. [Code Injection (eval/exec)](#8-code-injection-evalexec)

---

## 1. SQL Injection

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INJ-1.1 | Raw SQL с конкатенацией | 🔴 | `.raw()`, `.extra()`, `RawSQL()`, f-string в SQL | Параметризованные запросы / ORM |
| INJ-1.2 | Строковая интерполяция | 🔴 | `f"SELECT ... WHERE id={user_id}"`, `%`-форматирование | Bind-параметры (`?`, `%s`, named) |
| INJ-1.3 | `text()` без bindparams | 🟠 | SQLAlchemy `text(f"...")` | `text("... :p").bindparams(p=...)` |
| INJ-1.4 | ORDER BY / column injection | 🟠 | `?ordering=<колонка>` подставляется в SQL | Whitelist разрешённых колонок |
| INJ-1.5 | LIKE / wildcard injection | 🟡 | Неэкранированные `%`/`_` в LIKE | Экранировать спецсимволы LIKE |
| INJ-1.6 | Stored procedures | 🟠 | Динамический SQL внутри процедур | Параметризация и внутри БД |
| INJ-1.7 | Второго порядка (stored) | 🟠 | Сохранённые данные потом идут в raw SQL | Параметризация на всех путях |
| INJ-1.8 | Миграции с user input | 🟡 | `RunSQL`/`RunPython` с внешними данными | Ревью миграций, без user-controlled |

---

## 2. NoSQL Injection

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INJ-2.1 | Оператор-инъекция (Mongo) | 🔴 | `{"user": req.body.user}` где body = `{"$ne": null}` | Приводить типы, запрещать `$`-ключи от клиента |
| INJ-2.2 | `$where` / JS-выполнение | 🔴 | `$where` с пользовательской строкой | Запретить `$where`, серверный JS |
| INJ-2.3 | Тип входных данных | 🟠 | Строка ожидается, приходит объект/массив | Строгая схема (валидатор) |
| INJ-2.4 | Redis/поиск-инъекции | 🟡 | Неэкранированные команды/паттерны | Санитизация, параметризация клиента |

---

## 3. Command Injection

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INJ-3.1 | `shell=True` с user input | 🔴 | `subprocess.run(cmd, shell=True)` с внешними данными | `shell=False` + список аргументов |
| INJ-3.2 | `os.system` / `popen` | 🔴 | `os.system(f"... {user}")` | Не использовать; аргументы списком |
| INJ-3.3 | `eval`/backticks в shell (JS) | 🔴 | `child_process.exec(userInput)` | `execFile`/`spawn` с массивом аргументов |
| INJ-3.4 | Argument injection | 🟠 | Пользователь управляет флагами команды (`--output`) | Валидация/`--` разделитель |
| INJ-3.5 | Path в командах | 🟠 | Пользовательский путь без sanitization | Абсолютные пути, allowlist |

---

## 4. Небезопасная десериализация

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INJ-4.1 | `pickle.loads` недоверенного | 🔴 | `pickle`/`cPickle` на внешних данных → RCE | JSON; никогда pickle недоверенного |
| INJ-4.2 | `yaml.load` без SafeLoader | 🔴 | `yaml.load(data)` → выполнение объектов | `yaml.safe_load` |
| INJ-4.3 | Celery/broker сериализатор | 🔴 | `task_serializer=pickle` + Redis без auth → RCE | `json` сериализатор, `accept_content=['json']` |
| INJ-4.4 | Java/PHP/Ruby десериализация | 🔴 | `ObjectInputStream`, `unserialize`, `Marshal.load` | Безопасные форматы, allowlist классов |
| INJ-4.5 | `marshal`/`shelve`/`jsonpickle` | 🟠 | Небезопасные форматы на внешних данных | Строгий JSON со схемой |
| INJ-4.6 | Node `serialize-javascript`/vm | 🟠 | `eval`-подобная десериализация | Валидируемый JSON |

> ⚠️ **Урок из практики:** Celery-воркеры (root, `C_FORCE_ROOT`) + Redis без пароля. Если брокер использует
> pickle — запись в Redis = **RCE с root**. Всегда `json`-сериализатор + пароль на Redis.

---

## 5. Template Injection (SSTI)

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INJ-5.1 | `render_template_string` с input | 🔴 | Jinja/Twig/Freemarker из user input → RCE | Не рендерить пользовательские шаблоны |
| INJ-5.2 | Автоэкранирование включено | 🟠 | `autoescape=False` | Включить autoescape |
| INJ-5.3 | Пользовательские шаблоны | 🟠 | Юзер загружает шаблон письма/страницы | Sandbox / логика-only переменные |
| INJ-5.4 | Форматные строки | 🟡 | `"{}".format(user)` с доступом к атрибутам | Не форматировать недоверенным |

---

## 6. XXE (XML External Entity)

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INJ-6.1 | Внешние entity отключены | 🔴 | Парсер XML резолвит `<!ENTITY ... SYSTEM>` → LFI/SSRF | `resolve_entities=False`, defusedxml |
| INJ-6.2 | DTD отключён | 🟠 | Загрузка внешних DTD | Запретить DTD |
| INJ-6.3 | Billion laughs | 🟡 | Рекурсивные entity → DoS | Лимиты парсера / defusedxml |
| INJ-6.4 | SVG/DOCX/XML upload | 🟠 | Загружаемые XML-форматы с XXE | Безопасный парсинг, см. [12](./12-file-upload.md) |

---

## 7. LDAP / прочие инъекции

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INJ-7.1 | LDAP injection | 🟠 | Неэкранированный фильтр `(uid={user})` | Экранирование по RFC 4515 |
| INJ-7.2 | Header/CRLF injection | 🟠 | `\r\n` в заголовках/логах → splitting | Удалять CR/LF из значений |
| INJ-7.3 | Log injection | 🟡 | User input в логах без экранирования | Sanitize перед логированием |
| INJ-7.4 | Email header injection | 🟠 | `\n` в теме/To → доп. заголовки | Валидация email-полей |
| INJ-7.5 | XPath injection | 🟡 | User input в XPath | Параметризация XPath |
| INJ-7.6 | GraphQL/ORM filter injection | 🟡 | Произвольные фильтры из клиента | Whitelist полей/операторов |

---

## 8. Code Injection (eval/exec)

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INJ-8.1 | `eval`/`exec` (Python) | 🔴 | `eval(user_input)` → RCE | Не использовать; `ast.literal_eval` для литералов |
| INJ-8.2 | `eval`/`new Function` (JS) | 🔴 | Динамическое выполнение строк | Убрать полностью |
| INJ-8.3 | Динамический import | 🟠 | `__import__(user)`, `importlib` из input | Whitelist модулей |
| INJ-8.4 | `getattr`/`setattr` из input | 🟠 | Доступ к произвольным атрибутам | Whitelist имён |

---

## Быстрые команды проверки

```bash
# SQL
rg "\.raw\(|\.extra\(|RawSQL\(|cursor\.execute\(.*%|text\(f?['\"]" --type py
rg "f\"SELECT|f\"INSERT|f\"UPDATE|f\"DELETE|\" \+ .*(SELECT|WHERE)" 

# Command injection
rg "subprocess\.(call|run|Popen)\(.*shell=True|os\.system\(|os\.popen\(" --type py
rg "child_process\.(exec|execSync)\(" 

# Десериализация
rg "pickle\.loads|yaml\.load\(|marshal\.loads|jsonpickle" --type py
rg "unserialize\(|ObjectInputStream|Marshal\.load"
rg -i "task_serializer.*pickle|accept_content.*pickle"

# SSTI / eval
rg "render_template_string|autoescape\s*=\s*False" --type py
rg "\beval\(|new Function\(|exec\(" 

# XXE
rg "etree\.(parse|fromstring)|xml\.dom|SAXParser|DocumentBuilder" 
```
