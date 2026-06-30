"""Cloudinary-backed text overlay for generated videos.

Pipeline:
  1. Upload the local mp4 to Cloudinary with an eager text-overlay
     transformation applied synchronously during upload.
  2. Download the transformed mp4 back to /uploads.
  3. Delete the Cloudinary asset (we only need the file, not cloud storage).

Configuration (env):
  CLOUDINARY_CLOUD_NAME  — your cloud name
  CLOUDINARY_API_KEY     — API key
  CLOUDINARY_API_SECRET  — API secret

If any of the three are empty the module is a no-op and returns the original path.

Text style:
  - Font : Arial Bold, 56 px  (available on all Cloudinary plans)
  - Color: white with a soft dark drop-shadow for legibility on any background
  - Position: centered horizontally, 14 % from the bottom  (gravity south)
  - Width: capped at 80 % of video width with auto word-wrap
  - Multi-line supported via encoded newline (%0A)
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger("arteki.video_overlay")

UPLOAD_DIR = Path("uploads")

# ─── Cloudinary REST helpers ──────────────────────────────────────────────────

def _sign(params: dict[str, str]) -> str:
    """Compute a Cloudinary v1 signature for the given params dict."""
    # Sort params alphabetically, join as key=value&..., append secret.
    body = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hashlib.sha1(f"{body}{settings.cloudinary_api_secret}".encode()).hexdigest()


def _encode_overlay_text(text: str) -> str:
    """Encode text for a Cloudinary l_text layer.

    Cloudinary's layer encoding replaces a few characters:
      ,  ->  %2C
      /  ->  %2F
      newline -> %0A
      space -> %20  (standard URL encoding)
    """
    # Standard percent-encode everything, then fix Cloudinary-specific chars.
    encoded = quote(text, safe="")
    return encoded


def _build_overlay_transformation(text: str, video_width: int = 720) -> str:
    """Return the Cloudinary eager transformation string for a text overlay.

    Layout (9:16 portrait, 720 p):
      Shadow layer    — semi-transparent dark rectangle behind the text
      Text layer      — white Arial Bold 56 px, centered, 80 px from bottom
    """
    enc = _encode_overlay_text(text)
    # Drop-shadow: a dark blurred rectangle behind the text
    shadow = "l_text:Arial_56_bold:" + enc + ",co_rgb:000000,o_35,e_blur:600"
    # Main text: white, centered, 80 px from bottom, max-width 80 % of video
    main = (
        "l_text:Arial_56_bold:" + enc
        + ",co_white"
        + ",g_south"
        + ",y_80"
        + f",w_{int(video_width * 0.80)}"
        + ",c_fit"
    )
    return f"{shadow}/{main}"


# ─── Public API ───────────────────────────────────────────────────────────────

async def apply_text_overlay(local_video_path: str, text: str) -> str:
    """Upload ``local_video_path`` to Cloudinary, bake in ``text``, download result.

    Returns the new local ``/uploads/…`` path. On any error returns the
    original path unchanged (overlay is best-effort, never blocks delivery).
    """
    if not settings.cloudinary_enabled:
        logger.debug("Cloudinary not configured — skipping text overlay")
        return local_video_path

    if not text.strip():
        return local_video_path

    path = Path(local_video_path.lstrip("/"))
    if not path.exists():
        logger.warning("Overlay: source file not found: %s", path)
        return local_video_path

    cloud = settings.cloudinary_cloud_name
    api_key = settings.cloudinary_api_key
    upload_url = f"https://api.cloudinary.com/v1_1/{cloud}/video/upload"

    transformation = _build_overlay_transformation(text)
    timestamp = str(int(time.time()))

    # Params included in the signature (no file, no api_key).
    sign_params = {
        "eager": transformation,
        "eager_async": "false",  # render synchronously during upload
        "timestamp": timestamp,
    }
    signature = _sign(sign_params)

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            # ── Step 1: upload + eager transform ─────────────────────────────
            logger.info("Uploading video to Cloudinary for text overlay: %s", path.name)
            with open(path, "rb") as f:
                resp = await client.post(
                    upload_url,
                    data={
                        "api_key": api_key,
                        "timestamp": timestamp,
                        "signature": signature,
                        "eager": transformation,
                        "eager_async": "false",
                    },
                    files={"file": (path.name, f, "video/mp4")},
                )
            resp.raise_for_status()
            upload_data = resp.json()

        public_id = upload_data.get("public_id", "")
        eager_list = upload_data.get("eager") or []

        if not eager_list:
            logger.warning("Cloudinary eager transform not in response — fallback to original")
            await _delete_asset(cloud, api_key, public_id)
            return local_video_path

        transformed_url = eager_list[0].get("secure_url") or eager_list[0].get("url")
        if not transformed_url:
            logger.warning("Cloudinary eager URL missing — fallback to original")
            await _delete_asset(cloud, api_key, public_id)
            return local_video_path

        # ── Step 2: download the baked mp4 ───────────────────────────────────
        logger.info("Downloading overlaid video from Cloudinary: %s", transformed_url)
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            dl = await client.get(transformed_url)
        dl.raise_for_status()

        UPLOAD_DIR.mkdir(exist_ok=True)
        stem = path.stem.replace("vid_", "")
        out_name = f"vid_{stem}_txt.mp4"
        out_path = UPLOAD_DIR / out_name
        out_path.write_bytes(dl.content)
        logger.info("Text overlay saved → %s", out_path)

        # ── Step 3: delete from Cloudinary (we keep only local copy) ─────────
        await _delete_asset(cloud, api_key, public_id)

        return f"/uploads/{out_name}"

    except Exception as exc:
        logger.warning("Text overlay failed (%r) — delivering original video", exc)
        return local_video_path


async def _delete_asset(cloud: str, api_key: str, public_id: str) -> None:
    """Best-effort delete of a Cloudinary video asset after we've downloaded it."""
    if not public_id:
        return
    try:
        timestamp = str(int(time.time()))
        sig = _sign({"public_id": public_id, "timestamp": timestamp})
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"https://api.cloudinary.com/v1_1/{cloud}/video/destroy",
                data={
                    "api_key": api_key,
                    "timestamp": timestamp,
                    "signature": sig,
                    "public_id": public_id,
                },
            )
    except Exception as exc:
        logger.debug("Cloudinary delete failed (non-critical): %r", exc)
