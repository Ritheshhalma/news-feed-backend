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
    """
    Contract every source adapter must satisfy.

    Concrete subclasses (RSSAdapter, HTMLAdapter, PlaywrightAdapter) implement
    two methods:

    fetch(force)  — return articles from self.source.url.
                    force=False (default): skip URLs already in the database.
                    force=True: re-fetch and re-extract all discovered URLs.

    validate()    — prove self.source.url is reachable and parseable.
                    Called once during source onboarding; raises on failure.
    """

    def __init__(self, source=None):
        self.source = source

    @abstractmethod
    def fetch(self, force: bool = False) -> list[RawArticle]:
        """
        Return raw articles from self.source.url.

        Must not raise for individual item parse failures — skip and continue.
        Only raise for fetch-level failures (network error, unparseable feed).
        """
        ...

    @abstractmethod
    def validate(self) -> None:
        """
        Validate that self.source.url is reachable and returns parseable content.
        Raises an exception whose message is stored in source.error_message.
        """
        ...
