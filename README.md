# arteki-web-backend

Backend (API) проекта **arteki-web**. Деплоится в Kubernetes-кластер через общий Helm-чарт
`oci://ghcr.io/atom-group-software/charts/app`. Полный runbook онбординга — в инфра-репо
(`k8s-docs/ops/08-adding-new-projects.md`).

## Ветки

- `main` — **продакшн + CI/CD.** Пуш/мёрж сюда → GitHub Actions собирает Docker-образ и
  катит релиз `arteki-web-backend` в namespace `arteki-web` (`helm upgrade --install`).
- `dev` — общая ветка разработки («склад» проекта). Сюда мёржим фичи из `feature/*`,
  затем PR `dev` → `main`.

> Прямой push в `main` не делаем — только через PR из `dev` после ревью.

## Деплой (CI/CD)

| Параметр | Значение |
| --- | --- |
| Образ | `ghcr.io/atom-group-software/arteki-web-backend` |
| Release | `arteki-web-backend` |
| Namespace | `arteki-web` |
| Конфиг релиза | `deploy/values.yaml` |
| Workflow | `.github/workflows/deploy.yml` |

Папки `deploy/` и `.github/workflows/` разработчики без согласования с DevOps не трогают.

## Что ещё нужно для первого зелёного деплоя (пока не сделано)

- [ ] `Dockerfile` + код приложения (health-эндпоинт, порт `8000`).
- [ ] Заполнить `deploy/values.yaml` под проект (`env`, БД/`redis`, `initSchema`, `ingress`).
- [ ] Секреты репо: `KUBECONFIG`, `GHCR_READ_PAT`.
- [ ] Секреты кластера: `arteki-web-backend-secrets` (если есть секретные env) и `ghcr-pull`.

Подробности — `k8s-docs/ops/08-adding-new-projects.md`.
