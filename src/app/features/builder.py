from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from statistics import fmean, median, pstdev

TOTAL_LINES = (1.5, 2.5, 3.5, 4.5, 5.5, 6.5)
RESEARCH_WINDOWS = (5, 10, 20, 25, 30, 50, 75, 100, 200)
DEFAULT_PLAYER_WINDOWS = RESEARCH_WINDOWS
DEFAULT_H2H_WINDOWS = RESEARCH_WINDOWS


@dataclass(frozen=True, slots=True)
class MatchRecord:
    event_id: int
    started_at: datetime
    available_at: datetime
    source: str
    sport: str
    game: str | None
    tournament_key: str | None
    format: str | None
    player1_key: str
    player2_key: str
    team1_key: str | None
    team2_key: str | None
    score1: int
    score2: int


@dataclass(frozen=True, slots=True)
class TargetContext:
    event_id: int
    cutoff_at: datetime
    source: str
    sport: str
    game: str | None
    tournament_key: str | None
    format: str | None
    player1_key: str
    player2_key: str
    team1_key: str | None
    team2_key: str | None


@dataclass(frozen=True, slots=True)
class FeatureSet:
    event_id: int
    cutoff_at: datetime
    eligible_match_ids: tuple[int, ...]
    values: dict[str, float]


@dataclass(frozen=True, slots=True)
class _Performance:
    started_at: datetime
    goals_for: int
    goals_against: int
    same_direction: bool

    @property
    def outcome(self) -> str:
        if self.goals_for > self.goals_against:
            return "win"
        if self.goals_for < self.goals_against:
            return "loss"
        return "draw"

    @property
    def total(self) -> int:
        return self.goals_for + self.goals_against


class FeatureBuilder:
    """Build deterministic features from records available strictly before cutoff."""

    def __init__(
        self,
        history: list[MatchRecord],
        *,
        player_windows: tuple[int, ...] = DEFAULT_PLAYER_WINDOWS,
        h2h_windows: tuple[int, ...] = DEFAULT_H2H_WINDOWS,
        session_gap: timedelta = timedelta(minutes=45),
    ) -> None:
        if any(window <= 0 for window in (*player_windows, *h2h_windows)):
            raise ValueError("feature windows must be positive")
        self._history = tuple(history)
        self._player_windows = player_windows
        self._h2h_windows = h2h_windows
        self._session_gap = session_gap

    def build(self, target: TargetContext) -> FeatureSet:
        eligible = sorted(
            (
                match
                for match in self._history
                if match.event_id != target.event_id
                and match.started_at < target.cutoff_at
                and match.available_at < target.cutoff_at
                and match.sport == target.sport
            ),
            key=lambda item: (item.started_at, item.event_id),
            reverse=True,
        )
        values: dict[str, float] = {}
        for side, player_key, team_key in (
            ("p1", target.player1_key, target.team1_key),
            ("p2", target.player2_key, target.team2_key),
        ):
            player_matches = self._player_performances(eligible, player_key)
            scopes = {
                "global": player_matches,
                "source": self._player_performances(
                    [item for item in eligible if item.source == target.source], player_key
                ),
                "game": self._player_performances(
                    [item for item in eligible if item.game == target.game], player_key
                ),
                "tournament": self._player_performances(
                    [
                        item
                        for item in eligible
                        if item.tournament_key == target.tournament_key
                    ],
                    player_key,
                ),
                "format": self._player_performances(
                    [item for item in eligible if item.format == target.format], player_key
                ),
            }
            if team_key is not None:
                scopes["player_team"] = self._player_performances(
                    [
                        item
                        for item in eligible
                        if self._player_team_in_match(item, player_key, team_key)
                    ],
                    player_key,
                )
            for scope, performances in scopes.items():
                self._windowed(values, f"{side}_{scope}", performances, self._player_windows)
            self._session_features(
                values,
                prefix=f"{side}_session",
                performances=player_matches,
                cutoff_at=target.cutoff_at,
            )

        h2h = self._h2h_performances(eligible, target.player1_key, target.player2_key)
        self._windowed(values, "h2h", h2h, self._h2h_windows)
        same_direction = [item for item in h2h if item.same_direction]
        self._windowed(values, "h2h_same_direction", same_direction, self._h2h_windows)

        if target.team1_key is not None and target.team2_key is not None:
            paired = self._h2h_performances(
                [
                    item
                    for item in eligible
                    if self._pair_teams_in_match(
                        item,
                        target.player1_key,
                        target.team1_key,
                        target.player2_key,
                        target.team2_key,
                    )
                ],
                target.player1_key,
                target.player2_key,
            )
            self._windowed(values, "player_team_h2h", paired, self._h2h_windows)

        return FeatureSet(
            event_id=target.event_id,
            cutoff_at=target.cutoff_at,
            eligible_match_ids=tuple(item.event_id for item in eligible),
            values=values,
        )

    @staticmethod
    def _player_performances(
        records: list[MatchRecord], player_key: str
    ) -> list[_Performance]:
        output: list[_Performance] = []
        for item in records:
            if item.player1_key == player_key:
                output.append(
                    _Performance(item.started_at, item.score1, item.score2, True)
                )
            elif item.player2_key == player_key:
                output.append(
                    _Performance(item.started_at, item.score2, item.score1, False)
                )
        return output

    @staticmethod
    def _h2h_performances(
        records: list[MatchRecord], player1_key: str, player2_key: str
    ) -> list[_Performance]:
        output: list[_Performance] = []
        for item in records:
            if item.player1_key == player1_key and item.player2_key == player2_key:
                output.append(
                    _Performance(item.started_at, item.score1, item.score2, True)
                )
            elif item.player1_key == player2_key and item.player2_key == player1_key:
                output.append(
                    _Performance(item.started_at, item.score2, item.score1, False)
                )
        return output

    @staticmethod
    def _player_team_in_match(
        item: MatchRecord, player_key: str, team_key: str
    ) -> bool:
        return (item.player1_key, item.team1_key) == (player_key, team_key) or (
            item.player2_key,
            item.team2_key,
        ) == (player_key, team_key)

    @staticmethod
    def _pair_teams_in_match(
        item: MatchRecord,
        player1_key: str,
        team1_key: str,
        player2_key: str,
        team2_key: str,
    ) -> bool:
        direct = (
            item.player1_key,
            item.team1_key,
            item.player2_key,
            item.team2_key,
        ) == (player1_key, team1_key, player2_key, team2_key)
        reverse = (
            item.player1_key,
            item.team1_key,
            item.player2_key,
            item.team2_key,
        ) == (player2_key, team2_key, player1_key, team1_key)
        return direct or reverse

    @classmethod
    def _windowed(
        cls,
        output: dict[str, float],
        prefix: str,
        performances: list[_Performance],
        windows: tuple[int, ...],
    ) -> None:
        for window in windows:
            cls._aggregate(output, f"{prefix}_last_{window}", performances[:window])
        cls._aggregate(output, f"{prefix}_all", performances)

    @staticmethod
    def _aggregate(
        output: dict[str, float], prefix: str, performances: list[_Performance]
    ) -> None:
        matches = len(performances)
        wins = sum(item.outcome == "win" for item in performances)
        draws = sum(item.outcome == "draw" for item in performances)
        losses = sum(item.outcome == "loss" for item in performances)
        totals = [item.total for item in performances]
        output[f"{prefix}_matches"] = float(matches)
        output[f"{prefix}_wins"] = float(wins)
        output[f"{prefix}_draws"] = float(draws)
        output[f"{prefix}_losses"] = float(losses)
        denominator = matches or 1
        output[f"{prefix}_win_rate"] = wins / denominator
        output[f"{prefix}_draw_rate"] = draws / denominator
        output[f"{prefix}_loss_rate"] = losses / denominator
        output[f"{prefix}_goals_for_avg"] = (
            fmean(item.goals_for for item in performances) if matches else 0.0
        )
        output[f"{prefix}_goals_against_avg"] = (
            fmean(item.goals_against for item in performances) if matches else 0.0
        )
        output[f"{prefix}_total_avg"] = fmean(totals) if totals else 0.0
        output[f"{prefix}_median_total"] = float(median(totals)) if totals else 0.0
        output[f"{prefix}_std_total"] = pstdev(totals) if len(totals) > 1 else 0.0
        for line in TOTAL_LINES:
            label = str(line).replace(".", "_")
            output[f"{prefix}_over_{label}"] = (
                sum(total > line for total in totals) / denominator
            )
            output[f"{prefix}_under_{label}"] = (
                sum(total < line for total in totals) / denominator
            )

    def _session_features(
        self,
        output: dict[str, float],
        *,
        prefix: str,
        performances: list[_Performance],
        cutoff_at: datetime,
    ) -> None:
        for label, duration in (
            ("30m", timedelta(minutes=30)),
            ("1h", timedelta(hours=1)),
            ("3h", timedelta(hours=3)),
        ):
            output[f"{prefix}_matches_last_{label}"] = float(
                sum(cutoff_at - item.started_at <= duration for item in performances)
            )
        if not performances:
            output[f"{prefix}_minutes_since_previous_game"] = -1.0
            session: list[_Performance] = []
        else:
            output[f"{prefix}_minutes_since_previous_game"] = (
                cutoff_at - performances[0].started_at
            ).total_seconds() / 60
            session = [performances[0]]
            for previous, current in pairwise(performances):
                if previous.started_at - current.started_at > self._session_gap:
                    break
                session.append(current)
        output[f"{prefix}_wins"] = float(sum(item.outcome == "win" for item in session))
        output[f"{prefix}_draws"] = float(sum(item.outcome == "draw" for item in session))
        output[f"{prefix}_losses"] = float(sum(item.outcome == "loss" for item in session))
        output[f"{prefix}_goals"] = float(sum(item.goals_for for item in session))
        for outcome in ("win", "draw", "loss"):
            streak = 0
            for item in performances:
                if item.outcome != outcome:
                    break
                streak += 1
            output[f"{prefix}_consecutive_{outcome}s"] = float(streak)
