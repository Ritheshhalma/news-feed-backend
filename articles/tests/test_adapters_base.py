import pytest
from articles.adapters.base import BaseAdapter, RawArticle


class _FakeAdapter(BaseAdapter):
    def fetch(self):
        return [RawArticle(
            title="Sample", source_url="https://example.com/1",
            content="body", image_url=None, published_at=None, category_name=None,
        )]


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


def test_concrete_adapter_implements_fetch():
    results = _FakeAdapter().fetch()
    assert len(results) == 1
    assert results[0].title == "Sample"
