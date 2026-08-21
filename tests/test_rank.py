from dataclasses import replace

import pytest

from paperflow.models import Paper
from paperflow.normalize import canonical_arxiv_id, deduplicate
from paperflow.rank import rank_papers


def paper(arxiv_id: str, title: str, abstract: str, *, upvotes: int = 0) -> Paper:
    return Paper(
        arxiv_id=arxiv_id,
        title=title,
        authors=("Ada Researcher",),
        abstract=abstract,
        primary_category="cs.RO",
        published="2026-08-20",
        sources=("arxiv",),
        hf_upvotes=upvotes,
        url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
    )


@pytest.mark.parametrize(
    "value",
    [
        "2608.12345",
        "2608.12345v2",
        " 2608.12345v2 ",
        "https://arxiv.org/abs/2608.12345v2",
        "http://arxiv.org/abs/2608.12345/",
        "https://arxiv.org/pdf/2608.12345v2.pdf",
        "https://arxiv.org/pdf/2608.12345.pdf/",
    ],
)
def test_canonical_arxiv_id_accepts_supported_forms(value):
    assert canonical_arxiv_id(value) == "2608.12345"


@pytest.mark.parametrize(
    "value",
    [
        "2608.123456",
        "x2608.12345y",
        "paper 2608.12345",
        "https://example.com/abs/2608.12345",
    ],
)
def test_canonical_arxiv_id_rejects_noncanonical_surrounding_text(value):
    with pytest.raises(ValueError) as exc_info:
        canonical_arxiv_id(value)

    assert str(exc_info.value) == f"invalid arXiv identifier: {value}"


def test_deduplicate_merges_sources_and_keeps_upvotes():
    first = paper("2608.12345", "Robot", "A", upvotes=0)
    second = paper("2608.12345v2", "Robot", "A", upvotes=12)
    second = second.with_sources(("hf-daily",))

    result = deduplicate([first, second])

    assert len(result) == 1
    assert result[0].sources == ("arxiv", "hf-daily")
    assert result[0].hf_upvotes == 12


def test_deduplicate_is_order_independent_and_prefers_authoritative_metadata():
    authoritative = replace(
        paper("2608.12345v2", "arXiv title", "arXiv abstract", upvotes=-8),
        authors=(),
        primary_category="",
        published="",
        pdf_url="",
    )
    daily = replace(
        paper("2608.12345", "HF Daily title", "HF Daily abstract", upvotes=200),
        authors=("Daily Author",),
        primary_category="cs.CV",
        published="2026-08-19",
        sources=("hf-daily",),
        pdf_url="https://arxiv.org/pdf/2608.12345.pdf",
    )
    trending = replace(
        paper("2608.12345", "HF Trending title", "HF Trending abstract", upvotes=200),
        authors=("Trending Author",),
        primary_category="cs.AI",
        published="2026-08-18",
        sources=("hf-trending",),
    )
    other = paper("2608.00001", "Other", "Other abstract", upvotes=-2)
    papers = [daily, other, trending, authoritative]

    forward = deduplicate(papers)
    backward = deduplicate(list(reversed(papers)))

    assert forward == backward
    assert [item.arxiv_id for item in forward] == ["2608.00001", "2608.12345"]
    merged = forward[1]
    assert merged.title == "arXiv title"
    assert merged.abstract == "arXiv abstract"
    assert merged.authors == ("Daily Author",)
    assert merged.primary_category == "cs.CV"
    assert merged.published == "2026-08-19"
    assert merged.pdf_url == "https://arxiv.org/pdf/2608.12345.pdf"
    assert merged.sources == ("arxiv", "hf-daily", "hf-trending")
    assert merged.hf_upvotes == 200
    assert forward[0].hf_upvotes == 0


def test_deduplicate_prefers_completeness_then_lexical_metadata():
    sparse = replace(
        paper("2608.12345", "A sparse record", ""),
        authors=(),
        primary_category="",
        published="",
        sources=("hf-daily",),
        url="",
        pdf_url="",
    )
    complete_z = replace(
        paper("2608.12345", "Zulu complete record", "Complete abstract"),
        sources=("hf-daily",),
    )
    complete_a = replace(
        paper("2608.12345", "Alpha complete record", "Complete abstract"),
        sources=("hf-daily",),
    )
    papers = [sparse, complete_z, complete_a]

    forward = deduplicate(papers)
    backward = deduplicate(list(reversed(papers)))

    assert forward == backward
    assert forward[0].title == "Alpha complete record"


def test_title_match_beats_popularity():
    exact = paper("2608.00001", "3D reconstruction for robots", "method")
    popular = paper("2608.00002", "General vision", "robotics", upvotes=200)

    ranked = rank_papers(
        [popular, exact],
        keywords={"3d reconstruction": 8, "robotics": 1},
        categories=("cs.RO",),
    )

    assert ranked[0].paper.arxiv_id == "2608.00001"
    assert ranked[0].matched_keywords == ("3d reconstruction",)


def test_keyword_does_not_match_inside_ascii_word():
    ranked = rank_papers(
        [paper("2608.00001", "A chair for vision", "method")],
        keywords={"ai": 4},
        categories=(),
    )

    assert ranked[0].score == 0
    assert ranked[0].matched_keywords == ()


def test_keyword_phrase_matches_when_surrounded_by_punctuation():
    ranked = rank_papers(
        [paper("2608.00001", "Vision: (3D reconstruction), revisited", "method")],
        keywords={"3d reconstruction": 8},
        categories=(),
    )

    assert ranked[0].score == 80
    assert ranked[0].matched_keywords == ("3d reconstruction",)


def test_chinese_keyword_uses_ascii_boundaries():
    ranked = rank_papers(
        [paper("2608.00001", "自主机器人系统", "method")],
        keywords={"机器人": 2},
        categories=(),
    )

    assert ranked[0].score == 20
    assert ranked[0].matched_keywords == ("机器人",)


def test_title_keyword_match_takes_precedence_over_abstract_match():
    ranked = rank_papers(
        [paper("2608.00001", "AI planning", "AI planning in robotics")],
        keywords={"ai": 2},
        categories=(),
    )

    assert ranked[0].score == 20
    assert ranked[0].matched_keywords == ("ai",)


def test_negative_upvotes_are_normalized_before_scoring_and_sorting():
    papers = [
        paper("2608.00002", "General vision", "method", upvotes=-1),
        paper("2608.00001", "General vision", "method", upvotes=-10),
    ]

    ranked = rank_papers(papers, keywords={}, categories=())

    assert [item.paper.arxiv_id for item in ranked] == ["2608.00001", "2608.00002"]
    assert [item.paper.hf_upvotes for item in ranked] == [0, 0]
    assert [item.score for item in ranked] == [0, 0]
