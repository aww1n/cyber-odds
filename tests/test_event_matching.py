from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.normalization.events import MatchableEvent, choose_event_match, score_event_pair

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


def _event(
    event_id: int,
    *,
    player1: str = "player:1",
    player2: str = "player:2",
    team1: str | None = "team:1",
    team2: str | None = "team:2",
    tournament: str = "FC 26 H2H Liga-1",
    tournament_family: str | None = None,
    started_at: datetime = NOW,
) -> MatchableEvent:
    return MatchableEvent(
        event_id=event_id,
        started_at=started_at,
        tournament_name=tournament,
        player1_key=player1,
        player2_key=player2,
        team1_key=team1,
        team2_key=team2,
        tournament_family=tournament_family,
    )


def test_exact_event_mapping_is_automatic() -> None:
    decision = choose_event_match(_event(1), [_event(2)])

    assert decision.status == "exact"
    assert decision.automatic
    assert decision.best is not None
    assert decision.best.confidence == 1.0
    assert not decision.best.reversed_sides


def test_reversed_sides_are_detected_explicitly() -> None:
    source = _event(1)
    candidate = _event(
        2,
        player1="player:2",
        player2="player:1",
        team1="team:2",
        team2="team:1",
    )

    score = score_event_pair(source, candidate)

    assert score.confidence == 1.0
    assert score.reversed_sides


def test_two_equally_good_candidates_are_ambiguous() -> None:
    source = _event(1)

    decision = choose_event_match(source, [_event(2), _event(3)])

    assert decision.status == "ambiguous"
    assert not decision.automatic


def test_low_confidence_mapping_never_becomes_automatic() -> None:
    source = _event(1)
    candidate = _event(
        2,
        player1="other:1",
        player2="other:2",
        team1="other-team:1",
        team2="other-team:2",
        tournament="Unrelated tournament",
        started_at=NOW + timedelta(hours=2),
    )

    decision = choose_event_match(source, [candidate])

    assert decision.status == "ambiguous"
    assert not decision.automatic


def test_missing_teams_do_not_penalize_otherwise_exact_mapping() -> None:
    decision = choose_event_match(
        _event(1, team1=None, team2=None),
        [_event(2, team1=None, team2=None)],
    )

    assert decision.status == "exact"
    assert decision.best is not None
    assert decision.best.confidence == 1.0


def test_verified_family_overrides_cross_source_display_names() -> None:
    source = _event(
        1,
        tournament="Stream 2 Tour №3",
        tournament_family="uel",
    )
    candidate = _event(
        2,
        tournament="FC 26. United Esports Leagues. Czechia",
        tournament_family="uel",
        started_at=NOW - timedelta(minutes=10),
    )

    decision = choose_event_match(source, [candidate])

    assert decision.status == "likely"
    assert decision.best is not None
    assert decision.best.confidence > 0.9


def test_missing_bookmaker_teams_no_longer_cap_strong_match_below_threshold() -> None:
    source = _event(1, team1="team:1", team2="team:2")
    candidate = _event(
        2,
        team1=None,
        team2=None,
        tournament_family="uel",
        started_at=NOW + timedelta(minutes=10),
    )
    source = MatchableEvent(
        event_id=source.event_id,
        started_at=source.started_at,
        tournament_name=source.tournament_name,
        player1_key=source.player1_key,
        player2_key=source.player2_key,
        team1_key=source.team1_key,
        team2_key=source.team2_key,
        tournament_family="uel",
    )

    decision = choose_event_match(source, [candidate])

    assert decision.best is not None
    assert decision.best.confidence > 0.90
    assert decision.automatic


def test_cross_source_team_name_mismatch_does_not_block_strong_match() -> None:
    source = _event(
        1,
        team1="uel-team:alpha",
        team2="uel-team:beta",
        tournament_family="uel",
    )
    candidate = _event(
        2,
        team1="fonbet-team:x",
        team2="fonbet-team:y",
        tournament_family="uel",
        started_at=NOW + timedelta(minutes=10),
    )

    decision = choose_event_match(source, [candidate])

    assert decision.best is not None
    assert decision.best.components["team1"] == 0.0
    assert decision.best.components["team2"] == 0.0
    assert decision.best.components["active_weight"] == 0.9
    assert decision.best.confidence > 0.90
    assert decision.automatic
