"""
Image storage abstraction.

Routing (checked once at import time):
  AWS_S3_BUCKET set  →  upload to S3, return https://…s3.amazonaws.com/…
  AWS_S3_BUCKET unset →  save to MEDIA_ROOT, return /media/…

Production only needs to add AWS_S3_BUCKET (+ AWS credentials) to the env;
no code change required.
"""
import io
import os
from pathlib import Path

import boto3
from PIL import Image

_S3_BUCKET: str | None = os.environ.get("AWS_S3_BUCKET")


# ── Image processing ──────────────────────────────────────────────────────────

def _to_jpeg(raw_bytes: bytes, max_width: int | None = None, quality: int = 82) -> bytes:
    """Convert to JPEG. When max_width is None the image is stored at original dimensions."""
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    if max_width and img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


# ── Storage backends ──────────────────────────────────────────────────────────

def _save_s3(article_id: str, data: bytes, suffix: str) -> str:
    key = f"news-images/{article_id}-{suffix}.jpg"
    boto3.client("s3").put_object(
        Bucket=_S3_BUCKET,
        Key=key,
        Body=data,
        ContentType="image/jpeg",
        ACL="public-read",
    )
    return f"https://{_S3_BUCKET}.s3.amazonaws.com/{key}"


def _save_local(article_id: str, data: bytes, suffix: str) -> str:
    from django.conf import settings  # lazy import — settings not ready at module load
    subdir = Path(settings.MEDIA_ROOT) / "news-images"
    subdir.mkdir(parents=True, exist_ok=True)
    filename = f"{article_id}-{suffix}.jpg"
    (subdir / filename).write_bytes(data)
    return f"{settings.MEDIA_URL}news-images/{filename}"


# ── Public API ────────────────────────────────────────────────────────────────

def save_image(article_id: str, raw_bytes: bytes, suffix: str, max_width: int | None = None) -> str:
    """
    Convert *raw_bytes* to JPEG and persist it, returning the public URL.

    max_width=None  → original dimensions (used for full/detail image)
    max_width=N     → downscale to N px wide if larger (used for thumbnails)

    Storage routing (no code change needed to switch):
      AWS_S3_BUCKET set   → S3  (public-read ACL)
      AWS_S3_BUCKET unset → local MEDIA_ROOT  (served via /media/)
    """
    data = _to_jpeg(raw_bytes, max_width=max_width)
    return _save_s3(article_id, data, suffix) if _S3_BUCKET else _save_local(article_id, data, suffix)
