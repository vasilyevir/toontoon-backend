"""Каждое `settings.<имя>` в коде должно существовать в Settings.

Поле `rate_limit_per_hour` однажды пропало из конфига, а обращение к нему в
/api/generate осталось: каждая генерация падала с 500, и ни один тест этого не
заметил — до лимита в тестах никто не доходит. Эта проверка ловит такое
статически, без запросов.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings

APP = Path(__file__).resolve().parents[1] / "app"
REF = re.compile(r"\bsettings\.([a-z_][a-z0-9_]*)")


def test_every_settings_attribute_exists() -> None:
    known = set(Settings.model_fields) | {
        n for n in dir(Settings) if not n.startswith("_")
    }
    missing: dict[str, set[str]] = {}
    for path in APP.rglob("*.py"):
        for name in REF.findall(path.read_text(encoding="utf-8")):
            if name not in known:
                missing.setdefault(name, set()).add(str(path.relative_to(APP)))
    assert not missing, f"в Settings нет полей: {missing}"
