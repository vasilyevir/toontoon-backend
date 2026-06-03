"""Content generation backed by Pollinations.

This is intentionally isolated behind a small interface so it can be swapped for
the real generation stack later without touching the routers. For now:

* images  -> image.pollinations.ai (FLUX)
* video   -> mock: we return a representative MP4 placeholder (Pollinations has
             no public video endpoint; the product treats video as a 15s mock)
* vision  -> text.pollinations.ai OpenAI-compatible endpoint, used to describe an
             uploaded photo so it can enrich the prompt (graceful fallback)
"""
from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import random
from pathlib import Path
from urllib.parse import quote

import httpx

from app.config import settings
from app.core.security import new_id
from app.core.transliterate import transliterate
from app.models.generation import GenerationType
from app.models.tile import Tile
from app.services import picture_prompts, prompt_style

# A small, stable public placeholder used for the video mock.
_VIDEO_PLACEHOLDER = (
    "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4"
)

logger = logging.getLogger("arteki.content_gen")


class GenerationUnavailable(RuntimeError):
    """Raised when the upstream generator cannot produce a result (refund + retry later)."""


UPLOAD_DIR = Path("uploads")
# Per-attempt timeout. Multiple attempts + backoff stay safely under nginx's
# proxy_read_timeout (60s): worst case ~ 15 + 1.5 + 15 + 3 + 15 = 49.5s.
_IMAGE_FETCH_TIMEOUT = 15.0
_RETRY_BACKOFF_SECONDS = 1.5
_EXT_BY_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def build_prompt(
    *,
    tile: Tile | None,
    answers: dict[str, str],
    free_text: str | None,
    style: str | None,
    photo_description: str | None = None,
) -> tuple[str, str]:
    """Mechanical fallback builder (used when GPT is unavailable).

    Returns ``(prompt, negative_prompt)``. For picture tiles it fills the exact
    per-tile template with our answers; otherwise it assembles style anchor +
    scene + optional layout + technical block. Never emits banned "quality"
    words and never renders greeting text.
    """
    # Picture tiles: fill the per-tile template deterministically (best effort).
    if tile is not None and picture_prompts.is_picture_tile(tile.id):
        tpl = picture_prompts.TEMPLATES[tile.id]
        values: dict[str, str] = {}
        answer_values = [answers[q.id] for q in tile.questions if answers.get(q.id)]
        # Map answers onto placeholders positionally, falling back to defaults.
        names = list(tpl.fields.keys())
        for i, name in enumerate(names):
            if i < len(answer_values) and answer_values[i]:
                values[name] = answer_values[i]
            else:
                values[name] = tpl.fields[name] or "scene"
        prompt = tpl.template
        for name, value in values.items():
            prompt = prompt.replace("{" + name + "}", value)
        return transliterate(prompt), prompt_style.NEGATIVE_PROMPT

    style_key = prompt_style.map_style(style)
    is_text = prompt_style.is_text_tile(tile.category.value) if tile else False

    scene_parts: list[str] = []
    if tile is not None:
        scene_parts.append(tile.title)
        for question in tile.questions:
            value = answers.get(question.id)
            if not value:
                continue
            if question.id == "text":
                # Greeting text is overlaid later, never drawn by the generator.
                continue
            if question.id == "style":
                # Style is expressed via the anchor, not as a raw scene word.
                continue
            scene_parts.append(value)
    if free_text:
        scene_parts.append(free_text)
    if photo_description:
        scene_parts.append(f"inspired by the reference: {photo_description}")
    scene_parts.append("warm friendly expression, heartwarming gentle mood")

    scene = ", ".join(p.strip() for p in scene_parts if p and p.strip())
    prompt = prompt_style.assemble(scene, style_key=style_key, is_text=is_text)
    return transliterate(prompt), prompt_style.NEGATIVE_PROMPT


def build_image_url(
    prompt: str,
    *,
    negative: str | None = None,
    width: int = 1024,
    height: int = 1024,
    seed: int | None = None,
) -> str:
    seed = seed if seed is not None else random.randint(1, 1_000_000)
    encoded = quote(prompt, safe="")
    url = (
        f"{settings.pollinations_image_url}/prompt/{encoded}"
        f"?width={width}&height={height}&seed={seed}&nologo=true"
        f"&model={settings.pollinations_model}"
    )
    if negative:
        url += f"&negative_prompt={quote(negative, safe='')}"
    if settings.pollinations_referrer:
        url += f"&referrer={quote(settings.pollinations_referrer, safe='')}"
    if settings.pollinations_token:
        url += f"&token={quote(settings.pollinations_token, safe='')}"
    return url


def _to_data_url(photo_url: str) -> str | None:
    """Convert a locally uploaded file path/URL into a base64 data URL.

    Pollinations vision needs an inline or publicly reachable image; our local
    uploads aren't reachable externally, so we inline them as data URLs.
    """
    if photo_url.startswith("data:"):
        return photo_url

    # Resolve "/uploads/<file>" (served by this API) to a local file.
    name = photo_url.rsplit("/uploads/", 1)[-1] if "/uploads/" in photo_url else None
    if not name:
        return None
    path = Path("uploads") / name
    if not path.exists():
        return None
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


async def analyze_photo(photo_url: str) -> str:
    """Describe an uploaded photo via Pollinations vision. Returns "" on failure."""
    data_url = _to_data_url(photo_url)
    if not data_url:
        return ""

    payload = {
        "model": "openai",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image briefly for use as an image-generation reference."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=settings.vision_timeout_seconds) as client:
            resp = await client.post(f"{settings.pollinations_text_url}/openai", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        # Graceful fallback — generation continues without the photo description.
        return ""


async def _fetch_to_uploads(url: str, *, attempts: int = 3) -> str | None:
    """Download a generated image and store it locally.

    Returns a relative ``/uploads/<name>`` URL (served over HTTPS via the
    nginx + Next proxy, same-origin) so the browser never has to hotlink the
    slow/unreliable upstream generator.

    Pollinations' anonymous tier intermittently answers 402/5xx (rate/quota) or
    times out, so we retry with a short backoff. Returns None only after all
    attempts fail — the caller then treats the generation as failed and refunds.
    """
    headers = {}
    if settings.pollinations_token:
        headers["Authorization"] = f"Bearer {settings.pollinations_token}"

    last_reason = "unknown"
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(
                timeout=_IMAGE_FETCH_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                mime = resp.headers.get("content-type", "").split(";")[0].strip()
                if mime.startswith("image/") and resp.content:
                    UPLOAD_DIR.mkdir(exist_ok=True)
                    name = f"{new_id('img_')}{_EXT_BY_MIME.get(mime, '.jpg')}"
                    (UPLOAD_DIR / name).write_bytes(resp.content)
                    return f"/uploads/{name}"
                last_reason = f"200 but non-image content-type={mime!r}"
            else:
                last_reason = f"HTTP {resp.status_code}"
        except Exception as exc:  # network error / timeout
            last_reason = repr(exc)

        if attempt < attempts - 1:
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))

    logger.warning(
        "Pollinations image fetch failed after %d attempts (%s): %s",
        attempts,
        last_reason,
        url,
    )
    return None


async def _openai_image_to_uploads(prompt: str, *, attempts: int = 2) -> str | None:
    """Generate an image via OpenAI Images API and store it locally.

    Uses ``openai_image_model`` (gpt-image-1) and automatically falls back to
    dall-e-3 if the account cannot access the preferred model. Returns a relative
    ``/uploads/<name>.png`` URL, or None after all attempts fail.
    """
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    model = settings.openai_image_model
    last_reason = "unknown"

    for attempt in range(attempts):
        body: dict = {
            "model": model,
            "prompt": prompt[:4000] if model == "dall-e-3" else prompt[:32000],
            "n": 1,
            "size": settings.openai_image_size,
        }
        if model == "dall-e-3":
            body["response_format"] = "b64_json"
            body["quality"] = settings.openai_image_quality if settings.openai_image_quality in {"standard", "hd"} else "hd"
        else:
            if settings.openai_image_quality in {"low", "medium", "high", "auto"}:
                body["quality"] = settings.openai_image_quality

        try:
            async with httpx.AsyncClient(timeout=settings.openai_image_timeout) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/images/generations", json=body, headers=headers
                )
            if resp.status_code == 200:
                b64 = resp.json()["data"][0]["b64_json"]
                UPLOAD_DIR.mkdir(exist_ok=True)
                name = f"{new_id('img_')}.png"
                (UPLOAD_DIR / name).write_bytes(base64.b64decode(b64))
                return f"/uploads/{name}"

            last_reason = f"HTTP {resp.status_code}: {resp.text[:300]}"
            # Preferred model not accessible for this account -> fall back to dall-e-3.
            if model != "dall-e-3" and resp.status_code in (400, 403, 404):
                logger.warning("OpenAI image model %s unavailable, falling back to dall-e-3", model)
                model = "dall-e-3"
                continue
        except Exception as exc:  # network error / timeout
            last_reason = repr(exc)

        if attempt < attempts - 1:
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS)

    logger.warning("OpenAI image generation failed after %d attempts: %s", attempts, last_reason)
    return None


async def generate(
    *,
    gen_type: GenerationType,
    tile: Tile | None,
    answers: dict[str, str],
    free_text: str | None,
    style: str | None,
    photo_url: str | None,
) -> tuple[str, str]:
    """Run a generation.

    Returns ``(result_url, prompt)``. Swap the body of this function to plug in
    the production generation stack — the signature is stable.

    Prompt pipeline:
      1. If a photo was uploaded → analyze it via vision (Pollinations, 7s timeout).
      2. If OPENAI_API_KEY is set → GPT-4o mini writes a rich prompt from tile+answers.
      3. Fallback → simple mechanical builder (always works, no key needed).
    """
    from app.services import gpt as gpt_service  # local import avoids circular dep

    photo_description = ""
    if photo_url:
        photo_description = await analyze_photo(photo_url)

    # Tiles carry their style choice inside answers["style"]; honor it for the anchor.
    style = style or answers.get("style")

    # Try GPT prompt builder first.
    prompt, negative = await gpt_service.build_prompt(
        tile=tile,
        answers=answers,
        free_text=free_text,
        style=style,
        photo_description=photo_description or None,
    )

    # Fall back to mechanical builder if GPT is not configured or failed.
    if not prompt:
        prompt, negative = build_prompt(
            tile=tile,
            answers=answers,
            free_text=free_text,
            style=style,
            photo_description=photo_description or None,
        )

    if gen_type == GenerationType.VIDEO:
        # TODO: replace with the real video pipeline once available.
        return _VIDEO_PLACEHOLDER, prompt

    # Generate via the configured provider, always serving from our own origin.
    # OpenAI's Images API ignores negative prompts; Pollinations FLUX honors them.
    if settings.image_provider == "openai" and settings.openai_enabled:
        local_url = await _openai_image_to_uploads(prompt)
    else:
        local_url = await _fetch_to_uploads(build_image_url(prompt, negative=negative))

    if not local_url:
        # Provider is unavailable. Raise so the caller refunds the reserved TEKI
        # instead of charging the user for a broken/missing image.
        raise GenerationUnavailable("Image generator is temporarily unavailable")
    return local_url, prompt
