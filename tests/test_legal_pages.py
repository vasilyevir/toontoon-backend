"""Условия и политика отдаются сервером — но не раньше, чем в них нет дыр.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_legal_pages.py -q
"""
import pytest
from fastapi import HTTPException

from app.routers import legal


def test_страница_без_дыр_рендерится_целиком(monkeypatch, tmp_path):
    (tmp_path / "privacy.md").write_text("# Toontoon — Privacy Policy\n\n## 1. Who\n\n| A | B |\n|---|---|\n| x | y |\n")
    monkeypatch.setattr(legal, "LEGAL_DIR", tmp_path)
    html = legal.render("privacy")
    assert "<h1>Toontoon — Privacy Policy</h1>" in html and "<table>" in html
    assert "<title>Toontoon — Privacy Policy — Toontoon</title>" in html


def test_дыры_в_тексте_не_публикуются(monkeypatch, tmp_path):
    (tmp_path / "terms.md").write_text("# Terms\n\nOperated by [[LEGAL ENTITY]] at [[ADDRESS]].\n")
    monkeypatch.setattr(legal, "LEGAL_DIR", tmp_path)
    with pytest.raises(HTTPException) as e:
        legal.render("terms")
    assert e.value.status_code == 503 and "2 detail(s)" in e.value.detail


def test_настоящие_тексты_сегодня():
    """Что отдаст сервер прямо сейчас — по настоящим файлам. Оба исхода честны."""
    for page in ("terms", "privacy"):
        try:
            html = legal.render(page)
            assert "[[" not in html
        except HTTPException as e:
            assert e.status_code == 503  # дыры ещё есть — и это сказано вслух
