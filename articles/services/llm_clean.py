"""DeepSeek-based article title/content repair and category classification.
See docs/superpowers/specs/2026-07-05-deepseek-article-cleaning-design.md.
"""
import json
from dataclasses import dataclass

import httpx
from django.conf import settings

CATEGORY_TAXONOMY = [
    "India", "World", "Business", "Sports", "Entertainment", "Technology",
    "Science", "Health", "Education", "Politics", "Lifestyle", "Travel",
    "Cities", "Environment", "Opinion", "Real Estate", "Defence", "Auto",
]

_SYSTEM_PROMPT = (
    "You clean scraped news article data for a news aggregator. You will be "
    "given a raw scraped title and content, which may contain scraping "
    "artifacts: unrelated text glued onto the title, leftover snippets from "
    "other stories, truncated sentences, or boilerplate. Your job:\n"
    "1. Produce a clean title containing only this article's actual headline.\n"
    "2. Produce clean content with artifacts removed, preserving the real "
    "article body as-is — do not summarize or rewrite meaningfully.\n"
    "3. Classify the article into exactly one of these categories: "
    f"{', '.join(CATEGORY_TAXONOMY)}. Only if truly none fit, propose a new, "
    "concise category name instead and set is_new_category to true.\n"
    'Respond with strict JSON only: {"title": str, "content": str, '
    '"category": str, "is_new_category": bool}'
)


@dataclass
class CleanResult:
    title: str
    content: str
    category: str
    is_new_category: bool


class LLMCleanError(Exception):
    """Raised on a DeepSeek API failure or an invalid/malformed response."""


def build_prompt(title: str, content: str) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"TITLE: {title}\n\nCONTENT: {content}"},
    ]


def clean_article(title: str, content: str) -> CleanResult:
    messages = build_prompt(title, content)
    try:
        response = httpx.post(
            f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
            json={
                "model": settings.DEEPSEEK_MODEL,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            },
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMCleanError(f"DeepSeek API request failed: {exc}") from exc

    try:
        raw_content = response.json()["choices"][0]["message"]["content"]
        data = json.loads(raw_content)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise LLMCleanError(f"DeepSeek returned an unparseable response: {exc}") from exc

    cleaned_title = (data.get("title") or "").strip()
    cleaned_content = (data.get("content") or "").strip()
    category = (data.get("category") or "").strip()
    is_new_category = bool(data.get("is_new_category", False))

    if not cleaned_title or not cleaned_content or not category:
        raise LLMCleanError(f"DeepSeek response missing required fields: {data!r}")

    return CleanResult(
        title=cleaned_title,
        content=cleaned_content,
        category=category,
        is_new_category=is_new_category,
    )
