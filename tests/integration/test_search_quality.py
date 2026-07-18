import pytest

from skill_registry.runtime import search_skills


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("youtube transcript", {"youtube-transcript", "youtube-full"}),
        ("technical documentation", {"docs-architect", "wiki-page-writer"}),
        ("pdf", {"pdf-official"}),
        (
            "spreadsheet",
            {"calc", "office-productivity", "googlesheets-automation"},
        ),
        (
            "code review",
            {"code-review-checklist", "code-review-excellence", "differential-review"},
        ),
        ("azure blob storage", {"azure-blob-storage"}),
    ],
)
def test_fixed_query_has_expected_skill_in_top_five(repo_root, query, expected):
    matches = search_skills(repo_root, query, limit=5)["matches"]
    assert expected.intersection(candidate["load_name"] for candidate in matches)


@pytest.mark.parametrize(
    ("query", "canonical", "legacy"),
    [
        ("docx", "docx-official", "docx"),
        ("pdf", "pdf-official", "pdf"),
        ("pptx", "pptx-official", "pptx"),
        ("xlsx", "xlsx-official", "xlsx"),
    ],
)
def test_exact_office_duplicates_only_return_canonical(repo_root, query, canonical, legacy):
    matches = search_skills(repo_root, query, limit=50)["matches"]
    names = {candidate["load_name"] for candidate in matches}
    assert canonical in names
    assert legacy not in names
