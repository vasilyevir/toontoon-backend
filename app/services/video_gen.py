"""Video generation pipeline backed by kie.ai (ByteDance Seedance) — v2.

Text-to-video architecture (prompt-templates-video-v2.md):
  • NO keyframe images are generated — pure text-to-video.
  • anchor + motion → single Seedance prompt.
  • Group C (photo tiles) → first_frame_url + motion prompt only.
  • Prompt assembly: video_prompts.build_storyboard() → deterministic per-tile or LLM free-text.

Seedance takes several minutes, so this runs as a background job (see
``run_video_job``) and the frontend polls ``GET /api/generations/{id}``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx

from app.config import settings
from app.core.security import new_id
from app.db.session import session_scope
from app.models.tile import Tile
from app.services import video_prompts

logger = logging.getLogger("toontoon.video_gen")

UPLOAD_DIR = Path("uploads")


class VideoUnavailable(RuntimeError):
    """Raised when the video pipeline cannot produce a result (refund + retry)."""


def _public_url(path_or_url: str) -> str:
    """Return an absolute, externally reachable URL.

    Generated frames come back as ``/uploads/<name>``; kie.ai must be able to
    fetch them, so prefix our public base URL. Already-absolute URLs pass through.
    """
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    return f"{settings.public_base_url.rstrip('/')}{path_or_url}"




async def _submit_task(payload: dict) -> str:
    """POST createTask and return the kie.ai taskId."""
    headers = {
        "Authorization": f"Bearer {settings.kie_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{settings.kie_base_url.rstrip('/')}/api/v1/jobs/createTask"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code != 200:
        raise VideoUnavailable(f"createTask HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if data.get("code") != 200:
        raise VideoUnavailable(f"createTask error: {data.get('msg') or data}")
    task_id = (data.get("data") or {}).get("taskId")
    if not task_id:
        raise VideoUnavailable(f"createTask returned no taskId: {data}")
    return task_id


async def _poll_task(task_id: str) -> str:
    """Poll recordInfo until success; return the remote video URL.

    Raises ``VideoUnavailable`` on failure or timeout.
    """
    headers = {"Authorization": f"Bearer {settings.kie_api_key}"}
    url = f"{settings.kie_base_url.rstrip('/')}/api/v1/jobs/recordInfo"
    deadline = asyncio.get_event_loop().time() + settings.video_poll_timeout

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(settings.video_poll_interval)
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                resp = await client.get(url, params={"taskId": task_id}, headers=headers)
            data = (resp.json() or {}).get("data") or {}
        except Exception as exc:  # transient network error — keep polling
            logger.warning("recordInfo poll error for %s: %r", task_id, exc)
            continue

        # kie.ai uses `state`: waiting | queuing | generating | success | fail.
        state = str(data.get("state") or data.get("status") or "").lower()
        if state in {"success", "completed", "succeed", "succeeded"}:
            result_json = data.get("resultJson") or data.get("result_json") or ""
            try:
                urls = json.loads(result_json).get("resultUrls") or []
            except (ValueError, TypeError):
                urls = []
            if urls:
                return urls[0]
            raise VideoUnavailable("Seedance succeeded but returned no result URL")
        if state in {"fail", "failed", "error"}:
            raise VideoUnavailable(f"Seedance failed: {data.get('failMsg') or data.get('msg') or 'unknown'}")
        # else still processing → keep polling

    raise VideoUnavailable("Seedance timed out")


async def _download_video(remote_url: str) -> str | None:
    """Download the finished MP4 into /uploads; return a relative ``/uploads`` URL."""
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(remote_url)
        if resp.status_code == 200 and resp.content:
            UPLOAD_DIR.mkdir(exist_ok=True)
            name = f"{new_id('vid_')}.mp4"
            (UPLOAD_DIR / name).write_bytes(resp.content)
            return f"/uploads/{name}"
    except Exception as exc:
        logger.warning("Video download failed (%r) for %s", exc, remote_url)
    return None


async def _extract_thumbnail(local_video_path: str) -> str | None:
    """Extract the first frame of a local MP4 as a JPEG thumbnail.

    Uses ffmpeg (which is available on the server). Returns a relative
    ``/uploads`` URL, or None if extraction fails.
    """
    try:
        # local_video_path is like "/uploads/vid_xxx.mp4"
        mp4_path = Path(local_video_path.lstrip("/"))
        if not mp4_path.exists():
            return None

        thumb_name = mp4_path.stem + "_thumb.jpg"
        thumb_path = UPLOAD_DIR / thumb_name

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-i", str(mp4_path),
            "-vf", "select=eq(n\\,0),scale=200:-1",
            "-vframes", "1",
            "-q:v", "3",
            str(thumb_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=15)

        if thumb_path.exists() and thumb_path.stat().st_size > 0:
            return f"/uploads/{thumb_name}"
    except Exception as exc:
        logger.warning("Thumbnail extraction failed: %r", exc)
    return None


async def run_video_pipeline(
    *,
    tile: Tile | None,
    answers: dict[str, str],
    free_text: str | None,
    style: str | None,
    photo_url: str | None,
) -> tuple[str, str]:
    """v2 text-to-video pipeline — no keyframe images.

    Group A/B (tiles + free-text) → video_prompts.build_storyboard() → Seedance text-to-video.
    Group C (photo tiles) → first_frame_url + motion preset → Seedance image-to-video.
    Returns ``(local_video_url, prompt)``. Raises ``VideoUnavailable`` on failure.
    """
    if not settings.kie_enabled:
        raise VideoUnavailable("Video generator is not configured")

    tile_id = tile.id if tile else None
    text_overlay: str | None = None  # populated by storyboard builders; None for photo-to-video

    # Group C: photo-to-video — user uploaded a real photo
    if photo_url and tile_id in {"animate_photo", "animate_pet"}:
        motion_prompt = video_prompts.build_photo_motion_prompt(answers)
        input_block: dict = {
            "prompt": motion_prompt,
            "negative_prompt": video_prompts.NEGATIVE,
            "first_frame_url": _public_url(photo_url),
            "resolution": settings.video_resolution,
            "aspect_ratio": settings.video_aspect_ratio,
            "duration": 5,
            "generate_audio": ("голос" in motion_prompt.lower() or "audio:" in motion_prompt.lower()),
        }
        model = settings.kie_video_audio_model
        logger.info("Photo-to-video (Group C): tile=%s model=%s", tile_id, model)
        record_prompt = f"[photo → video] {motion_prompt}"

    else:
        # Group A / B / free-text: pure text-to-video
        board = await video_prompts.build_storyboard(
            tile_id=tile_id,
            answers=answers,
            free_text=free_text,
            style=style,
        )
        seedance_prompt = video_prompts.assemble_prompt(board)
        input_block = {
            "prompt": seedance_prompt,
            "negative_prompt": board.negative,
            "resolution": settings.video_resolution,
            "aspect_ratio": settings.video_aspect_ratio,
            "duration": board.duration,
            "generate_audio": board.audio_enabled,
        }
        model = settings.kie_video_audio_model if board.audio_enabled else settings.kie_video_model
        logger.info(
            "Text-to-video (v2): tile=%s loop=%s dur=%s audio=%s model=%s",
            tile_id, board.loop, board.duration, board.audio_enabled, model,
        )
        record_prompt = seedance_prompt[:300]
        text_overlay = board.text_overlay  # may be None

    payload = {"model": model, "input": input_block}
    task_id = await _submit_task(payload)
    remote_url = await _poll_task(task_id)
    local_url = await _download_video(remote_url)
    final_url = local_url or remote_url

    # Bake text into the video if the storyboard requested an overlay.
    if text_overlay and final_url and final_url.startswith("/uploads/"):
        from app.services import video_overlay
        final_url = await video_overlay.apply_text_overlay(final_url, text_overlay)

    return final_url, record_prompt


# ─── Background job ──────────────────────────────────────────────────────────

# Keep strong references to in-flight tasks so they aren't garbage-collected.
_RUNNING: set[asyncio.Task] = set()


def schedule_video_job(**kwargs) -> None:
    """Fire-and-forget the video pipeline as a background task."""
    task = asyncio.create_task(run_video_job(**kwargs))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


async def run_video_job(
    *,
    gen_id: str,
    user_id: str,
    session_id: str,
    payment_amount: int,
    payment_id: str,
    tile: Tile | None,
    answers: dict[str, str],
    free_text: str | None,
    style: str | None,
    photo_url: str | None,
    chat_id: str | None = None,
) -> None:
    """Background worker: run the pipeline, then update the Generation record.

    On success → status DONE + result_url. On failure → status FAILED + refund.
    If ``chat_id`` is set, the result (or a Retry-able error bubble on
    failure) is appended to that chat too — this is the only way a client
    ever sees a video's outcome in its conversation history, since this
    completes minutes after the original ``POST /api/generate`` response.
    """
    from app.models.generation import GenerationStatus
    from app.models.payment import Payment, PaymentStatus
    from app.services import auth_service, chat_service, generations_service, wallet

    try:
        result_url, prompt = await run_video_pipeline(
            tile=tile,
            answers=answers,
            free_text=free_text,
            style=style,
            photo_url=photo_url,
        )
    except Exception as exc:
        logger.warning("Video job %s failed: %r", gen_id, exc)
        # Refund. The job runs long after the HTTP request is gone, so it opens
        # its own database session; the ledger entry is idempotent by payment
        # id, which makes a retried refund harmless.
        try:
            async with session_scope() as db:
                await wallet.cancel(
                    db,
                    user_id,
                    Payment(payment_id=payment_id, status=PaymentStatus.PENDING, amount=payment_amount),
                )
        except Exception:
            logger.exception("Refund after failed video job %s failed", gen_id)

        generation = await generations_service.get(gen_id)
        if generation is not None:
            generation.status = GenerationStatus.FAILED
            await generations_service.save(generation)

        if chat_id:
            try:
                from app.core.security import new_id as _new_id
                from app.models.chat import ChatMessage, ChatRole

                chat = await chat_service.get(chat_id)
                if chat is not None:
                    await chat_service.add_message(
                        chat,
                        ChatMessage(
                            id=_new_id("msg_"),
                            role=ChatRole.AI,
                            generation_id=gen_id,
                            is_generation_error=True,
                            text="Video generation failed — your TOONTOON was refunded.",
                        ),
                    )
            except Exception:
                logger.exception("Failed to append error message to chat %s", chat_id)
        return

    # Extract first-frame thumbnail for gallery/sidebar (best-effort, local path only).
    thumbnail_url: str | None = None
    if result_url and result_url.startswith("/uploads/"):
        thumbnail_url = await _extract_thumbnail(result_url)
        if thumbnail_url:
            logger.info("Video thumbnail saved → %s", thumbnail_url)
        else:
            logger.warning("Thumbnail extraction skipped for %s", result_url)

    generation = await generations_service.get(gen_id)
    if generation is not None:
        generation.status = GenerationStatus.DONE
        generation.result_url = result_url
        generation.thumbnail_url = thumbnail_url
        generation.prompt = prompt
        await generations_service.save(generation)
    logger.info("Video job %s done → %s", gen_id, result_url)

    if chat_id:
        try:
            from app.core.security import new_id as _new_id
            from app.models.chat import ChatMessage, ChatRole

            chat = await chat_service.get(chat_id)
            if chat is not None:
                await chat_service.add_message(
                    chat,
                    ChatMessage(
                        id=_new_id("msg_"),
                        role=ChatRole.AI,
                        generated_video=result_url,
                        thumbnail_url=thumbnail_url,
                        generation_id=gen_id,
                    ),
                )
        except Exception:
            logger.exception("Failed to append result message to chat %s", chat_id)

    # Push notification: tell the user their video is ready.
    try:
        from app.services import push_service
        tile_label = tile.title if tile else None
        body = f"«{tile_label}» готово!" if tile_label else "Ваше видео готово!"
        await push_service.send_push_to_user(
            user_id=user_id,
            title="🎬 Видео готово!",
            body=body,
            url="/generate",
        )
    except Exception as exc:
        logger.debug("Push notification failed (non-critical): %r", exc)
