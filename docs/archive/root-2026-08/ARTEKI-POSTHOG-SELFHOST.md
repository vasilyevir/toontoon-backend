# ARTEKI — PostHog self-host (аналитика продукта)

Продуктовая аналитика (ретеншн, воронки, session replay) на **своём** инстансе
PostHog — данные не уходят третьим лицам, комплаенс проще. Ниже: как поднять на
`193.149.190.155` и что прислать для подключения приложения.

Статус: инстанс поднимает владелец сервера. Клиентская сторона (адаптер +
конфиг) готовится по этому доку; активируется, когда придёт ключ `phc_…` + host.

---

## 1. Поднять PostHog (Docker Compose)

Требования: Docker + docker-compose, ~4 ГБ RAM свободно, поддомен/порт.

```bash
# на сервере
git clone https://github.com/PostHog/posthog.git
cd posthog
# продовый compose (Postgres + ClickHouse + Redis + web)
cp .env.example .env        # выставить POSTHOG_SECRET, домены
docker compose -f docker-compose.hobby.yml up -d
```
Официальный гайд self-host: https://posthog.com/docs/self-host

**Важно для ARTEKI:**
- Повесить за nginx на HTTPS-хост (напр. `https://analytics.arteki.internal`
  или порт, доступный из приложения). У нас пиннинг только на API-хост, поэтому
  PostHog-хост должен иметь валидный TLS **или** отдельный пиннинг (см. §4).
- Ограничить доступ к дашборду PostHog (basic-auth/файрвол) — это админка.

---

## 2. Что прислать для подключения приложения

После запуска, в PostHog → Project settings:
1. **Project API Key** — строка вида `phc_…` (это ingestion-ключ, не секрет).
2. **API Host** — URL инстанса (напр. `https://analytics.example.com`).

Пришли эти два значения — я впишу их в приложение (§3).

---

## 3. Клиент — как это встроится (готовим заранее)

По образцу уже готовых адаптеров (`TelemetryDeckClient`, `SentryMonitoring`):
- `Config.xcconfig`: `ARTEKI_POSTHOG_KEY = phc_…`, `ARTEKI_POSTHOG_HOST = https:/$()/…`
  (пусто → PostHog выключен, события идут на наш `/api/events`).
- `App/AppConfig.swift`: поля `posthogKey` / `posthogHost` из Info.plist.
- `Analytics/PostHogClient.swift`: реализация `AnalyticsClient`, обёрнутая в
  `#if canImport(PostHog)` — добавляется SPM-пакетом `posthog-ios`.
- Подключение — **только после согласия** (`ConsentManager`), как и остальная
  аналитика: без PII, события из каталога `AnalyticsEvent` (snake_case).

Session replay включать осознанно: маскировать поля ввода (пароль/почта) —
PostHog это умеет (`maskAllInputs`), обязательно включить.

---

## 4. Сеть/пиннинг (не забыть)

Приложение пиннит TLS ТОЛЬКО для API-хоста (`193.149.190.155`). PostHog SDK
ходит на свой host отдельным клиентом (не через наш `APIClient`), поэтому:
- дать PostHog-хосту **валидный сертификат** (Let's Encrypt на поддомене), или
- добавить отдельный пиннинг для PostHog-хоста, или
- (dev) временно разрешить ATS-исключение для PostHog-хоста в `Info.plist`.

---

## 5. Приоритет

PostHog — «вкусные» продуктовые метрики (ретеншн/воронки/replay). Не блокирует
релиз: пока не подключён, аналитика уже собирается через наш `/api/events`
(`ServerAnalyticsClient`). Подключаем, как только придут `phc_…` + host.
