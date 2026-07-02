from articles.services.dedup import normalize_title, title_hash, content_hash


def test_normalize_title_lowercases_and_strips_punctuation():
    assert normalize_title("  Fire Breaks Out, 3 Rescued!  ") == "fire breaks out 3 rescued"


def test_title_hash_is_stable_and_deterministic():
    assert title_hash("Fire breaks out") == title_hash("Fire breaks out")
    assert title_hash("Fire breaks out") != title_hash("Flood hits city")


def test_title_hash_ignores_punctuation_and_case_differences():
    assert title_hash("Fire Breaks Out!") == title_hash("fire breaks out")


def test_content_hash_collapses_whitespace():
    assert content_hash("Hello   world") == content_hash("Hello world")
    assert content_hash("Hello world") != content_hash("Goodbye world")
