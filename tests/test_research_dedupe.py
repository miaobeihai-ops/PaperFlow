from paperflow.research_dedupe import deduplicate_research_items
from paperflow.research_models import ResearchItem, SourceRecord


def item(
    *,
    key: str,
    doi: str = "",
    arxiv_id: str = "",
    title: str = "Paper",
    year: int = 2026,
    source: str = "crossref",
    abstract: str = "",
) -> ResearchItem:
    return ResearchItem(
        key=key,
        doi=doi,
        arxiv_id=arxiv_id,
        title=title,
        authors=(),
        abstract=abstract,
        published=f"{year}-08-24",
        year=year,
        url="https://example.test/p",
        pdf_url="",
        subjects=(),
        sources=(SourceRecord(source, "https://example.test/p", key),),
    )


def test_dedup_precedence_is_doi_then_arxiv_then_normalized_title_year():
    merged = deduplicate_research_items(
        [
            item(key="a", doi="https://doi.org/10.1/ABC", source="crossref"),
            item(key="b", doi="10.1/abc", source="openalex"),
            item(key="c", arxiv_id="2608.12345v2", source="arxiv"),
            item(key="d", arxiv_id="2608.12345", source="hf-daily"),
            item(key="e", title="A  Useful: Paper", year=2026),
            item(key="f", title="a useful paper", year=2026, source="rss"),
        ]
    )

    assert len(merged) == 3
    by_key = {record.key: record for record in merged}
    assert tuple(record.name for record in by_key["doi:10.1/abc"].sources) == (
        "crossref",
        "openalex",
    )
    assert tuple(record.name for record in by_key["arxiv:2608.12345"].sources) == (
        "arxiv",
        "hf-daily",
    )
    assert "title:2026:a useful paper" in by_key


def test_record_with_multiple_identifiers_bridges_source_records():
    merged = deduplicate_research_items(
        [
            item(key="doi", doi="10.1/bridge", title="Bridge"),
            item(key="arxiv", arxiv_id="2608.33333", title="Different title", source="arxiv"),
            item(
                key="both",
                doi="10.1/bridge",
                arxiv_id="2608.33333v2",
                title="Bridge",
                source="openalex",
                abstract="complete metadata",
            ),
        ]
    )

    assert len(merged) == 1
    assert merged[0].key == "doi:10.1/bridge"
    assert merged[0].arxiv_id == "2608.33333"
    assert merged[0].abstract == "complete metadata"
    assert tuple(record.name for record in merged[0].sources) == (
        "arxiv",
        "crossref",
        "openalex",
    )


def test_dedup_is_deterministic_and_newest_first():
    records = [
        item(key="older", title="Older", year=2025),
        item(key="z", title="Zulu", year=2026),
        item(key="a", title="Alpha", year=2026),
    ]

    forward = deduplicate_research_items(records)
    reverse = deduplicate_research_items(list(reversed(records)))

    assert forward == reverse
    assert [record.title for record in forward] == ["Alpha", "Zulu", "Older"]


def test_invalid_optional_identifiers_are_discarded_not_exposed():
    merged = deduplicate_research_items(
        [item(key="unsafe", doi="not a doi", arxiv_id="../private", title="Safe title")]
    )

    assert merged[0].doi == ""
    assert merged[0].arxiv_id == ""
    assert merged[0].key == "title:2026:safe title"
