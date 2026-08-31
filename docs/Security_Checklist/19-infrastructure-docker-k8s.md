# 19 — Инфраструктура: Docker, Kubernetes, Reverse Proxy, ОС

> Безопасность развёртывания: контейнеры, оркестрация, reverse-proxy, сегментация сети, ОС/SSH,
> CI/CD. Здесь же — быстрые митигейты на прокси без передеплоя.

**Легенда:** 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low · ⚪ Info
**Стандарты:** CIS Docker / Kubernetes / Ubuntu · **CWE:** 250, 284, 306, 16, 1188

---

## Категории
1. [Docker / контейнеры](#1-docker--контейнеры)
2. [Kubernetes](#2-kubernetes)
3. [Reverse Proxy и экспозиция](#3-reverse-proxy-и-экспозиция)
4. [Сегментация сети](#4-сегментация-сети)
5. [Redis / брокеры / кэш](#5-redis--брокеры--кэш)
6. [ОС / SSH / firewall](#6-ос--ssh--firewall)
7. [CI/CD](#7-cicd)

---

## 1. Docker / контейнеры

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INF-1.1 | Non-root user | 🟡 | Контейнер под root; `C_FORCE_ROOT=true` | `USER nonroot`, отказ от root |
| INF-1.2 | `no-new-privileges` | 🟡 | Нет `security_opt` | `no-new-privileges:true` |
| INF-1.3 | `cap_drop: ALL` | 🟡 | Все capabilities | Drop ALL, добавлять точечно |
| INF-1.4 | `read_only` rootfs | 🟢 | Записываемая ФС контейнера | `read_only: true` + tmpfs |
| INF-1.5 | Лимиты ресурсов | 🟡 | Нет `mem_limit`/`cpus`/`pids_limit` → DoS | Задать лимиты |
| INF-1.6 | docker.sock не смонтирован | 🔴 | `/var/run/docker.sock` в контейнере = root на хосте | Не монтировать |
| INF-1.7 | Минимальный base image | 🟢 | Full image (git/curl в рантайме) | `-slim`/distroless |
| INF-1.8 | Секреты не в образе | 🟠 | `ENV SECRET=`, `.env` в образе | Runtime-инъекция |
| INF-1.9 | Digest-pinning образов | 🟢 | Тег без `@sha256:` (переопубликация) | Пин по digest |
| INF-1.10 | Нет `--reload`/dev в проде | 🟢 | Gunicorn `--reload`, dev-сервер | Прод-конфигурация |
| INF-1.11 | Image scanning | 🟡 | Нет Trivy/Grype в CI | Сканирование образов |
| INF-1.12 | Healthcheck | 🟢 | Нет healthcheck | Настроить |

> ⚠️ **Урок из практики:** контейнеры под root, Celery-воркеры с `C_FORCE_ROOT=true`, без `cap_drop`/limits,
> одна сеть на всё. Любой RCE в контейнере → лёгкое латеральное движение. Hardening снижает blast radius.

---

## 2. Kubernetes

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INF-2.1 | Pod Security Standards | 🟠 | `privileged`, root, writable rootfs | `runAsNonRoot`, `readOnlyRootFilesystem`, `drop: [ALL]` |
| INF-2.2 | RBAC минимальный | 🟠 | `cluster-admin` для приложения | ServiceAccount с минимальными правами |
| INF-2.3 | Secrets не в ConfigMap | 🔴 | Секреты в ConfigMap/plaintext | Secrets/vault/external-secrets |
| INF-2.4 | Network Policies | 🟡 | Поды видят друг друга свободно | Default-deny + явные правила |
| INF-2.5 | Ingress TLS | 🟠 | HTTP без редиректа | HTTPS-only, redirect |
| INF-2.6 | Resource limits | 🟡 | Нет requests/limits | Задать |
| INF-2.7 | Image pull policy | 🟢 | Stale cached image | `Always` в проде |
| INF-2.8 | Runtime security | 🟡 | Нет Falco/Sysdig | Мониторинг аномалий |
| INF-2.9 | Secrets encryption at-rest (etcd) | 🟠 | etcd без шифрования | EncryptionConfiguration |

---

## 3. Reverse Proxy и экспозиция

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INF-3.1 | Служебные пути закрыты снаружи | 🔴 | `/service/*`, `/webhook`, `/internal`, `/metrics` публичны | Блок пути на прокси (только внутр. сеть) |
| INF-3.2 | Admin по IP-allowlist/VPN | 🟠 | Admin-панель из всего интернета | IP allowlist / VPN / mTLS |
| INF-3.3 | Security headers на всех vhost | 🟡 | Заголовки неравномерны | Общий snippet (см. [08](./08-csrf-cors-headers.md)) |
| INF-3.4 | Актуальная версия прокси | 🟠 | Уязвимая версия (smuggling) | Обновление образа прокси |
| INF-3.5 | Тестовые vhost закрыты | 🟡 | Тестовый поддомен (напр. `staging.`) публичен | Убрать/закрыть по IP |
| INF-3.6 | Проксирование без фильтрации | 🟠 | `reverse_proxy` на всё без разбора путей | Явные `handle`-блоки |
| INF-3.7 | Request size limits | 🟡 | Нет `client_max_body_size` | Задать лимит |

> 💡 **Быстрый митигейт без передеплоя (Caddy):** служебные пути нужны только внутренним демонам,
> которые ходят к приложению по внутренней сети мимо прокси. Значит их можно резать снаружи:
> ```caddy
> @internal path_regexp ^/(internal|service|webhook)/
> handle @internal { respond 404 }
> handle { reverse_proxy app:5000 }
> ```
> `caddy reload` без простоя. Так закрывается критичная эксплуатация через веб-периметр мгновенно.

---

## 4. Сегментация сети

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INF-4.1 | БД/брокер не публичны | 🟠 | Порт БД/Redis на `0.0.0.0` | `expose`, не `ports`; проверить `ss -tulpn` |
| INF-4.2 | Отдельные сети | 🟡 | Одна сеть на web/db/broker/adapters | Изолированные internal-сети |
| INF-4.3 | Egress filtering | 🟡 | Контейнер ходит куда угодно (SSRF-усиление) | Ограничить исходящие |
| INF-4.4 | Внутр. сервисы с auth | 🟠 | Нет auth внутри сети | Zero-trust, auth даже внутри |
| INF-4.5 | Firewall default-deny | 🟠 | Открыты лишние порты | Только 80/443/SSH наружу |

---

## 5. Redis / брокеры / кэш

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INF-5.1 | Redis с паролем | 🟠 | `requirepass` не задан | Сильный пароль + `protected-mode yes` |
| INF-5.2 | Celery не pickle | 🔴 | pickle + Redis без auth = RCE (root!) | `json`-сериализатор |
| INF-5.3 | Брокер не публичен | 🟠 | Порт брокера доступен | Внутренняя сеть |
| INF-5.4 | TLS для брокера | 🟢 | Плейнтекст между хостами | TLS при разнесении |
| INF-5.5 | Отдельная сеть брокера | 🟢 | Брокер в общей сети | Изоляция |

> ⚠️ **Урок из практики:** Redis без `requirepass`/`protected-mode`, Celery-воркеры root. Если брокер на
> pickle — запись в Redis = RCE с root. Двойная защита: пароль на Redis + json-сериализатор.

---

## 6. ОС / SSH / firewall

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INF-6.1 | SSH ключи-only | 🟠 | `PasswordAuthentication yes` | `no` — только ключи |
| INF-6.2 | Запрет root-логина | 🟠 | `PermitRootLogin yes` | `no` (или `prohibit-password`) |
| INF-6.3 | fail2ban | 🟡 | Нет защиты от SSH-брутфорса | fail2ban jail sshd |
| INF-6.4 | Auto-updates безопасности | 🟡 | `unattended-upgrades` выключен | Включить security-обновления |
| INF-6.5 | Firewall | 🟠 | Открытые порты | ufw/nftables default-deny |
| INF-6.6 | NTP sync | 🟢 | Время рассинхронизировано (TOTP/подтверждения) | Синхронизация времени |
| INF-6.7 | Актуальность ядра/ОС | 🟡 | Устаревшая ОС | Регулярные обновления |

---

## 7. CI/CD

| # | Проверка | Крит. | Что искать / вектор | Как исправить |
|---|----------|-------|---------------------|---------------|
| INF-7.1 | Изоляция pipeline | 🔴 | PR-pipeline имеет write к проду | Отдельные credentials для deploy |
| INF-7.2 | Секреты не в логах CI | 🟠 | `--build-arg SECRET=`, echo секретов | Защищённые переменные/masking |
| INF-7.3 | Least privilege токенов | 🟠 | CI-токен с избыточными правами | Минимальный scope |
| INF-7.4 | Подпись артефактов | 🟢 | Неверифицируемые артефакты | Signing / provenance (SLSA) |
| INF-7.5 | Protected branches | 🟡 | Прямой push в main без review | Protected + required reviews |
| INF-7.6 | Security-гейты в CI | 🟡 | Нет SAST/SCA/secrets-scan | Встроить (см. [26](./26-automated-tooling.md)) |

---

## Быстрые команды проверки (read-only)

```bash
# Экспозиция портов
ss -tulpn                              # что слушает; БД/Redis не должны быть на 0.0.0.0
ss -tulpn | grep -E '6379|3306|5432|27017'

# Docker hardening
docker ps
for c in $(docker ps --format '{{.Names}}'); do
  docker inspect "$c" --format '{{.Name}} Privileged={{.HostConfig.Privileged}} User={{.Config.User}} CapAdd={{.HostConfig.CapAdd}} RO={{.HostConfig.ReadonlyRootfs}}'
done
docker ps -q | xargs -r docker inspect --format '{{.Name}} {{range .Mounts}}{{.Source}} {{end}}' | grep -i docker.sock

# Redis без пароля
docker exec <redis> redis-cli CONFIG GET requirepass

# SSH/firewall/ОС
grep -vE '^\s*#|^\s*$' /etc/ssh/sshd_config
sudo nft list ruleset || sudo iptables -S; ufw status verbose
systemctl status unattended-upgrades --no-pager; timedatectl

# Compose эффективная конфигурация
docker compose config
```
