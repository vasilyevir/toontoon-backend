# 15 — Блокчейн, смарт-контракты и Web3-интеграция

> Атаки на уровне блокчейна и взаимодействия с ним: reentrancy, MEV/front-running, oracle
> manipulation, flash loans, взаимодействие backend↔контракт, ноды/RPC. Дополняет [14](./14-cryptography-signatures.md).

**Легенда:** 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low · ⚪ Info
**Стандарты:** SWC Registry, EIP-712/2/155 · **CWE:** 362, 682, 841

---

## Категории
1. [Атаки на уровне контракта](#1-атаки-на-уровне-контракта)
2. [MEV / front-running / mempool](#2-mev--front-running--mempool)
3. [Oracle и цены](#3-oracle-и-цены)
4. [Смарт-контракт ABI и привилегии](#4-смарт-контракт-abi-и-привилегии)
5. [Ноды и RPC](#5-ноды-и-rpc)
6. [Backend ↔ блокчейн](#6-backend--блокчейн)

---

## 1. Атаки на уровне контракта

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| BC-1.1 | Reentrancy | 🔴 | Внешний вызов до обновления состояния | Checks-Effects-Interactions, `nonReentrant` |
| BC-1.2 | Integer over/underflow | 🟠 | Арифметика без проверок (Solidity < 0.8) | SafeMath / Solidity ≥ 0.8 |
| BC-1.3 | Signature malleability | 🟡 | `s` не канонический (EIP-2) | Каноническая проверка / OZ ECDSA |
| BC-1.4 | Deadline/expiry в подписи | 🟠 | Подпись живёт вечно | `block.timestamp <= deadline` |
| BC-1.5 | Chain ID replay | 🟠 | Cross-chain replay | EIP-155 / domain separator с chainId |
| BC-1.6 | Access control функций | 🔴 | `onlyOwner`/роли отсутствуют на критичных | Строгие модификаторы |
| BC-1.7 | Gas griefing / DoS | 🟡 | Return data bomb, unbounded loop | Лимиты, pull-over-push |
| BC-1.8 | Delegatecall/proxy | 🟠 | Небезопасный delegatecall, storage collision | Аудит proxy-паттерна |
| BC-1.9 | Unchecked external call | 🟠 | Игнор возврата `call`/`transfer` | Проверять результат |

---

## 2. MEV / front-running / mempool

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| BC-2.1 | Front-running подписи | 🟠 | MEV-бот перехватывает pending tx с подписью | Private mempool / commit-reveal |
| BC-2.2 | Sandwich-атаки | 🟡 | Свопы без slippage-защиты | Min output / slippage tolerance |
| BC-2.3 | Flash loan manipulation | 🟠 | За одну tx набить объём/цену | TWAP, block-delay, снапшоты |
| BC-2.4 | Транзакция без slippage | 🟡 | Нет min amount out | Обязательный slippage |

---

## 3. Oracle и цены

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| BC-3.1 | Oracle manipulation | 🟠 | Цена из spot DEX в одном блоке | Chainlink / TWAP |
| BC-3.2 | Stale price | 🟡 | Устаревшие данные оракула | Проверка `updatedAt`/heartbeat |
| BC-3.3 | Один источник цены | 🟠 | Нет агрегации/санити | Несколько источников, отклонение → алерт |
| BC-3.4 | Off-chain курсы | 🟠 | Курс с одной биржи без границ | См. [13](./13-payments-webhooks.md) §6 |

---

## 4. Смарт-контракт ABI и привилегии

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| BC-4.1 | ABI совпадает с deployed | 🟠 | ABI в проекте ≠ on-chain | Сверить с развёрнутым |
| BC-4.2 | Owner-only функции | 🟠 | `setAuthorizedSigner`, `setVault`, `transferOwnership` | Кто owner? Multisig? |
| BC-4.3 | Лимиты в контракте | 🟡 | Нет max claim/daily limit/pause | Лимиты + pause-механизм |
| BC-4.4 | Pause/emergency stop | 🟠 | Нельзя заморозить при инциденте | `pause()` для остановки |
| BC-4.5 | Upgrade-механизм | 🟠 | Кто может апгрейдить контракт | Timelock + multisig |
| BC-4.6 | Централизация власти | 🟡 | EOA-owner с полным контролем | Multisig/timelock/governance |

---

## 5. Ноды и RPC

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| BC-5.1 | Актуальная версия ноды | 🟠 | Устаревшая нода (CVE, напр. BTC < 0.17) | Обновление ноды |
| BC-5.2 | RPC-токены не в коде/URL | 🟠 | `https://.../TOKEN`, `user:pass@` в URL | Секреты из vault |
| BC-5.3 | RPC не публичен | 🟠 | Открытый RPC-эндпоинт | Auth + сеть |
| BC-5.4 | Резервный RPC | 🟢 | Один провайдер = single point | Fallback-провайдеры |
| BC-5.5 | Rate limit RPC | 🟢 | Исчерпание квоты провайдера | Кэш + лимиты |

---

## 6. Backend ↔ блокчейн

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| BC-6.1 | Верификация tx перед зачислением | 🔴 | Доверие вебхуку/клиенту без проверки on-chain | `getTransactionReceipt`/`account_tx` |
| BC-6.2 | Число подтверждений | 🟠 | Малое число confirmations | Адекватное по сети |
| BC-6.3 | Reorg handling | 🟠 | Нет отката при реорганизации | Мониторинг + компенсация |
| BC-6.4 | Идемпотентность по txid | 🟠 | Повтор зачисления по одному tx | UNIQUE по txid |
| BC-6.5 | Nonce из контракта | 🟠 | Nonce из БД/клиента | Читать из контракта |
| BC-6.6 | Валидация адресов | 🟠 | Некорректный/подменённый адрес | Checksum-валидация |
| BC-6.7 | Сканер событий надёжен | 🟡 | Пропуск/дубли событий | Курсор + идемпотентность |

> ⚠️ **Урок из практики:** служебный вебхук уведомления о транзакции для части сетей всегда возвращал success без верификации txid →
> инъекция фейковых платежей (Critical). Backend **обязан** независимо верифицировать транзакцию в
> блокчейне, а не доверять уведомлению.

---

## Быстрые команды проверки

```bash
# Взаимодействие с контрактом
rg -i "abi\.encode|encodePacked|keccak256|ecrecover|signMessage|eth_sign" 
rg -i "getTransactionReceipt|account_tx|getaddrbytx|confirmations|block_number"

# RPC/ноды
rg -i "fullnode_url|rpc_url|infura|alchemy|getblock|quicknode|trongrid" 
rg -i "https?://[^/]*:[^@]*@"    # креды в URL

# Reorg/idempotency
rg -i "reorg|orphan|reorganiz|txid.*unique|unique.*txid"

# Nonce
rg -i "nonce" --type py | rg -i "request|client|body"    # nonce от клиента — плохо
```

> Для аудита самих контрактов on-chain используй специализированные инструменты: Slither, Mythril,
> Echidna, Foundry (fuzzing). Этот файл покрывает **взаимодействие**, а не построчный аудит Solidity.
