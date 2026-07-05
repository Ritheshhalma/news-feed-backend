import pytest
from articles.adapters.base import BaseAdapter, RawArticle


class _FakeAdapter(BaseAdapter):
    """Minimal concrete implementation used only in tests."""

    def fetch(self, force: bool = False) -> list[RawArticle]:
        return [RawArticle(
            title="Sample", source_url="https://example.com/1",
            content="body", image_url=None, published_at=None, category_name=None,
        )]

    def validate(self) -> None:
        pass  # always passes in tests


def test_raw_article_is_a_plain_dataclass_with_expected_fields():
    raw = RawArticle(
        title="T", source_url="https://x.com", content="C",
        image_url="https://x.com/i.jpg", published_at="2026-06-24T00:00:00Z", category_name="Sports",
    )
    assert raw.title == "T"
    assert raw.category_name == "Sports"


def test_base_adapter_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        BaseAdapter()


def test_concrete_adapter_implements_fetch_with_force_flag():
    adapter = _FakeAdapter()
    results = adapter.fetch()
    assert len(results) == 1
    assert results[0].title == "Sample"

    results_force = adapter.fetch(force=True)
    assert len(results_force) == 1


def test_concrete_adapter_validate_does_not_raise():
    _FakeAdapter().validate()


def test_partial_implementation_raises_type_error_missing_validate():
    """A class that only implements fetch() but not validate() cannot be instantiated."""
    class _OnlyFetch(BaseAdapter):
        def fetch(self, force: bool = False) -> list[RawArticle]:
            return []

    with pytest.raises(TypeError):
        _OnlyFetch()


def test_partial_implementation_raises_type_error_missing_fetch():
    """A class that only implements validate() but not fetch() cannot be instantiated."""
    class _OnlyValidate(BaseAdapter):
        def validate(self) -> None:
            pass

    with pytest.raises(TypeError):
        _OnlyValidate()
