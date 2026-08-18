from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Literal

from app.normalization.common import normalize_text


@dataclass(frozen=True, slots=True)
class MatchableEvent:
    event_id: int
    started_at: datetime
    tournament_name: str
    player1_key: str | None
    player2_key: str | None
    team1_key: str | None = None
    team2_key: str | None = None
    tournament_family: str | None = None


@dataclass(frozen=True, slots=True)
class PairScore:
    candidate_event_id: int
    confidence: float
    reversed_sides: bool
    components: dict[str, float | bool]


@dataclass(frozen=True, slots=True)
class EventMatchDecision:
    status: Literal["exact", "likely", "ambiguous", "rejected"]
    best: PairScore | None
    second_best_confidence: float

    @property
    def automatic(self) -> bool:
        return self.status in {"exact", "likely"}


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _identity_score(left: str | None, right: str | None) -> float:
    if left is None and right is None:
        return 0.5
    if left is None or right is None:
        return 0.0
    return 1.0 if left == right else 0.0


def _time_score(left: datetime, right: datetime) -> tuple[float, float]:
    seconds = abs((_aware_utc(left) - _aware_utc(right)).total_seconds())
    if seconds <= 60:
        return 1.0, seconds
    if seconds <= 300:
        return 1.0 - (seconds - 60) / 1200, seconds
    if seconds <= 900:
        return 0.8 - 0.3 * (seconds - 300) / 600, seconds
    if seconds <= 1800:
        return 0.5 * (1800 - seconds) / 900, seconds
    return 0.0, seconds


def _tournament_score(left: str, right: str) -> float:
    left_normalized = normalize_text(left)
    right_normalized = normalize_text(right)
    if not left_normalized or not right_normalized:
        return 0.0
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def score_event_pair(
    source: MatchableEvent,
    candidate: MatchableEvent,
) -> PairScore:
    if source.tournament_family and candidate.tournament_family:
        tournament = (
            1.0 if source.tournament_family == candidate.tournament_family else 0.0
        )
    else:
        tournament = _tournament_score(source.tournament_name, candidate.tournament_name)
    time, time_delta_seconds = _time_score(source.started_at, candidate.started_at)

    def direction_score(reversed_sides: bool) -> tuple[float, dict[str, float | bool]]:
        candidate_player1 = (
            candidate.player2_key if reversed_sides else candidate.player1_key
        )
        candidate_player2 = (
            candidate.player1_key if reversed_sides else candidate.player2_key
        )
        candidate_team1 = candidate.team2_key if reversed_sides else candidate.team1_key
        candidate_team2 = candidate.team1_key if reversed_sides else candidate.team2_key
        player1 = _identity_score(source.player1_key, candidate_player1)
        player2 = _identity_score(source.player2_key, candidate_player2)
        team1 = _identity_score(source.team1_key, candidate_team1)
        team2 = _identity_score(source.team2_key, candidate_team2)

        # Cross-source team labels are too inconsistent to be reliable evidence
        # for event identity in this market. Keep team1/team2 as diagnostic
        # components, but do not let team-name disagreements suppress an
        # otherwise strong player+tournament+time match.
        weighted = [
            (0.25, player1),
            (0.25, player2),
            (0.20, tournament),
            (0.20, time),
        ]
        active_weight = sum(weight for weight, _ in weighted)
        confidence = sum(weight * value for weight, value in weighted) / active_weight
        return confidence, {
            "player1": player1,
            "player2": player2,
            "team1": team1,
            "team2": team2,
            "tournament": tournament,
            "time": time,
            "time_delta_seconds": time_delta_seconds,
            "reversed_sides": reversed_sides,
            "active_weight": active_weight,
        }

    direct_confidence, direct_components = direction_score(False)
    reverse_confidence, reverse_components = direction_score(True)
    if reverse_confidence > direct_confidence:
        confidence, components, reversed_sides = (
            reverse_confidence,
            reverse_components,
            True,
        )
    else:
        confidence, components, reversed_sides = (
            direct_confidence,
            direct_components,
            False,
        )
    return PairScore(
        candidate_event_id=candidate.event_id,
        confidence=confidence,
        reversed_sides=reversed_sides,
        components=components,
    )


def choose_event_match(
    source: MatchableEvent,
    candidates: list[MatchableEvent],
    *,
    automatic_threshold: float = 0.90,
    exact_threshold: float = 0.985,
    ambiguity_margin: float = 0.03,
) -> EventMatchDecision:
    if not 0 <= automatic_threshold <= exact_threshold <= 1:
        raise ValueError("thresholds must satisfy 0 <= automatic <= exact <= 1")
    if not 0 <= ambiguity_margin <= 1:
        raise ValueError("ambiguity_margin must be between zero and one")

    ranked = sorted(
        (score_event_pair(source, candidate) for candidate in candidates),
        key=lambda score: (-score.confidence, score.candidate_event_id),
    )
    if not ranked:
        return EventMatchDecision("rejected", None, 0.0)
    best = ranked[0]
    second = ranked[1].confidence if len(ranked) > 1 else 0.0
    if best.confidence < automatic_threshold:
        return EventMatchDecision("ambiguous", best, second)
    if best.confidence - second < ambiguity_margin:
        return EventMatchDecision("ambiguous", best, second)
    status: Literal["exact", "likely"] = (
        "exact" if best.confidence >= exact_threshold else "likely"
    )
    return EventMatchDecision(status, best, second)
