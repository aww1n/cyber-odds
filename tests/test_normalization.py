from __future__ import annotations

from app.normalization import Candidate, decide_normalization, normalize_player_name


def test_player_variants_have_one_exact_normalized_key() -> None:
    variants = [
        "PRITISTREET",
        "pritistreet",
        "Pritistreet",
        "Испания (pritistreet)",
        "Spain (PRITISTREET)",
    ]

    assert {normalize_player_name(value) for value in variants} == {"pritistreet"}


def test_suspicious_fuzzy_player_requires_review() -> None:
    candidates = [Candidate(7, "PRITISTREET", "pritistreet")]

    decision = decide_normalization("pritistret", candidates)

    assert decision.action == "review"
    assert decision.candidate_id == 7


def test_distant_name_is_created_without_forced_merge() -> None:
    candidates = [Candidate(7, "PRITISTREET", "pritistreet")]

    decision = decide_normalization("blackstar98", candidates)

    assert decision.action == "new"
    assert decision.candidate_id is None


def test_close_competing_candidates_are_never_auto_merged() -> None:
    candidates = [
        Candidate(1, "long-player-name-a", "long-player-name-a"),
        Candidate(2, "long-player-name-b", "long-player-name-b"),
    ]

    decision = decide_normalization(
        "long-player-name-c",
        candidates,
        auto_fuzzy_threshold=0.90,
    )

    assert decision.action == "review"
