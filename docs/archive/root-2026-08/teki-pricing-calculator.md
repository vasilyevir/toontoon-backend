# TEKI Pricing Calculator — техническое задание

> Задача для разработчика (Claude Code). Самодостаточно: здесь есть все данные,
> чтобы реализовать в один проход. Не нужно ничего «допридумывать» — спорные
> места вынесены в раздел **§9 Открытые вопросы** и должны решаться продактом,
> а не в коде.

---

## 0. TL;DR — что строим

Движок, который считает цену одной генерации по цепочке:

```
1. себестоимость в $   (COGS, по типам: image / postcard / video)
2. × 3                 (наценка, конфигурируемая)
3. курс TEKI           (парсинг с биржи azbit → базовый курс)
   ├─ unlock-курс      (для разлоченных токенов)
   └─ lock-курс        (для залоченных токенов)
4. итог в TEKI         (retailUsd / курс) — отдельно для unlock и lock
```

Два артефакта:

1. **`src/lib/tekiPricing.mjs`** — чистый движок (config + функции, без зависимостей от Next).
2. **`scripts/teki-cost.mjs`** — CLI: тянет живой курс, печатает таблицу цен.

Опциональный третий артефакт (НЕ в этой задаче, см. §1) — API-роут для UI.

---

## 1. Границы задачи

**В скоупе:**
- Конфиг себестоимости по типам генерации.
- Парсер курса TEKI/USDT с azbit + вывод двух производных курсов (unlock/lock).
- Калькулятор: COGS → ×3 → перевод в TEKI по обоим курсам.
- CLI, печатающий таблицу для всех типов по живому курсу.
- Обработка ошибок сети/недоступности биржи (fallback).

**НЕ в скоупе (отдельные задачи, не трогать):**
- Реальное списание TEKI у пользователя (флоу Boostify — уже есть отдельно).
- UI с «двумя балансами».
- Замена захардкоженных цен в коде (`src/app/preview/page.tsx`,
  `src/app/api/generations/route.ts` — там сейчас `0.40` и курс `0.70`; это
  легаси, в этой задаче не используем и не правим).

---

## 2. Источник курса — azbit (живой публичный API)

Курс берём **по API, без парсинга HTML**. Пара уже торгуется.

**Endpoint:**
```
GET https://data.azbit.com/api/tickers
```
Возвращает массив всех пар. Параметр `?market=` биржа **игнорирует** (отдаёт
полный список), поэтому фильтруем на клиенте по `currencyPairCode`.

**Реальный ответ для нашей пары (пример, снят при написании ТЗ):**
```json
{
  "timestamp": 1781798829,
  "currencyPairCode": "TEKI_USDT",
  "price": 17.64487,
  "price24hAgo": 20.42427,
  "priceChangePercentage24h": -13.61,
  "volume24h": 154265.0070469,
  "bidPrice": 17.51686,
  "askPrice": 17.8043,
  "low24h": 17.19183,
  "high24h": 20.48092
}
```

**Извлечение:**
```js
const all = await res.json();               // массив
const teki = all.find(t => t.currencyPairCode === "TEKI_USDT");
const baseRate = teki.price;                // USD за 1 TEKI (last price)
```

**Какое поле брать за базовый курс:** по умолчанию `price` (last). Заложить
конфиг-флаг `RATE_SOURCE: "last" | "bid" | "mid"`:
- `last` → `price`
- `bid` → `bidPrice` (консервативно: столько реально дают за токен при продаже)
- `mid` → `(bidPrice + askPrice) / 2`

> Рекомендация: для расчёта **себестоимости в TEKI** честнее `bid` или `mid`,
> а не оптимистичный `last`. Дефолт оставить `last`, но флаг сделать.

**Кэш:** не дёргать биржу на каждый расчёт. Кэшировать курс в памяти на
`RATE_TTL_MS` (дефолт 60_000 мс). В CLI — один запрос за запуск.

**Fallback (биржа недоступна / пара пропала / `price <= 0`):**
использовать `RATE_FALLBACK_USD` (дефолт `20`) и пометить результат
`stale: true`, чтобы вызывающий код/печать показали предупреждение.
Никогда не падать с исключением наружу из расчёта — курс деградирует, но
калькулятор работает.

---

## 3. Модель себестоимости (COGS)

Себестоимость = сумма компонентов в $. Хранить как **один конфиг**, чтобы смена
провайдера/разрешения меняла цифру в одном месте (никаких магических чисел по
коду — см. CLAUDE.md).

| Тип | Компоненты | $ |
|-----|-----------|---|
| **image** | LLM-промт (GPT-4o-mini) `$0.0002` + генерация (gpt-image-1 medium 1024×1024) `$0.042` | **≈ $0.0422** |
| **postcard** | то же, что image (текст накладывается на фронте, доп. затрат `$0`) | **≈ $0.0422** |
| **video** (5 c) | 4 кадра (Pollinations) `$0` + Seedance-2-fast 720p `$0.125–0.205/с` × `5 c` | **≈ $0.625–1.025; план $0.75** |

**Конфиг (предлагаемая структура):**
```js
export const COGS = {
  image: {
    components: [
      { label: "LLM prompt (gpt-4o-mini)", usd: 0.0002 },
      { label: "image gen (gpt-image-1 medium 1024²)", usd: 0.042 },
    ],
  },
  postcard: {
    components: [
      { label: "LLM prompt (gpt-4o-mini)", usd: 0.0002 },
      { label: "image gen (gpt-image-1 medium 1024²)", usd: 0.042 },
      { label: "text overlay (frontend)", usd: 0 },
    ],
  },
  video: {
    // параметризуем, чтобы менять длину/разрешение/референсы одним числом
    frames: { count: 4, usdEach: 0 },        // Pollinations — бесплатно
    seedance: {
      seconds: 5,
      usdPerSecond: 0.125,                    // 720p с референсами; без = 0.205
    },
  },
};
```
`computeCogsUsd(type)` суммирует компоненты (для video: `frames.count*usdEach +
seedance.seconds*seedance.usdPerSecond`).

> ⚠️ **Сверка с текущим кодом:** в проде картинки сейчас идут через
> **Pollinations flux (бесплатно)**, а не gpt-image-1. Цифры выше — это
> **целевая** экономика на платном провайдере (как договорились в расчёте).
> Цифры из конфига = источник правды; если провайдер другой — правится конфиг,
> формулы не трогаются.

---

## 4. Два курса — unlock и lock

Базовый курс с биржи один (`baseRate`). Из него считаем два **производных**
курса. Семантика (из обсуждения):

- **unlock-токены** — разлоченные, пользователь может вывести и продать на бирже.
- **lock-токены** — залоченные, внутренние, на биржу не выводятся.

**Формула (дефолтная, параметры конфигурируемы):**
```js
unlockRate = baseRate * (1 + UNLOCK_PREMIUM);  // дефолт +0.20  → "Биржа +20% Unlock"
lockRate   = baseRate * (1 - LOCK_DISCOUNT);    // дефолт −0.30  → "Биржа −30% Lock"
```
Курс — это **$ за 1 TEKI**. Чем курс ниже, тем больше токенов нужно за ту же
сумму в $ (поэтому lock-токенов на генерацию уходит больше, чем unlock).

```js
export const RATE_MODEL = {
  UNLOCK_PREMIUM: 0.20,   // +20%
  LOCK_DISCOUNT:  0.30,   // −30%
};
```

> ⚠️ Конкретные `+20% / −30%` и сама модель — **бизнес-решение, ещё не
> финализировано** (см. §9). Поэтому это конфиг, а не константы в формуле.

---

## 5. Итоговая цена

```
retailUsd      = cogsUsd * MARKUP          // MARKUP дефолт 3
priceUnlockTeki = retailUsd / unlockRate
priceLockTeki   = retailUsd / lockRate
```

```js
export const MARKUP = 3;   // «к себестоимости прибавлять ×3»
```

**Округление:** деньги — 4 знака (`$0.1266`); TEKI — настраиваемый
`TEKI_DECIMALS` (дефолт 4), плюс отдельно «человеческое» округление для UI
(`TEKI_UI_DECIMALS`, дефолт 2). Округлять в большую сторону (`Math.ceil` на
последнем знаке) — чтобы не уйти в минус по марже. Заложить флаг
`ROUND_UP: true`.

**Пример при `baseRate = 17.64`, `MARKUP = 3`, `+20% / −30%`:**
`unlockRate = 21.17`, `lockRate = 12.35`.

| Тип | COGS $ | ×3 retail $ | unlock TEKI | lock TEKI |
|-----|-------:|------------:|------------:|----------:|
| image | 0.0422 | 0.1266 | 0.0060 | 0.0103 |
| postcard | 0.0422 | 0.1266 | 0.0060 | 0.0103 |
| video | 0.7500 | 2.2500 | 0.1063 | 0.1822 |

---

## 6. Архитектура, файлы, типы

**Ограничения репо (проверено):** Next 16, TypeScript, **node v20.19.1**
(нет `tsx`/`ts-node`, нельзя гонять `.ts` напрямую). Скрипты в проекте принято
писать как **`.mjs` и запускать через `node`** (см. `scripts/generate-tile-images.mjs`).
`tsconfig` имеет `allowJs: true`, `module: esnext`, `moduleResolution: bundler`.

**Рекомендуемый вариант (без новых зависимостей): движок в `.mjs` + JSDoc-типы.**
`allowJs: true` означает, что TS-приложение сможет импортировать этот модуль с
типами (через JSDoc), а node — запускать как есть.

```
src/lib/tekiPricing.mjs     # движок: config + чистые функции + fetch курса
scripts/teki-cost.mjs       # CLI: import движка → печать таблицы
```

**Сигнатуры (JSDoc):**
```js
/** @typedef {"image"|"postcard"|"video"} GenType */

/** @typedef {{ base:number, unlock:number, lock:number,
 *              source:"last"|"bid"|"mid", stale:boolean, fetchedAt:number }} TekiRate */

/** @typedef {{ type:GenType, cogsUsd:number, retailUsd:number,
 *              unlockTeki:number, lockTeki:number }} GenPrice */

/** Сумма компонентов себестоимости. @param {GenType} type @returns {number} */
export function computeCogsUsd(type) {}

/** Тянет курс с azbit, считает unlock/lock, кэширует, fallback при ошибке.
 *  @returns {Promise<TekiRate>} */
export async function fetchTekiRate() {}

/** Из базового курса делает unlock/lock (чистая, для тестов без сети).
 *  @param {number} base @returns {{unlock:number, lock:number}} */
export function deriveRates(base) {}

/** Полный расчёт цены одного типа по готовому курсу.
 *  @param {GenType} type @param {TekiRate} rate @returns {GenPrice} */
export function priceGeneration(type, rate) {}

/** Расчёт всех типов сразу. @param {TekiRate} rate @returns {GenPrice[]} */
export function priceAll(rate) {}
```

Все настройки (`COGS`, `MARKUP`, `RATE_MODEL`, `RATE_SOURCE`, `RATE_TTL_MS`,
`RATE_FALLBACK_USD`, округления) — экспортируемые константы вверху файла.

**Альтернатива (если хотим строгий TS):** движок в `src/lib/tekiPricing.ts`,
CLI через `npx tsx` (добавить `tsx` в `devDependencies` и скрипт
`"teki:cost": "tsx scripts/teki-cost.ts"`). Минус — новая зависимость. По
умолчанию выбираем `.mjs`-вариант выше.

---

## 7. CLI

**Запуск (по конвенции репо):**
```bash
export PATH="/Users/zhenyashe/XCloude/Tools/node/bin:$PATH"
cd /Users/zhenyashe/XCloude/arteki
node scripts/teki-cost.mjs
```

**Пример вывода (формат — ориентир):**
```
TEKI rate (azbit, last)  $17.64  ▼13.6% 24h   [live]
  unlock (+20%)  $21.17 / TEKI
  lock   (−30%)  $12.35 / TEKI

Тип       COGS $    ×3 $     unlock TEKI   lock TEKI
image     0.0422    0.1266   0.0060        0.0103
postcard  0.0422    0.1266   0.0060        0.0103
video     0.7500    2.2500   0.1063        0.1822
```
При fallback вместо `[live]` печатать `[STALE — биржа недоступна, курс=20.00]`.

Желательные флаги CLI: `--rate=<num>` (зафиксировать курс вручную, без сети),
`--markup=<num>`, `--json` (машинный вывод).

---

## 8. Edge cases и ошибки

- Биржа недоступна / таймаут (поставить `AbortSignal.timeout(8000)`) → fallback-курс, `stale:true`, не падать.
- Пары `TEKI_USDT` нет в ответе → fallback, `stale:true`.
- `price <= 0` или `NaN` → fallback.
- Деление на ноль (`unlockRate`/`lockRate` == 0) → защититься, отдать `Infinity`-safe значение или fallback-курс.
- Неизвестный `type` → бросить понятную ошибку (это баг вызова, не runtime-данные).
- Курс не кэшировать «навечно»: уважать `RATE_TTL_MS`.

---

## 9. Открытые вопросы (решает продакт, НЕ кодом)

1. **Конфликт двух моделей цены.** Текущая спека = **cost-plus ×3**:
   картинка ≈ `$0.13`, видео ≈ `$2.25`. Раньше звучала **другая** модель —
   «1 генерация = 0.25 unlock-TEKI = $5» (привязка к бирже, не к себестоимости).
   Разница для картинки ~×40. Нужно выбрать одно из:
   - чистый cost-plus ×3 (как сейчас в ТЗ);
   - cost-plus, но с **минимальной ценой** (`PRICE_FLOOR_USD`, напр. $1–5);
   - фиксированная цена в TEKI (0.25 unlock / 5 lock), себестоимость — только для контроля маржи.
   > Движок должен поддержать любой: заложить опциональный `PRICE_FLOOR_USD` и
   > возможность задать фикс-цену в TEKI поверх расчёта.
2. **Точные `UNLOCK_PREMIUM` / `LOCK_DISCOUNT`** (`+20% / −30%` — черновик).
3. **Какое биржевое поле** считать курсом для списания (`last` / `bid` / `mid`).
4. **Округление TEKI для пользователя** (сколько знаков, всегда вверх?).
5. **Видео:** считать по `$0.125/с` (с референсами) или `$0.205/с` (без)? И длина ролика (5 c сейчас).

---

## 10. Чеклист приёмки

- [ ] `node scripts/teki-cost.mjs` печатает таблицу по **живому** курсу с azbit.
- [ ] При выключенной сети скрипт не падает, печатает `[STALE]` и fallback-курс.
- [ ] `computeCogsUsd("image") === 0.0422`, `computeCogsUsd("video") === 0.75` (при дефолтном конфиге).
- [ ] `deriveRates(20)` → `{ unlock: 24, lock: 14 }` (при `+20% / −30%`).
- [ ] `priceGeneration` корректно делит `retailUsd` на оба курса; маржа не уходит в минус из-за округления (округление вверх).
- [ ] Все числа — из конфига, по коду нет «магических» значений.
- [ ] `--rate=20 --markup=3 --json` отдаёт валидный JSON для всех типов.
- [ ] `npm run lint` чистый.
- [ ] Краткий отчёт по формату из `arteki/CLAUDE.md` (Что сделано / Что изменено / Проверка / Риски).

---

## 11. Контекст репозитория (чтобы не переоткрывать)

- Экономика/конфиги живут в `src/lib/` (`tileConfig.ts` — у тайлов уже есть `priceTeki`).
- Текущий мок-топап: `src/app/api/teki/topup/route.ts` (`MOCK_AMOUNT = 3`, «until Boostify integration»).
- Захардкоженные легаси-цены (НЕ источник правды): `src/app/preview/page.tsx`
  (`price = 0.40`, `tekiPrice = price / 0.70`), `src/app/api/generations/route.ts` (`costUsd: 0.40`).
- Логин «через Boostify» уже есть: `src/app/login/page.tsx`. Само списание
  unlock/lock — на стороне Boostify (в этом репо его нет).
- Деплой: `vercel --prod` (см. `CLAUDE.md`). Эта задача деплоя не требует — это внутренний инструмент.
