from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.features import RESEARCH_WINDOWS, FeatureBuilder, MatchRecord, TargetContext

CUTOFF = datetime(2026, 8, 17, 12, tzinfo=UTC)


def _record(
    event_id: int,
    *,
    started_at: datetime,
    available_at: datetime,
    player1: str = "A",
    player2: str = "C",
    score1: int = 2,
    score2: int = 1,
    sport: str = "football",
) -> MatchRecord:
    return MatchRecord(
        event_id=event_id,
        started_at=started_at,
        available_at=available_at,
        source="esb",
        sport=sport,
        game="FC26",
        tournament_key="liga-1",
        format="2x4",
        player1_key=player1,
        player2_key=player2,
        team1_key="Spain",
        team2_key="Italy",
        score1=score1,
        score2=score2,
    )


def _target() -> TargetContext:
    return TargetContext(
        event_id=100,
        cutoff_at=CUTOFF,
        source="esb",
        sport="football",
        game="FC26",
        tournament_key="liga-1",
        format="2x4",
        player1_key="A",
        player2_key="B",
        team1_key="Spain",
        team2_key="France",
    )


def test_feature_builder_excludes_every_record_not_available_before_cutoff() -> None:
    history = [
        _record(
            1,
            started_at=CUTOFF - timedelta(minutes=20),
            available_at=CUTOFF - timedelta(minutes=10),
        ),
        _record(
            2,
            started_at=CUTOFF,
            available_at=CUTOFF - timedelta(minutes=1),
        ),
        _record(
            3,
            started_at=CUTOFF - timedelta(minutes=30),
            available_at=CUTOFF,
        ),
        _record(
            4,
            started_at=CUTOFF - timedelta(minutes=40),
            available_at=CUTOFF + timedelta(seconds=1),
        ),
    ]

    features = FeatureBuilder(history).build(_target())

    assert features.eligible_match_ids == (1,)
    assert features.values["p1_global_last_5_matches"] == 1
    assert features.values["p1_global_last_5_win_rate"] == 1


def test_features_are_perspective_correct_for_reversed_h2h() -> None:
    history = [
        _record(
            1,
            started_at=CUTOFF - timedelta(hours=1),
            available_at=CUTOFF - timedelta(minutes=45),
            player1="B",
            player2="A",
            score1=1,
            score2=3,
        )
    ]

    features = FeatureBuilder(history).build(_target())

    assert features.values["h2h_last_5_matches"] == 1
    assert features.values["h2h_last_5_win_rate"] == 1
    assert features.values["h2h_last_5_goals_for_avg"] == 3
    assert features.values["h2h_same_direction_last_5_matches"] == 0


def test_other_sport_is_not_mixed_into_global_player_form() -> None:
    history = [
        _record(
            1,
            started_at=CUTOFF - timedelta(hours=1),
            available_at=CUTOFF - timedelta(minutes=45),
            sport="hockey",
        )
    ]

    features = FeatureBuilder(history).build(_target())

    assert features.values["p1_global_all_matches"] == 0


def test_all_research_history_windows_are_generated() -> None:
    features = FeatureBuilder([]).build(_target())

    assert RESEARCH_WINDOWS == (5, 10, 20, 25, 30, 50, 75, 100, 200)
    for window in RESEARCH_WINDOWS:
        assert f"p1_global_last_{window}_matches" in features.values
        assert f"h2h_last_{window}_matches" in features.values
