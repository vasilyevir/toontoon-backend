# 26 — Автоматизированные инструменты (SAST / DAST / SCA / Secrets)

> Набор инструментов для автоматической проверки безопасности и их встраивание в CI/CD. Автоматика
> не заменяет ручной аудит бизнес-логики, но ловит большинство типовых проблем.

**Легенда:** 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low · ⚪ Info

---

## Категории
1. [SAST — статический анализ](#1-sast--статический-анализ)
2. [SCA — анализ зависимостей](#2-sca--анализ-зависимостей)
3. [Secrets detection](#3-secrets-detection)
4. [Container / IaC scanning](#4-container--iac-scanning)
5. [DAST — динамический анализ](#5-dast--динамический-анализ)
6. [Рекомендуемый CI pipeline](#6-рекомендуемый-ci-pipeline)

---

## 1. SAST — статический анализ

| Инструмент | Язык | Что проверяет | Команда |
|-----------|------|---------------|---------|
| **bandit** | Python | hardcoded passwords, SQLi, eval, pickle | `bandit -r . -f json` |
| **semgrep** | Multi | Custom rules + OWASP, бизнес-паттерны | `semgrep --config=auto .` |
| **eslint-plugin-security** | JS/TS | XSS, eval, innerHTML, RegExp DoS | plugin в `.eslintrc` |
| **gosec** | Go | Инъекции, крипто, файлы | `gosec ./...` |
| **Brakeman** | Ruby/Rails | SQLi, XSS, mass assignment | `brakeman` |
| **mypy / pyright** | Python | Типы в security-critical коде | `mypy --strict` |
| **CodeQL** | Multi | Semantic queries, taint-анализ | GitHub Advanced Security |

---

## 2. SCA — анализ зависимостей

| Инструмент | Что проверяет | Команда |
|-----------|---------------|---------|
| **pip-audit** | Python CVE | `pip-audit --strict` |
| **safety** | Python CVE (альтернатива) | `safety check --full-report` |
| **npm audit** | JS/TS CVE | `npm audit --audit-level=high` |
| **osv-scanner** | Multi (OSV база) | `osv-scanner -r .` |
| **Snyk** | Multi + license | `snyk test` |
| **Dependabot / Renovate** | Auto-PR на CVE | Настройка в репо |

---

## 3. Secrets detection

| Инструмент | Что проверяет | Команда |
|-----------|---------------|---------|
| **gitleaks** | Secrets в коде и git-истории | `gitleaks detect --source .` |
| **trufflehog** | High-entropy + known patterns, верификация | `trufflehog git file://. --only-verified` |
| **detect-secrets** | Pre-commit hook | `detect-secrets scan` |

---

## 4. Container / IaC scanning

| Инструмент | Что проверяет | Команда |
|-----------|---------------|---------|
| **trivy** | Образы, IaC, ФС, секреты | `trivy image myapp:latest` / `trivy config .` |
| **grype** | CVE в образах | `grype myapp:latest` |
| **checkov** | Terraform/K8s/CFN misconfig | `checkov -d .` |
| **tfsec** | Terraform misconfig | `tfsec .` |
| **kube-bench** | CIS Kubernetes | `kube-bench run` |
| **dockle** | CIS Docker образа | `dockle myapp:latest` |

---

## 5. DAST — динамический анализ

| Инструмент | Что проверяет | Когда запускать |
|-----------|---------------|-----------------|
| **OWASP ZAP** | XSS, SQLi, CSRF, headers, auth | На staging после деплоя |
| **Nuclei** | 5000+ templates известных уязвимостей | Staging, по расписанию |
| **nikto** | Web-server misconfig | При изменении конфига сервера |
| **sqlmap** | SQLi (с разрешения) | Точечная проверка подозрений |
| **Burp Suite** | Полноценный ручной/авто DAST | Ручной пентест |

---

## 6. Рекомендуемый CI pipeline

```yaml
security-checks:
  steps:
    - name: SAST
      run: |
        bandit -r backend/ -f json -o bandit-report.json
        semgrep --config=auto --error --json -o semgrep-report.json .

    - name: Dependency Audit (SCA)
      run: |
        pip-audit --strict
        npm audit --audit-level=high

    - name: Secrets Scan
      run: gitleaks detect --source . --verbose --redact

    - name: Container & IaC Scan
      run: |
        trivy image --severity HIGH,CRITICAL --exit-code 1 $IMAGE_NAME
        trivy config --severity HIGH,CRITICAL .

    - name: DAST (staging only)
      if: github.ref == 'refs/heads/staging'
      run: zap-cli quick-scan --self-contained $STAGING_URL
```

### Практика встраивания

| # | Рекомендация | Крит. |
|---|--------------|-------|
| TOOL-1 | Гейт на High/Critical (fail build) | 🟡 |
| TOOL-2 | Secrets-scan в pre-commit hook | 🟡 |
| TOOL-3 | SCA + Dependabot/Renovate постоянно | 🟡 |
| TOOL-4 | Baseline для подавления известных FP | 🟢 |
| TOOL-5 | DAST только на staging (не на прод) | 🟠 |
| TOOL-6 | Не полагаться только на автоматику — ручной аудит логики | 🟠 |

> ⚠️ Автосканеры **не ловят** бизнес-логику, IDOR на кастомных моделях, race conditions, слабую
> криптографию по смыслу, backdoor-эндпоинты. Их результат — вход для ручного аудита, а не финал.
