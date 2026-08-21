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


def test_canonical_arxiv_id_removes_url_and_version():
    assert canonical_arxiv_id("https://arxiv.org/abs/2608.12345v2") == "2608.12345"


def test_deduplicate_merges_sources_and_keeps_upvotes():
    first = paper("2608.12345", "Robot", "A", upvotes=0)
    second = paper("2608.12345v2", "Robot", "A", upvotes=12)
    second = second.with_sources(("hf-daily",))

    result = deduplicate([first, second])

    assert len(result) == 1
    assert result[0].sources == ("arxiv", "hf-daily")
    assert result[0].hf_upvotes == 12


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
