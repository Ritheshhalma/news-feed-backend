"""
Image storage abstraction.

Two concrete backends implement BaseImageStorage:

  S3ImageStorage   — upload to AWS S3 (public-read ACL), return HTTPS URL
  LocalImageStorage — write to MEDIA_ROOT, return /media/ URL

Routing is automatic: if AWS_S3_BUCKET is set at process startup the S3
backend is used; otherwise the local backend is used. No code change needed
to switch between them.
"""
import io
import os
from abc import ABC, abstractmethod
from pathlib import Path

import boto3
from PIL import Image

_S3_BUCKET: str | None = os.environ.get("AWS_S3_BUCKET")


# ── Image processing ──────────────────────────────────────────────────────────

def _to_jpeg(raw_bytes: bytes, max_width: int | None = None, quality: int = 82) -> bytes:
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    if max_width and img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


# ── Storage backends ──────────────────────────────────────────────────────────

class BaseImageStorage(ABC):
    """Contract that every image storage backend must satisfy."""

    @abstractmethod
    def save(self, article_id: str, data: bytes, suffix: str) -> str:
        """Persist JPEG *data* and return its public URL."""
        ...


class S3ImageStorage(BaseImageStorage):

    def save(self, article_id: str, data: bytes, suffix: str) -> str:
        key = f"news-images/{article_id}-{suffix}.jpg"
        boto3.client("s3").put_object(
            Bucket=_S3_BUCKET,
            Key=key,
            Body=data,
            ContentType="image/jpeg",
            ACL="public-read",
        )
        return f"https://{_S3_BUCKET}.s3.amazonaws.com/{key}"


class LocalImageStorage(BaseImageStorage):

    def save(self, article_id: str, data: bytes, suffix: str) -> str:
        from django.conf import settings  # lazy — settings not ready at module load
        subdir = Path(settings.MEDIA_ROOT) / "news-images"
        subdir.mkdir(parents=True, exist_ok=True)
        filename = f"{article_id}-{suffix}.jpg"
        (subdir / filename).write_bytes(data)
        return f"{settings.MEDIA_URL}news-images/{filename}"


def _get_storage() -> BaseImageStorage:
    return S3ImageStorage() if _S3_BUCKET else LocalImageStorage()


# ── Public API ────────────────────────────────────────────────────────────────

def save_image(article_id: str, raw_bytes: bytes, suffix: str, max_width: int | None = None) -> str:
    """
    Convert *raw_bytes* to JPEG and persist via the active storage backend.

    max_width=None  → original dimensions (full/detail image)
    max_width=N     → downscale to N px wide if larger (thumbnails)
    """
    data = _to_jpeg(raw_bytes, max_width=max_width)
    return _get_storage().save(article_id, data, suffix)
