from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class RawArticle:
    title: str
    source_url: str
    content: str
    image_url: str | None
    published_at: str | None
    category_name: str | None
    author_name: str | None = None
    tags: list[str] = field(default_factory=list)


class BaseAdapter(ABC):
    """Shared interface every source adapter (RSS, HTML, ...) implements."""

    def __init__(self, source=None):
        self.source = source

    @abstractmethod
    def fetch(self) -> list[RawArticle]:
        """Return raw articles fetched from self.source.url. Must not raise
        for individual-item parse failures — skip and continue; only raise
        for fetch-level failures (network error, unparseable feed)."""
        ...
