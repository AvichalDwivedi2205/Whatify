from __future__ import annotations

from app.core.historian_grounding import apply_historian_grounding, retrieve_citations
from app.models.contracts import ComparisonPoint, RealityAnchorCard, RealityResponse


def test_retrieve_citations_returns_ranked_results() -> None:
    citations = retrieve_citations(topic="Alexandria library governance", limit=2)
    assert len(citations) == 2
    assert all(isinstance(item, str) and item for item in citations)


def test_apply_historian_grounding_fills_missing_citations() -> None:
    reality = RealityResponse(
        cards=[RealityAnchorCard(title="Anchor", bullet="Fact", citation=None)],
        comparison_points=[ComparisonPoint(changed_fact="A", real_fact="B")],
    )

    grounded = apply_historian_grounding(topic="Alexandria", reality=reality)

    assert grounded.cards
    assert grounded.cards[0].citation is not None
    assert grounded.cards[0].citation != ""
