from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

ZERO_WIDTH = re.compile("[\u200b-\u200d\ufeff]")
WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = ZERO_WIDTH.sub("", normalized).casefold().strip()
    return WHITESPACE.sub(" ", normalized)


@dataclass(frozen=True, slots=True)
class Candidate:
    entity_id: int
    display_name: str
    normalized_name: str


@dataclass(frozen=True, slots=True)
class NormalizationDecision:
    action: Literal["exact", "fuzzy", "review", "new"]
    normalized_value: str
    candidate_id: int | None
    confidence: float
    second_best_confidence: float


def decide_normalization(
    normalized_value: str,
    candidates: list[Candidate],
    *,
    auto_fuzzy_threshold: float = 0.985,
    review_threshold: float = 0.80,
    minimum_margin: float = 0.05,
) -> NormalizationDecision:
    if not normalized_value:
        raise ValueError("normalized_value must not be empty")
    if not 0 <= review_threshold <= auto_fuzzy_threshold <= 1:
        raise ValueError("normalization thresholds must satisfy 0 <= review <= auto <= 1")
    if not 0 <= minimum_margin <= 1:
        raise ValueError("minimum_margin must be between zero and one")

    exact = [item for item in candidates if item.normalized_name == normalized_value]
    if len(exact) == 1:
        return NormalizationDecision("exact", normalized_value, exact[0].entity_id, 1.0, 0.0)
    if len(exact) > 1:
        return NormalizationDecision("review", normalized_value, exact[0].entity_id, 1.0, 1.0)

    ranked = sorted(
        (
            (
                SequenceMatcher(None, normalized_value, item.normalized_name).ratio(),
                item,
            )
            for item in candidates
        ),
        key=lambda pair: (-pair[0], pair[1].entity_id),
    )
    if not ranked:
        return NormalizationDecision("new", normalized_value, None, 0.0, 0.0)

    best_score, best = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    margin = best_score - second_score
    long_enough = len(normalized_value) >= 5 and len(best.normalized_name) >= 5
    if best_score >= auto_fuzzy_threshold and margin >= minimum_margin and long_enough:
        return NormalizationDecision(
            "fuzzy", normalized_value, best.entity_id, best_score, second_score
        )
    if best_score >= review_threshold:
        return NormalizationDecision(
            "review", normalized_value, best.entity_id, best_score, second_score
        )
    return NormalizationDecision("new", normalized_value, None, best_score, second_score)
