"""Сгенерировать справочную часть документации из кода.

Контракт и список настроек не пишутся руками: расхождение между документом
и кодом — вопрос времени, а не аккуратности. Один прогон обновляет и то и
другое.

    PYTHONPATH=. .venv/bin/python -m scripts.dump_reference          # записать
    PYTHONPATH=. .venv/bin/python -m scripts.dump_reference --check  # сверить

``--check`` ничего не пишет и возвращает ненулевой код, если файлы отстали
от кода. Это то, что вешается в CI: документация не может протухнуть молча.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import get_args, get_origin

DOCS = Path("docs")
OPENAPI = DOCS / "openapi.json"
CONFIG_MD = DOCS / "CONFIG.md"


def build_openapi() -> str:
    from app.main import app

    schema = app.openapi()
    # Версия приложения меняется чаще контракта; без её фиксации diff шумит.
    schema["info"]["version"] = "generated"
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _type_name(annotation) -> str:
    origin = get_origin(annotation)
    if origin is not None:
        args = ", ".join(_type_name(a) for a in get_args(annotation) if a is not type(None))
        return f"{origin.__name__}[{args}]" if args else origin.__name__
    return getattr(annotation, "__name__", str(annotation))


def build_config_md() -> str:
    from app.config import Settings

    lines = [
        "# Настройки",
        "",
        "**Сгенерировано из `app/config.py`** — не редактируй руками, правь код",
        "и прогоняй `python -m scripts.dump_reference`.",
        "",
        "Любая настройка задаётся переменной окружения с тем же именем в верхнем",
        "регистре. Значения по умолчанию рассчитаны на локальную разработку:",
        "в кластере переопределяются через ConfigMap, секреты — через Secret.",
        "",
        "| Переменная | Тип | По умолчанию |",
        "| --- | --- | --- |",
    ]
    secret_markers = ("key", "secret", "password", "token", "dsn", "url")
    for name, field in Settings.model_fields.items():
        default = field.default
        if any(marker in name for marker in secret_markers) and isinstance(default, str) and default:
            shown = "«задаётся в окружении»"
        elif default is None:
            shown = "—"
        elif default == "":
            shown = "пусто"
        else:
            shown = f"`{default}`"
        lines.append(f"| `{name.upper()}` | {_type_name(field.annotation)} | {shown} |")

    lines += [
        "",
        "## Что обязательно переопределить в проде",
        "",
        "- `DATABASE_URL` — иначе приложение пойдёт искать базу на localhost.",
        "- `EXPOSE_DEV_TOKENS=false` — иначе токен сброса пароля уходит в ответе API.",
        "- `DEBUG=false`, `SESSION_COOKIE_SECURE=true`.",
        "- `S3_*` — доступ к объектному хранилищу и имя бакета для окружения.",
        "- `PUBLIC_BASE_URL`, `FRONTEND_URL`, `CORS_ORIGINS` — настоящие адреса.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    check = "--check" in sys.argv
    DOCS.mkdir(exist_ok=True)

    artifacts = {OPENAPI: build_openapi(), CONFIG_MD: build_config_md()}
    stale = []
    for path, content in artifacts.items():
        if check:
            current = path.read_text() if path.exists() else ""
            if current != content:
                stale.append(path)
        else:
            path.write_text(content)

    if check:
        if stale:
            print("Документация отстала от кода:")
            for path in stale:
                print(f"  {path} — перегенерируй: python -m scripts.dump_reference")
            return 1
        print("Контракт и настройки совпадают с кодом.")
        return 0

    from app.main import app

    routes = sum(1 for r in app.routes if getattr(r, "methods", None))
    print(f"openapi.json: {routes} маршрутов\nCONFIG.md: обновлён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
