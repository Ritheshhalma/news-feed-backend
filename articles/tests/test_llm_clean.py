import json

import httpx
import pytest

from articles.services.llm_clean import CATEGORY_TAXONOMY, LLMCleanError, clean_article


def _fake_response(payload: dict, status_code: int = 200) -> httpx.Response:
    body = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    return httpx.Response(status_code, json=body, request=request)


def test_clean_article_parses_valid_response(mocker):
    fake = _fake_response({
        "title": "Clean Title", "content": "Clean body text.",
        "category": "Business", "is_new_category": False,
    })
    mocker.patch("articles.services.llm_clean.httpx.post", return_value=fake)

    result = clean_article("Garbled title extra junk", "garbled content")

    assert result.title == "Clean Title"
    assert result.content == "Clean body text."
    assert result.category == "Business"
    assert result.is_new_category is False


def test_clean_article_raises_on_malformed_json(mocker):
    body = {"choices": [{"message": {"content": "not json"}}]}
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    fake = httpx.Response(200, json=body, request=request)
    mocker.patch("articles.services.llm_clean.httpx.post", return_value=fake)

    with pytest.raises(LLMCleanError):
        clean_article("title", "content")


def test_clean_article_raises_on_missing_required_fields(mocker):
    fake = _fake_response({
        "title": "", "content": "body", "category": "Business", "is_new_category": False,
    })
    mocker.patch("articles.services.llm_clean.httpx.post", return_value=fake)

    with pytest.raises(LLMCleanError):
        clean_article("title", "content")


def test_clean_article_raises_on_http_error(mocker):
    mocker.patch(
        "articles.services.llm_clean.httpx.post",
        side_effect=httpx.HTTPError("boom"),
    )
    with pytest.raises(LLMCleanError):
        clean_article("title", "content")


def test_clean_article_raises_on_json_array_not_dict(mocker):
    """Test that a JSON array response (not an object) raises LLMCleanError."""
    body = {"choices": [{"message": {"content": json.dumps([])}}]}
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    fake = httpx.Response(200, json=body, request=request)
    mocker.patch("articles.services.llm_clean.httpx.post", return_value=fake)

    with pytest.raises(LLMCleanError):
        clean_article("title", "content")


def test_clean_article_raises_on_non_string_field(mocker):
    """Test that a non-string field value (e.g., int) raises LLMCleanError."""
    fake = _fake_response({
        "title": 123,  # int instead of string
        "content": "body",
        "category": "Business",
        "is_new_category": False,
    })
    mocker.patch("articles.services.llm_clean.httpx.post", return_value=fake)

    with pytest.raises(LLMCleanError):
        clean_article("title", "content")


def test_category_taxonomy_is_the_fixed_eighteen_categories():
    assert CATEGORY_TAXONOMY == [
        "India", "World", "Business", "Sports", "Entertainment", "Technology",
        "Science", "Health", "Education", "Politics", "Lifestyle", "Travel",
        "Cities", "Environment", "Opinion", "Real Estate", "Defence", "Auto",
    ]
