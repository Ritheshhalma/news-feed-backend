import hashlib
import re
import unicodedata


def normalize_title(title: str) -> str:
    title = unicodedata.normalize("NFKC", title)
    title = title.lower().strip()
    title = re.sub(r"[^\w\s]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title


def title_hash(title: str) -> str:
    """Dedupe identity for an Article — see design doc §1.2/§3."""
    return hashlib.sha256(normalize_title(title).encode()).hexdigest()


def content_hash(body: str) -> str:
    """Change-detection fingerprint, independent of the dedupe identity."""
    normalized = re.sub(r"\s+", " ", body.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()
