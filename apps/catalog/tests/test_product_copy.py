import re

from apps.catalog.demo_content import PRODUCT_COPY


def test_product_copy_has_sources_and_written_search_descriptions():
    assert {
        "chatgpt-plus",
        "gemini-advanced",
        "claude-pro",
        "spotify-premium",
        "youtube-premium",
    } <= PRODUCT_COPY.keys()
    for slug, copy in PRODUCT_COPY.items():
        assert 0 < len(copy["short"]) <= 300, slug
        assert 0 < len(copy["title"]) <= 70, slug
        assert 0 < len(copy["description"]) <= 160, slug
        assert copy["heading"] and copy["body"] and copy["sources"], slug
        assert all(line.startswith("https://") for line in copy["sources"].splitlines()), slug
        assert not re.search(
            r"(?<!\w)(رو|می‌شه|می‌تونید|خودتون)(?!\w)", copy["title"] + " " + copy["description"]
        ), slug
    assert len({copy["short"] for copy in PRODUCT_COPY.values()}) == len(PRODUCT_COPY)


def test_each_benefit_section_closes_on_a_written_limit():
    """Benefits may be conversational; the limit that closes them is a
    commitment and stays written Persian (design language, 2026-10-27)."""
    for slug, copy in PRODUCT_COPY.items():
        limit = copy["body"].rsplit("\n\n", 1)[-1]
        assert limit.startswith("**"), slug
        assert not re.search(
            r"(?<![\w‌])(رو|می‌شه|می‌تونید|خودتون|نمی‌مونه|داره)(?![\w‌])", limit
        ), slug
