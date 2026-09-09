"""Эксперимент: одежда с витринного кадра стиля.

Флаг выключен — промпт стиля прежний. Включён и кадр приложен — в промпте
стоит требование взять одежду с образца и подогнать под пол человека, сразу
за требованием сохранить самого человека.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import content_gen, prompt_style


def _style():
    return SimpleNamespace(prompt_template={"text": "the person in a long dark coat", "anchor": "realistic"})


def test_default_prompt_untouched() -> None:
    prompt, _ = content_gen.build_style_prompt(_style(), editing=True)
    assert "STYLE SAMPLE" not in prompt
    assert "outfit comes from" not in prompt


def test_wardrobe_clause_follows_identity() -> None:
    prompt, _ = content_gen.build_style_prompt(_style(), editing=True, wardrobe_from_sample=True)
    assert "outfit comes from" in prompt
    assert "menswear only" in prompt
    identity = prompt.index(prompt_style.IDENTITY_CLAUSE[:40])
    wardrobe = prompt.index(prompt_style.WARDROBE_FROM_SAMPLE_CLAUSE[:40])
    scene = prompt.index("long dark coat")
    assert identity < wardrobe < scene


# ─── Проверка одежды после кадра ────────────────────────────────────────────

import asyncio
from dataclasses import dataclass, replace as _replace

from app.services import image_job, gpt as gpt_service
from app.services.generation.operations import GenerationRequest, Operation


@dataclass
class _Result:
    data: bytes
    cost_usd: float | None = 0.05
    provider_id: str = "p"
    model: str = "m"


def _request() -> GenerationRequest:
    return GenerationRequest(operation=Operation.IMAGE_TO_IMAGE, prompt="scene",
                             image=b"ref", image_mime="image/jpeg")


def test_wardrobe_check_off_does_nothing(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(gpt_service, "wardrobe_mismatch", lambda *a, **k: called.append(1))
    result, prompt, reshot = asyncio.run(image_job.reshoot_if_wardrobe_off(
        None, _request(), _Result(b"img"), "scene", enabled=False, prefer=None))
    assert not called and not reshot and prompt == "scene"


def test_wardrobe_ok_keeps_frame(monkeypatch) -> None:
    async def ok(result, reference): return None
    monkeypatch.setattr(gpt_service, "wardrobe_mismatch", ok)
    first = _Result(b"img")
    result, prompt, reshot = asyncio.run(image_job.reshoot_if_wardrobe_off(
        None, _request(), first, "scene", enabled=True, prefer=None))
    assert result is first and not reshot


def test_wardrobe_mismatch_reshoots_once_and_sums_cost(monkeypatch) -> None:
    async def bad(result, reference): return "man"
    monkeypatch.setattr(gpt_service, "wardrobe_mismatch", bad)
    seen = []
    async def run(db, request, prefer=None):
        seen.append(request.prompt); return _Result(b"again", cost_usd=0.07)
    monkeypatch.setattr(image_job.generation_core, "run", run)
    result, prompt, reshot = asyncio.run(image_job.reshoot_if_wardrobe_off(
        None, _request(), _Result(b"img", cost_usd=0.05), "scene", enabled=True, prefer=None))
    assert reshot and result.data == b"again"
    assert "strictly in menswear" in prompt and seen == [prompt]
    assert abs(result.cost_usd - 0.12) < 1e-9


def test_wardrobe_check_needs_reference(monkeypatch) -> None:
    async def bad(result, reference): return "man"
    monkeypatch.setattr(gpt_service, "wardrobe_mismatch", bad)
    request = _replace(_request(), image=None)
    _, _, reshot = asyncio.run(image_job.reshoot_if_wardrobe_off(
        None, request, _Result(b"img"), "scene", enabled=True, prefer=None))
    assert not reshot


def test_job_spec_carries_wardrobe_fields() -> None:
    from app.services import jobs
    spec = jobs.spec_from_call(
        photo_media_id="med_1", extra_media_ids=[], gen_id="g", user_id="u",
        payment_id="p", payment_amount=15, request=_request(), prompt="scene",
        wardrobe_check=True, wardrobe_sample_key="showcase/x.jpg")
    again = jobs.JobSpec.from_record(spec.to_record())
    assert again.wardrobe_check is True and again.wardrobe_sample_key == "showcase/x.jpg"


def test_job_accepts_every_spec_argument() -> None:
    """Словарь аргументов один на оба пути: всё, что кладёт спецификация и
    маршрут заказа, задача обязана принимать. Лишний ключ ронял её TypeError
    уже после списания денег — молча, в фоне."""
    import inspect
    params = inspect.signature(image_job._run_image_job).parameters
    for key in ("wardrobe_check", "wardrobe_sample_key", "check_drawn", "from_chat",
                "said", "sample_brands", "prefer", "prompt", "request"):
        assert key in params, key
