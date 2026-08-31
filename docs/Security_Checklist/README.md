# 🛡️ Security Checklist Library — полная библиотека чеклистов для аудита безопасности

> Максимально полный набор чеклистов для проверки любого проекта (backend, frontend, инфраструктура,
> Web3/blockchain, платёжные системы) «от и до» на все классы уязвимостей — с рекомендациями по
> исправлению, уровнями критичности и командами для проверки.
>
> **Основано на:** OWASP Top 10 (2021), OWASP API Security Top 10 (2023), OWASP ASVS 4.0, MASVS,
> CWE, CIS Benchmarks, а также на обобщённом опыте реальных пентестов и аудитов финтех- и Web3-проектов.
>
> **Версия:** 1.0 · **Язык:** RU · **Формат:** Markdown

---

## 📌 Как пользоваться этой библиотекой

Каждый файл — это самостоятельный чеклист по одной **категории** уязвимостей. Внутри файла проверки
разбиты на **подкатегории** и оформлены таблицами со столбцами:

| Столбец | Значение |
|---------|----------|
| **#** | Уникальный номер проверки (для ссылок в отчёте, напр. `AUTH-2.1.5`) |
| **Проверка** | Что проверяем |
| **Критичность** | Максимальная серьёзность при провале (см. легенду ниже) |
| **Что искать / вектор** | Конкретные паттерны кода, конфиги, признаки уязвимости |
| **Как исправить** | Рекомендация с примером |

### Порядок работы аудитора

1. Начни с **[00 — Методология](./00-methodology-scoping.md)**: зафиксируй scope, стек, threat model.
2. Пройди файлы по порядку или выборочно — в зависимости от стека проекта (см. матрицу применимости ниже).
3. Каждую находку оформляй по **[28 — Шаблоны отчётов](./28-report-templates.md)**.
4. Используй **[27 — Команды быстрой проверки](./27-quick-commands.md)** для grep/git/docker-разведки.
5. Приоритизируй находки: `Priority = Severity × Exploitability × Business Impact`.
6. После фиксов — re-test по процедуре из файла 00.

---

## 🚦 Легенда уровней критичности

Единая шкала для всех файлов библиотеки:

| Значок | Уровень | Определение | Пример | SLA на исправление |
|--------|---------|-------------|--------|--------------------|
| 🔴 | **Critical** | Прямая кража средств / полная компрометация системы / RCE / утечка ключей | Бэкдор для вывода средств, публичный служебный эндпоинт отдаёт ключ, RCE через десериализацию, подделка OAuth | 0–24 часа |
| 🟠 | **High** | Кража через цепочку атак, значительная утечка данных, обход аутентификации | IDOR на финансах, SSRF во внутреннюю сеть, отсутствие подписи вебхука, race condition в выплатах | 1–7 дней |
| 🟡 | **Medium** | Ослабление защиты, расширение attack surface, раскрытие информации | Нет rate limiting, user enumeration, verbose ошибки, слабый CORS | 2–4 недели |
| 🟢 | **Low** | Отклонение от best practices, минимальный риск | `console.log`, отсутствие `noopener`, source maps в проде | При удобном случае |
| ⚪ | **Info** | Гигиена, наблюдение, потенциальный риск без прямого вектора | `.bak` файлы в репо, отсутствующий мониторинг | Бэклог |

> ⚠️ **Контекст бизнеса важнее CVSS.** CVSS Medium на финансовой операции = фактически High/Critical.
> При равной критичности — **сначала то, что ближе к деньгам, ключам и PII.**

---

## 🗂️ Индекс чеклистов

### Основа
| Файл | Категория | Ключевые темы |
|------|-----------|---------------|
| [00-methodology-scoping.md](./00-methodology-scoping.md) | Методология | Scope, threat modeling (STRIDE), severity scoring, re-test, порядок аудита |

### Backend — идентификация и доступ
| Файл | Категория | Ключевые темы |
|------|-----------|---------------|
| [01-authentication.md](./01-authentication.md) | Аутентификация | JWT, OAuth, wallet-auth, пароли, MFA, brute-force |
| [02-authorization-idor.md](./02-authorization-idor.md) | Авторизация | RBAC, IDOR, privilege escalation, mass assignment |
| [03-session-management.md](./03-session-management.md) | Сессии | Cookie-флаги, инвалидация, фиксация, concurrent sessions |
| [04-secrets-management.md](./04-secrets-management.md) | Секреты | `.env`, git-история, vault/KMS, ротация, дефолтные креды |

### Backend — входные данные и инъекции
| Файл | Категория | Ключевые темы |
|------|-----------|---------------|
| [05-injections.md](./05-injections.md) | Инъекции | SQLi, NoSQLi, command, LDAP, SSTI, XXE, десериализация |
| [06-input-validation.md](./06-input-validation.md) | Валидация | Числа/Decimal, адреса, границы, тип, canonicalization |
| [11-ssrf.md](./11-ssrf.md) | SSRF | callback URL, internal-адреса, DNS rebinding, metadata |
| [12-file-upload.md](./12-file-upload.md) | Загрузка файлов | magic bytes, path traversal, SVG XSS, zip bomb |

### Backend — API и данные
| Файл | Категория | Ключевые темы |
|------|-----------|---------------|
| [09-api-security.md](./09-api-security.md) | API | Rate limiting, pagination, GraphQL, WebSocket, method override |
| [18-database-orm.md](./18-database-orm.md) | БД / ORM | `select_for_update`, atomic, least privilege, миграции |

### Frontend
| Файл | Категория | Ключевые темы |
|------|-----------|---------------|
| [07-xss.md](./07-xss.md) | XSS | `dangerouslySetInnerHTML`, `eval`, CSP, DOM-based |
| [08-csrf-cors-headers.md](./08-csrf-cors-headers.md) | CSRF/CORS/Headers | SameSite, wildcard CORS, HSTS, CSP, clickjacking |
| [10-frontend-config.md](./10-frontend-config.md) | Frontend config | `NEXT_PUBLIC_`, debug-tools, source maps, Web3-клиент |

### Деньги, крипта, Web3
| Файл | Категория | Ключевые темы |
|------|-----------|---------------|
| [13-payments-webhooks.md](./13-payments-webhooks.md) | Платежи | Вебхуки, суммы/валюта, идемпотентность, выводы средств |
| [14-cryptography-signatures.md](./14-cryptography-signatures.md) | Криптография | ECDSA-подписи, шифрование at-rest, KDF, replay |
| [15-blockchain-web3.md](./15-blockchain-web3.md) | Блокчейн | Reentrancy, MEV, chain ID, reorg, oracle, курсы |
| [16-referral-commission.md](./16-referral-commission.md) | Рефералы | Комиссии, self-referral, циклы, двойное начисление |

### Логика и данные
| Файл | Категория | Ключевые темы |
|------|-----------|---------------|
| [17-business-logic.md](./17-business-logic.md) | Бизнес-логика | State machine, TOCTOU, promo stacking, workflow bypass |
| [24-data-privacy-compliance.md](./24-data-privacy-compliance.md) | Приватность | PII, GDPR, retention, маскирование, публичные бакеты |

### Инфраструктура и эксплуатация
| Файл | Категория | Ключевые темы |
|------|-----------|---------------|
| [19-infrastructure-docker-k8s.md](./19-infrastructure-docker-k8s.md) | Инфра | Docker/K8s hardening, reverse-proxy, network policies, SSH |
| [20-cloud-security.md](./20-cloud-security.md) | Облако | S3/MinIO, IAM, metadata, KMS, secrets в CI/CD |
| [21-dependency-supply-chain.md](./21-dependency-supply-chain.md) | Supply chain | CVE, lock-файлы, typosquatting, digest-pinning, SRI |
| [22-logging-monitoring.md](./22-logging-monitoring.md) | Логи/мониторинг | Утечки в логи, алертинг, аудит-трейл, NTP |
| [23-error-handling.md](./23-error-handling.md) | Ошибки | Stack trace, 403 vs 404, verbose headers |

### Процессы и инструменты
| Файл | Категория | Ключевые темы |
|------|-----------|---------------|
| [25-incident-response.md](./25-incident-response.md) | Инциденты | Containment, forensics, recovery, post-mortem |
| [26-automated-tooling.md](./26-automated-tooling.md) | Инструменты | SAST, DAST, SCA, secrets detection, CI pipeline |
| [27-quick-commands.md](./27-quick-commands.md) | Команды | grep/rg, git-история, docker/ss/curl-разведка |
| [28-report-templates.md](./28-report-templates.md) | Шаблоны | Оформление находок, executive summary, сводные таблицы |
| [29-owasp-standards-mapping.md](./29-owasp-standards-mapping.md) | Стандарты | OWASP Top 10 / API Top 10 / ASVS / CWE ↔ файлы |

---

## 🧭 Матрица применимости по типу проекта

Выбирай файлы под свой стек — не все категории применимы к каждому проекту.

| Тип проекта | Обязательно | Дополнительно |
|-------------|-------------|---------------|
| **REST API / SaaS backend** | 00–06, 09, 11, 18, 22, 23, 27–29 | 12, 17, 21, 25, 26 |
| **SPA / Next.js frontend** | 00, 03, 07, 08, 10, 21, 29 | 11, 24 |
| **Платёжная платформа** | 00–06, 09, 11, **13**, **14**, 17, 18, 22, 25 | 15, 16, 19, 20 |
| **Web3 / DeFi / крипто-шлюз** | 00, 01, 04, **13**, **14**, **15**, 16, 18, 19, 20 | 10, 11 |
| **Инфраструктура / DevOps** | 00, 04, **19**, **20**, 21, 22, 25, 26, 27 | 23 |
| **Обработка PII / регулируемые** | 00–04, **24**, 22, 23, 25 | 12, 20 |

---

## ✅ Быстрый старт (30 минут)

Минимальный «дымовой» аудит для любого проекта:

1. **Секреты:** `04` + `27` → git-история, `.env`, захардкоженные ключи.
2. **Аутентификация:** `01` → JWT lifetime, OAuth verify, rate limiting на login.
3. **Авторизация:** `02` → IDOR на «своих» ресурсах, mass assignment.
4. **Инъекции:** `05` → raw SQL, `eval`, десериализация, command injection.
5. **Конфиг:** `08` + `10` → CORS `*`, DEBUG=True, security headers, source maps.
6. **Зависимости:** `21` → `pip-audit` / `npm audit`.
7. **Инфра:** `19` → открытые порты, дефолтные пароли БД/Redis, контейнеры под root.

Всё найденное — в отчёт по `28`.

---

## 🔑 Главные принципы (выжимка из реальных инцидентов)

1. **Деньги защищает не «никто не постучался в URL», а криптография и контроль доступа.**
   *(Пример: публичный служебный эндпоинт отдавал ключ шифрования кошельков любому, кто знал дефолтный service-key.)*
2. **Дефолтные секреты = отсутствие секретов.** `changeme`, `password123!`, `admin` — один пароль на всё.
3. **Клиент никогда не определяет цену, скидку, сумму, права.** Всё — серверная валидация из БД.
4. **Каждая финансовая операция: `atomic` + `select_for_update` + идемпотентность.**
5. **Подпись проверяется через `hmac.compare_digest` и через `raise`, а не `return`.**
6. **Служебные эндпоинты не должны быть доступны из интернета** — только из внутренней сети.
7. **Часть Critical/High чинится на reverse-proxy без передеплоя** — используй это для быстрого митигейта.
8. **Fail closed, не fail open:** нет секрета в env → падаем, а не работаем на дефолте.

---

## 📚 Внешние стандарты

- OWASP Top 10 (2021): https://owasp.org/Top10/
- OWASP API Security Top 10 (2023): https://owasp.org/API-Security/
- OWASP ASVS 4.0: https://owasp.org/www-project-application-security-verification-standard/
- OWASP MASVS (mobile): https://mas.owasp.org/
- OWASP Cheat Sheet Series: https://cheatsheetseries.owasp.org/
- CWE: https://cwe.mitre.org/
- CIS Benchmarks: https://www.cisecurity.org/cis-benchmarks
