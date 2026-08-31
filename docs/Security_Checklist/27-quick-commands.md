# 27 — Команды быстрой проверки (grep / git / docker / curl)

> Готовые команды для быстрой разведки при аудите. Все команды — неразрушающие (read-only /
> диагностика). Используй `rg` (ripgrep) вместо `grep` — быстрее.

**Легенда:** 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low · ⚪ Info

---

## 1. Секреты и git-история

```bash
# .env когда-либо коммитился
git log --all --diff-filter=A --name-only -- '*.env*'

# Hex-ключи в истории
git log --all -p -G "0x[0-9a-fA-F]{40,}" -- '*.py' '*.env*' '*.yaml' '*.ts'

# Присвоение секретных переменных в истории
git log --all -p -G "(SIGNER_KEY|SECRET_KEY|PRIVATE_KEY|API_KEY)\s*=" | grep -E "^\+.*="

# Секреты в текущем коде
rg "(sk_|pk_|AKIA|ghp_|xox[baprs]-|AIza)[0-9A-Za-z_\-]{10,}"
rg -i "(password|secret|token|api_key|private_key)\s*[:=]\s*['\"][^'\"]{6,}"
rg "0x[0-9a-fA-F]{40,}"

# Дефолтные секреты
rg -i "['\"](dev|changeme|password123|admin|rootpassword|secret|test)['\"]"

# Автоинструменты
gitleaks detect --source . --no-banner
trufflehog git file://. --only-verified
```

## 2. Скрытые эндпоинты и отключённые проверки

```bash
# Скрытые роуты
rg "include_in_schema\s*=\s*False" --type py
rg "auth\s*=\s*None|permission_classes\s*=\s*\[\s*AllowAny" --type py

# Отключённые проверки
rg "verify_signature.*False|verify\s*=\s*False|validate\s*=\s*False|check\s*=\s*False" --type py

# Подозрительные комментарии
rg -i "TODO|FIXME|HACK|BYPASS|no validation|temporarily|disable|backdoor" 

# Бэкдор-паттерны
rg -i "/private/|/internal/|claim.?signature|debug.?endpoint"
```

## 3. Аутентификация и авторизация

```bash
# is_active не проверяется
rg -A10 "def get_user_from_token|def authenticate" --type py | rg -v "is_active|is_blocked"

# IDOR
rg "\.objects\.get\(.*\bid=" --type py | rg -v "request\.user|user=|owner="
rg "\.objects\.filter\(.*\bpk=" --type py | rg -v "request\.user"

# Mass assignment
rg "fields\s*=\s*['\"]__all__['\"]" --type py
rg "setattr\(.*request\.(data|POST)" --type py

# Небезопасное сравнение секретов (не timing-safe)
rg "(backend.?key|api.?key|token|signature)\s*==" --type py
```

## 4. Инъекции и опасные функции

```bash
# SQL
rg "\.raw\(|\.extra\(|RawSQL\(|cursor\.execute\(.*%|text\(f?['\"]" --type py

# Command injection
rg "subprocess\.(call|run|Popen)\(.*shell=True|os\.(system|popen)\(" --type py
rg "child_process\.(exec|execSync)\(" 

# Десериализация / eval
rg "pickle\.loads|yaml\.load\(|marshal\.loads" --type py
rg "\beval\(|new Function\(|exec\(" 

# SSTI
rg "render_template_string|autoescape\s*=\s*False" --type py

# XSS (frontend)
rg "dangerouslySetInnerHTML|v-html|innerHTML|document\.write"
```

## 5. Деньги и гонки

```bash
# Финансовые операции без atomic
rg "\.save\(\)" --type py -l | xargs rg -L "transaction\.atomic"

# select_for_update / atomic / F()
rg "select_for_update|with_for_update|transaction\.atomic|F\(" --type py

# Клиент задаёт цену/скидку/сумму
rg -i "request\.(data|POST|json).*\b(amount|price|discount|total|balance)\b" --type py

# Webhook подпись
rg -i "hmac|compare_digest|X-.*Signature|webhook_secret" --type py
rg "return.*Http(Error|Response)" --type py   # return вместо raise
```

## 6. SSRF / внешние запросы

```bash
rg "requests\.(get|post|put|delete|head)\(" --type py
rg "httpx\.(get|post|AsyncClient)|aiohttp\.(ClientSession|request)" --type py
rg -i "callback_url|webhook_url|notify_url|image_url|avatar_url|redirect_uri"
rg -i "is_private|is_loopback|169\.254|127\.0\.0\.1|ipaddress"   # проверка приватных диапазонов
```

## 7. Конфигурация и заголовки

```bash
rg -i "CORS_ALLOW_ALL_ORIGINS|Access-Control-Allow-Origin|allow_origins"
rg -i "ALLOWED_HOSTS.*\*|DEBUG\s*=\s*True|DEV_MODE"
rg -i "csrf_exempt|@csrf|SameSite"

# Живая проверка заголовков
curl -sI https://target/ | grep -iE "strict-transport|content-security|x-frame|x-content-type|access-control|server"
```

## 8. Инфраструктура (read-only с сервера)

```bash
# Экспозиция портов
ss -tulpn
ss -tulpn | grep -E '6379|3306|5432|27017|6000'   # БД/брокер не должны быть на 0.0.0.0

# Docker hardening
docker ps
for c in $(docker ps --format '{{.Names}}'); do
  docker inspect "$c" --format '{{.Name}} Priv={{.HostConfig.Privileged}} User={{.Config.User}} Cap={{.HostConfig.CapAdd}} RO={{.HostConfig.ReadonlyRootfs}}'
done
docker ps -q | xargs -r docker inspect --format '{{.Name}} {{range .Mounts}}{{.Source}} {{end}}' | grep -i docker.sock

# Redis без пароля
docker exec <redis> redis-cli CONFIG GET requirepass

# SSH / firewall / ОС
grep -vE '^\s*#|^\s*$' /etc/ssh/sshd_config
sudo nft list ruleset || sudo iptables -S; ufw status verbose
systemctl status unattended-upgrades --no-pager; timedatectl; uname -a

# Утечки в логах
docker logs --tail 500 <container> 2>&1 | rg -i "api.?key|password|secret|priv"

# Образы (digest для supply chain)
docker images --digests
```

## 9. Зависимости

```bash
pip-audit -r requirements.txt
safety check --full-report
npm audit --audit-level=high
osv-scanner -r .

# Незапиненные версии
rg ">=|\*" requirements.txt pyproject.toml 2>/dev/null
rg "\"\^|\"~|latest" package.json

# SRI отсутствует
rg "<script[^>]*src=[\"']https?://" | rg -L "integrity"
```

## 10. Доступность чувствительных путей (внешняя проверка)

```bash
# С разрешения. Ожидаем 404/403 на служебных путях:
curl -sk -o /dev/null -w '%{http_code}\n' https://target/.env
curl -sk -o /dev/null -w '%{http_code}\n' https://target/.git/HEAD
curl -sk -o /dev/null -w '%{http_code}\n' https://target/internal/service-key

# Тестовые заголовки окружения
curl -sI https://target/ | grep -iE "x-environment|x-debug"
```
