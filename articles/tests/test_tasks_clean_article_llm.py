import pytest
from django.db import IntegrityError

from articles.models import Article, MSTArticleCategory, MSTArticlePortal
from articles.services.llm_clean import CleanResult, LLMCleanError
from articles.tasks import clean_article_llm

pytestmark = pytest.mark.django_db


def _make_article(portal, **kwargs):
    defaults = dict(
        title="Garbled Title Extra Text", source_url="https://example.com/clean-1",
        hashed_key="hash-clean-1", content="garbled content",
        content_hash="hash-clean-1-content", portal=portal,
    )
    defaults.update(kwargs)
    return Article.objects.create(**defaults)


def test_clean_article_llm_updates_article_on_success(mocker):
    portal = MSTArticlePortal.objects.create(name="Clean Portal 1")
    article = _make_article(portal)
    mocker.patch(
        "articles.tasks.llm_clean.clean_article",
        return_value=CleanResult(
            title="Clean Title", content="Clean body",
            category="Business", is_new_category=False,
        ),
    )

    clean_article_llm(str(article.id))

    article.refresh_from_db()
    assert article.title == "Clean Title"
    assert article.content == "Clean body"
    assert article.category.name == "Business"
    assert article.category.is_llm_suggested is False
    assert article.llm_clean_status == "success"
    assert article.llm_cleaned_at is not None
    assert article.hashed_key == "hash-clean-1"  # untouched — critical invariant
    assert article.content_hash == "hash-clean-1-content"  # untouched — critical invariant


def test_clean_article_llm_creates_new_flagged_category(mocker):
    portal = MSTArticlePortal.objects.create(name="Clean Portal 2")
    article = _make_article(
        portal, source_url="https://example.com/clean-2", hashed_key="hash-clean-2",
    )
    mocker.patch(
        "articles.tasks.llm_clean.clean_article",
        return_value=CleanResult(
            title="T", content="C", category="Esports", is_new_category=True,
        ),
    )

    clean_article_llm(str(article.id))

    category = MSTArticleCategory.objects.get(name="Esports")
    assert category.is_llm_suggested is True


def test_clean_article_llm_reuses_existing_category_case_insensitively(mocker):
    portal = MSTArticlePortal.objects.create(name="Clean Portal 3")
    existing_category = MSTArticleCategory.objects.create(name="Business", is_llm_suggested=False)
    article = _make_article(
        portal, source_url="https://example.com/clean-3", hashed_key="hash-clean-3",
    )
    mocker.patch(
        "articles.tasks.llm_clean.clean_article",
        return_value=CleanResult(
            title="T", content="C", category="business", is_new_category=False,
        ),
    )

    clean_article_llm(str(article.id))

    article.refresh_from_db()
    assert article.category_id == existing_category.id
    assert MSTArticleCategory.objects.filter(name__iexact="business").count() == 1


def test_clean_article_llm_marks_failed_and_reraises_on_error(mocker):
    portal = MSTArticlePortal.objects.create(name="Clean Portal 4")
    article = _make_article(
        portal, source_url="https://example.com/clean-4", hashed_key="hash-clean-4",
    )
    mocker.patch("articles.tasks.llm_clean.clean_article", side_effect=LLMCleanError("boom"))

    with pytest.raises(LLMCleanError):
        clean_article_llm(str(article.id))

    article.refresh_from_db()
    assert article.llm_clean_status == "failed"
    assert article.title == "Garbled Title Extra Text"  # untouched on failure
    assert article.hashed_key == "hash-clean-4"


def test_clean_article_llm_recovers_from_concurrent_category_creation_race(mocker):
    """Regression test: two concurrent clean_article_llm runs can both find no
    existing category and both call .create() — the loser hits IntegrityError
    on the unique `name` constraint. The task must recover by re-fetching the
    category the winner just created, instead of crashing.
    """
    portal = MSTArticlePortal.objects.create(name="Clean Portal 5")
    article = _make_article(
        portal, source_url="https://example.com/clean-5", hashed_key="hash-clean-5",
    )
    mocker.patch(
        "articles.tasks.llm_clean.clean_article",
        return_value=CleanResult(
            title="T", content="C", category="Sports", is_new_category=True,
        ),
    )

    original_create = MSTArticleCategory.objects.create

    def create_and_raise(**kwargs):
        # Simulate a concurrent request winning the race: it creates the row
        # first (as if in another transaction), and this request then hits
        # the unique constraint on `name`.
        original_create(**kwargs)
        raise IntegrityError("duplicate key")

    mock_create = mocker.patch.object(
        MSTArticleCategory.objects, "create", side_effect=create_and_raise,
    )

    clean_article_llm(str(article.id))

    winner_category = MSTArticleCategory.objects.get(name="Sports")
    article.refresh_from_db()
    assert article.category_id == winner_category.id
    assert article.llm_clean_status == "success"
    assert mock_create.call_count == 1
    assert MSTArticleCategory.objects.filter(name="Sports").count() == 1
