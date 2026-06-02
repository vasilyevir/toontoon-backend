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

import base64
import mimetypes
import random
from pathlib import Path
from urllib.parse import quote

import httpx

from app.config import settings
from app.core.transliterate import transliterate
from app.models.generation import GenerationType
from app.models.tile import Tile

_QUALITY_SUFFIX = "high quality, highly detailed, professional, sharp focus, beautiful lighting"

# A small, stable public placeholder used for the video mock.
_VIDEO_PLACEHOLDER = (
    "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4"
)


def build_prompt(
    *,
    tile: Tile | None,
    answers: dict[str, str],
    free_text: str | None,
    style: str | None,
    photo_description: str | None = None,
) -> str:
    """Compose a single text prompt from a tile + answers, or free-form text."""
    parts: list[str] = []

    if tile is not None:
        parts.append(tile.title)
        # Append answers in the tile's question order for a stable prompt.
        for question in tile.questions:
            value = answers.get(question.id)
            if value:
                parts.append(value)
    if free_text:
        parts.append(free_text)
    if style:
        parts.append(f"{style} style")
    if photo_description:
        parts.append(f"based on the reference: {photo_description}")

    parts.append(_QUALITY_SUFFIX)
    prompt = ", ".join(p.strip() for p in parts if p and p.strip())
    return transliterate(prompt)


def build_image_url(prompt: str, *, width: int = 1024, height: int = 1024, seed: int | None = None) -> str:
    seed = seed if seed is not None else random.randint(1, 1_000_000)
    encoded = quote(prompt, safe="")
    return (
        f"{settings.pollinations_image_url}/prompt/{encoded}"
        f"?width={width}&height={height}&seed={seed}&nologo=true&model=flux"
    )


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

    # Try GPT prompt builder first.
    prompt = await gpt_service.build_prompt(
        tile=tile,
        answers=answers,
        free_text=free_text,
        style=style,
        photo_description=photo_description or None,
    )

    # Fall back to mechanical builder if GPT is not configured or failed.
    if not prompt:
        prompt = build_prompt(
            tile=tile,
            answers=answers,
            free_text=free_text,
            style=style,
            photo_description=photo_description or None,
        )

    if gen_type == GenerationType.VIDEO:
        # TODO: replace with the real video pipeline once available.
        return _VIDEO_PLACEHOLDER, prompt

    return build_image_url(prompt), prompt
