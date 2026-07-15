import pytest

from skill_registry.runtime import search_skills


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("youtube transcript", {"youtube-transcript", "youtube-full"}),
        ("technical documentation", {"docs-architect", "wiki-page-writer"}),
        ("pdf", {"pdf"}),
        (
            "spreadsheet",
            {"calc", "office-productivity", "googlesheets-automation"},
        ),
        (
            "code review",
            {"code-review-checklist", "code-review-excellence", "differential-review"},
        ),
    ],
)
def test_fixed_query_has_expected_skill_in_top_five(repo_root, query, expected):
    matches = search_skills(repo_root, query, limit=5)["matches"]
    assert expected.intersection(candidate["load_name"] for candidate in matches)
