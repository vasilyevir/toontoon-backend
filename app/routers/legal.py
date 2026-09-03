"""Условия и политика конфиденциальности — страницами с этого же сервера.

Тексты лежат в `content/legal/`, а ссылки из приложения ведут на
`https://toontoon.ai/terms` и `/privacy`. Домена пока нет, но когда он появится,
публикация должна быть одной DNS-записью, а не задачей: страницы уже отдаются.

Два правила, и оба про честность:

* Пока в тексте остались `[[СКОБКИ]]` — сведения, которых в коде нет и быть не
  может: юрлицо, адрес, почта, — страница НЕ публикуется, а отвечает 503 с
  внятной причиной. Документ с дырами хуже отсутствующего: его прочитают как
  обещание.
* Отдаётся ровно `content/legal/*.md`, без второго экземпляра в HTML. Два
  экземпляра одного документа расходятся — так уже разошлись адреса ссылок в
  `AppConfig` и `SettingsView`.
"""
from __future__ import annotations

import re
from pathlib import Path

import markdown
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legal"])

LEGAL_DIR = Path(__file__).resolve().parents[2] / "content" / "legal"
PAGES = {"terms": "terms.md", "privacy": "privacy.md"}
_PLACEHOLDER = re.compile(r"\[\[[^\]]*\]\]")

_SHELL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Toontoon</title>
<style>
  :root{{color-scheme:dark}}
  body{{margin:0;background:#0A0A0A;color:#E0E0E0;font:16px/1.65 -apple-system,system-ui,sans-serif}}
  main{{max-width:44rem;margin:0 auto;padding:3rem 1.25rem 5rem}}
  h1{{font-size:1.9rem;line-height:1.2;letter-spacing:-.01em}} h2{{margin-top:2.2rem;font-size:1.25rem}}
  a{{color:#FF8FA3}} table{{border-collapse:collapse;width:100%;font-size:.93rem;display:block;overflow-x:auto}}
  th,td{{text-align:left;vertical-align:top;padding:.5rem .6rem;border-bottom:1px solid #3A3A3A}}
  blockquote{{margin:1.2rem 0;padding:.6rem 1rem;border-left:3px solid #474747;color:#A3A3A3}}
  code{{background:#191919;padding:.1em .35em;border-radius:4px}}
</style></head><body><main>{body}</main></body></html>"""


def render(page: str) -> str:
    """HTML страницы или исключение, если публиковать нельзя."""
    name = PAGES.get(page)
    if not name:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such page")
    text = (LEGAL_DIR / name).read_text(encoding="utf-8")
    if (holes := _PLACEHOLDER.findall(text)):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"This page is not published yet: {len(holes)} detail(s) still to be filled in.",
        )
    body = markdown.markdown(text, extensions=["tables"])
    title = re.search(r"^#\s+(.+)$", text, re.M)
    return _SHELL.format(title=(title.group(1) if title else page.title()), body=body)


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
async def terms() -> HTMLResponse:
    return HTMLResponse(render("terms"))


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy() -> HTMLResponse:
    return HTMLResponse(render("privacy"))
