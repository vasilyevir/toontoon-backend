"""Image processing on the way in: strip metadata, measure, make a thumbnail.

Two things happen here that are easy to skip and expensive to add later.

**EXIF is removed.** Photos from a phone carry GPS coordinates of where they
were taken. We keep reference photos indefinitely and show them back in a
gallery (CH-18), so storing the shooting location next to a face is exactly what
must not happen. Re-encoding without metadata costs one call.

**A thumbnail is produced.** The history grid shows three images per row; making
it download full-size results is the difference between a screen that opens and
one that stalls on mobile data.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Optional

from PIL import Image

from app.config import settings

# Pillow refuses absurd images by default; keep that guard rail.
Image.MAX_IMAGE_PIXELS = 64_000_000


@dataclass(slots=True)
class ProcessedImage:
    data: bytes
    mime: str
    width: int
    height: int
    sha256: str
    thumbnail: Optional[bytes]
    thumbnail_mime: str = "image/jpeg"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def preview(data: bytes, *, side: int = 384) -> bytes:
    """Мелкая копия для разбора моделью.

    Понять, портрет это или постер, можно и по картинке в триста пикселей, а
    платим мы за каждый. Снимок с телефона на четыре мегапикселя стоил бы здесь
    в разы дороже ответа, который он даёт.
    """
    with Image.open(io.BytesIO(data)) as img:
        img = _apply_orientation(img).convert("RGB")
        img.thumbnail((side, side))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=70)
        return out.getvalue()


def process(data: bytes, *, make_thumbnail: bool = True) -> ProcessedImage:
    """Normalise an uploaded or generated image.

    Returns the cleaned bytes — metadata removed, orientation applied — plus the
    dimensions, content hash and an optional thumbnail.
    """
    with Image.open(io.BytesIO(data)) as img:
        # Apply the EXIF orientation flag before dropping EXIF, otherwise photos
        # taken sideways stay sideways forever.
        img = _apply_orientation(img)
        fmt = (img.format or "PNG").upper()
        has_alpha = img.mode in ("RGBA", "LA", "P")

        if has_alpha:
            clean = img.convert("RGBA")
            out_format, mime = "PNG", "image/png"
        else:
            clean = img.convert("RGB")
            out_format, mime = ("PNG", "image/png") if fmt == "PNG" else ("JPEG", "image/jpeg")

        buf = io.BytesIO()
        # Passing no exif/icc argument is what actually drops the metadata.
        if out_format == "JPEG":
            clean.save(buf, format="JPEG", quality=92, optimize=True)
        else:
            clean.save(buf, format="PNG", optimize=True)
        cleaned = buf.getvalue()

        thumb: Optional[bytes] = None
        if make_thumbnail:
            preview = clean.convert("RGB")
            preview.thumbnail(
                (settings.thumbnail_max_side, settings.thumbnail_max_side),
                Image.Resampling.LANCZOS,
            )
            tbuf = io.BytesIO()
            preview.save(tbuf, format="JPEG", quality=settings.thumbnail_quality, optimize=True)
            thumb = tbuf.getvalue()

        return ProcessedImage(
            data=cleaned,
            mime=mime,
            width=clean.width,
            height=clean.height,
            sha256=sha256_hex(cleaned),
            thumbnail=thumb,
        )


def _apply_orientation(img: "Image.Image") -> "Image.Image":
    try:
        exif = img.getexif()
        orientation = exif.get(274)  # 274 == EXIF Orientation
    except Exception:  # noqa: BLE001 — malformed EXIF must not fail an upload
        return img
    rotations = {3: 180, 6: 270, 8: 90}
    if orientation in rotations:
        return img.rotate(rotations[orientation], expand=True)
    return img


def has_metadata(data: bytes) -> bool:
    """Test helper: does this image still carry EXIF? Used to prove the strip."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return bool(img.getexif())
    except Exception:  # noqa: BLE001
        return False
