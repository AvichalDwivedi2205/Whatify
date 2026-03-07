from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.contracts import RealityAnchorCard, RealityResponse


@dataclass(frozen=True)
class HistorianSource:
    title: str
    citation: str
    keywords: tuple[str, ...]


_SOURCES: tuple[HistorianSource, ...] = (
    HistorianSource(
        title="Library of Alexandria historical decline",
        citation="Encyclopaedia Britannica, 'Library of Alexandria'",
        keywords=("alexandria", "library", "hellenistic", "ptolemaic", "rome"),
    ),
    HistorianSource(
        title="Knowledge diffusion in the medieval Mediterranean",
        citation="UNESCO Memory of the World - documentary heritage references",
        keywords=("knowledge", "mediterranean", "translation", "scholar", "archive"),
    ),
    HistorianSource(
        title="State centralization and information control",
        citation="Cambridge economic and institutional history surveys",
        keywords=("state", "centralize", "governance", "institution", "censorship"),
    ),
    HistorianSource(
        title="Trade and diplomacy in late antiquity",
        citation="Oxford Classical Dictionary entries on Eastern Mediterranean trade",
        keywords=("trade", "diplomacy", "harbor", "empire", "alliance"),
    ),
)


def apply_historian_grounding(*, topic: str, reality: RealityResponse, limit: int = 3) -> RealityResponse:
    citations = retrieve_citations(topic=topic, limit=limit)
    cards = list(reality.cards)

    if cards:
        grounded_cards: list[RealityAnchorCard] = []
        for index, card in enumerate(cards):
            if card.citation:
                grounded_cards.append(card)
                continue
            fallback = citations[index % len(citations)] if citations else "Grounding source unavailable"
            grounded_cards.append(
                RealityAnchorCard(
                    title=card.title,
                    bullet=card.bullet,
                    citation=fallback,
                )
            )
        cards = grounded_cards
    elif citations:
        cards = [
            RealityAnchorCard(
                title="Grounding references",
                bullet="References automatically attached for historian comparison traceability.",
                citation=citations[0],
            )
        ]

    return RealityResponse(cards=cards, comparison_points=reality.comparison_points)


def retrieve_citations(*, topic: str, limit: int = 3) -> list[str]:
    tokens = set(_tokenize(topic))
    if not tokens:
        return [source.citation for source in _SOURCES[:limit]]

    scored = sorted(
        _SOURCES,
        key=lambda source: _source_score(source, tokens),
        reverse=True,
    )
    selected = [source.citation for source in scored if _source_score(source, tokens) > 0]
    if selected:
        return selected[:limit]
    return [source.citation for source in _SOURCES[:limit]]


def _source_score(source: HistorianSource, tokens: set[str]) -> int:
    return len(tokens.intersection(source.keywords))


def _tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", text.lower()) if token]
