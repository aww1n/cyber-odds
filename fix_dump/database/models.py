from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

BIGINT_ID = BigInteger().with_variant(Integer, "sqlite")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Source(TimestampMixin, Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('bookmaker', 'history', 'manual')", name="source_type"
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class RawPayload(Base):
    __tablename__ = "raw_payloads"
    __table_args__ = (
        Index("ix_raw_payloads_content_sha256", "content_sha256"),
        Index("ix_raw_payloads_received_at", "received_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(1024), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255))
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    response_headers: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Player(TimestampMixin, Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class Team(TimestampMixin, Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class PlayerAlias(TimestampMixin, Base):
    __tablename__ = "player_aliases"
    __table_args__ = (
        UniqueConstraint("source_id", "normalized_alias", name="uq_player_alias_source_norm"),
        Index("ix_player_aliases_alias", "alias"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    match_method: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class TeamAlias(TimestampMixin, Base):
    __tablename__ = "team_aliases"
    __table_args__ = (
        UniqueConstraint("source_id", "normalized_alias", name="uq_team_alias_source_norm"),
        Index("ix_team_aliases_alias", "alias"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    match_method: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class NormalizationReview(TimestampMixin, Base):
    __tablename__ = "normalization_reviews"
    __table_args__ = (
        CheckConstraint("entity_type IN ('player', 'team', 'tournament')", name="entity_type"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status"),
        Index("ix_normalization_reviews_status", "status"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_value: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(255), nullable=False)
    candidate_id: Mapped[int | None] = mapped_column(BigInteger)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))


class Tournament(TimestampMixin, Base):
    __tablename__ = "tournaments"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_tournament_source_external"),
        Index("ix_tournaments_normalized_name", "normalized_name"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sport: Mapped[str] = mapped_column(String(64), nullable=False)
    game: Mapped[str | None] = mapped_column(String(64))
    format: Mapped[str | None] = mapped_column(String(128))
    family: Mapped[str | None] = mapped_column(String(64), index=True)
    country_code: Mapped[str | None] = mapped_column(String(8))


class Event(TimestampMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_event_source_external"),
        CheckConstraint(
            "status IN ('scheduled', 'live', 'finished', 'cancelled', 'unknown')",
            name="status",
        ),
        Index("ix_events_started_at", "started_at"),
        Index("ix_events_external_id", "external_id"),
        Index("ix_events_source_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    sport: Mapped[str] = mapped_column(String(64), nullable=False)
    game: Mapped[str | None] = mapped_column(String(64))
    tournament_id: Mapped[int | None] = mapped_column(ForeignKey("tournaments.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    player1_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    player2_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    team1_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    team2_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    format: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="unknown")
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))


class EventParticipant(Base):
    __tablename__ = "event_participants"
    __table_args__ = (
        UniqueConstraint("event_id", "side", name="uq_event_participant_side"),
        CheckConstraint("side IN (1, 2)", name="side"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    side: Mapped[int] = mapped_column(Integer, nullable=False)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    raw_name: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_player_name: Mapped[str | None] = mapped_column(String(255))
    raw_team_name: Mapped[str | None] = mapped_column(String(255))
    raw_team_name_alt: Mapped[str | None] = mapped_column(String(255))
    external_player_key: Mapped[str | None] = mapped_column(String(255))
    external_participant_id: Mapped[str | None] = mapped_column(String(128))
    external_team_id: Mapped[str | None] = mapped_column(String(128))


class Result(Base):
    __tablename__ = "results"
    __table_args__ = (
        CheckConstraint("winner IN ('P1', 'X', 'P2')", name="winner"),
        Index("ix_results_event_id", "event_id", unique=True),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    score1: Mapped[int] = mapped_column(Integer, nullable=False)
    score2: Mapped[int] = mapped_column(Integer, nullable=False)
    winner: Mapped[str] = mapped_column(String(2), nullable=False)
    is_draw: Mapped[bool] = mapped_column(Boolean, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    # Some historical APIs expose the final score but not the moment it was settled.
    # NULL is preferable to inventing a timestamp from the collection time.
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Timestamp reported by the historical source for its last result-row update.
    # This is evidence of availability, but is not claimed as bookmaker settlement.
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))


class Market(TimestampMixin, Base):
    __tablename__ = "markets"
    __table_args__ = (
        UniqueConstraint("event_id", "external_id", name="uq_market_event_external"),
        Index("ix_markets_event_id", "event_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    period: Mapped[str | None] = mapped_column(String(64))


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"
    __table_args__ = (
        CheckConstraint("odds > 1", name="odds_positive"),
        Index("ix_odds_snapshots_event_id", "event_id"),
        Index("ix_odds_snapshots_received_at", "received_at"),
        Index("ix_odds_event_market_received", "event_id", "market_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    bookmaker_source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    selection: Mapped[str] = mapped_column(String(64), nullable=False)
    line: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    odds: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload_id: Mapped[int] = mapped_column(ForeignKey("raw_payloads.id"), nullable=False)


class ModelPrediction(Base):
    __tablename__ = "model_predictions"
    __table_args__ = (
        CheckConstraint("probability > 0 AND probability <= 1", name="probability"),
        UniqueConstraint(
            "odds_snapshot_id",
            "model_name",
            "model_version",
            "selection",
            name="uq_prediction_snapshot_model_selection",
        ),
        Index("ix_model_predictions_event_id", "event_id"),
        Index("ix_model_predictions_event_match_id", "event_match_id"),
        Index("ix_model_predictions_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    event_match_id: Mapped[int | None] = mapped_column(ForeignKey("event_matches.id"))
    # Freeze the participant orientation used when the live prediction was made.
    # EventMatch.components is mutable because matching is recalculated later.
    mapping_reversed_sides: Mapped[bool | None] = mapped_column(Boolean)
    odds_snapshot_id: Mapped[int] = mapped_column(ForeignKey("odds_snapshots.id"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    market_code: Mapped[str] = mapped_column(String(64), nullable=False)
    selection: Mapped[str] = mapped_column(String(64), nullable=False)
    probability: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    fair_odds: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False)
    value_ratio: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    value_percent: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False)
    display_odds: Mapped[Decimal | None] = mapped_column(Numeric(12, 5))
    sample_size: Mapped[int | None] = mapped_column(Integer)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    anomaly_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    feature_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        CheckConstraint("decision IN ('alert', 'skip')", name="decision"),
        Index("ix_signals_created_at", "created_at"),
        Index("ux_signals_alert_key", "alert_key", unique=True),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("model_predictions.id"), nullable=False, unique=True
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy: Mapped[str] = mapped_column(String(128), nullable=False)
    alert_key: Mapped[str | None] = mapped_column(String(255))
    minimum_odds: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False)
    safety_multiplier: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    stake_mode: Mapped[str | None] = mapped_column(String(32))
    suggested_stake: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    bet_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    filter_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    telegram_message_id: Mapped[str | None] = mapped_column(String(128))
    # An unsent recommendation must not be delivered after its quoted odds are stale
    # or after the event has started. New live signals set this explicitly.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Settlement(Base):
    __tablename__ = "settlements"
    __table_args__ = (
        CheckConstraint("outcome IN ('win', 'loss', 'return', 'void')", name="outcome"),
        Index("ix_settlements_signal_id", "signal_id", unique=True),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), nullable=False)
    result_id: Mapped[int] = mapped_column(ForeignKey("results.id"), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    stake: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    payout: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    telegram_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class EventMatch(Base):
    __tablename__ = "event_matches"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence"),
        CheckConstraint("status IN ('matched', 'ambiguous', 'rejected')", name="status"),
        UniqueConstraint("source_event_id", "bookmaker_event_id", name="uq_event_match_pair"),
        Index("ix_event_matches_status", "status"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    source_event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    bookmaker_event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    components: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ParserRun(Base):
    __tablename__ = "parser_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'success', 'partial', 'error')", name="status"),
        Index("ix_parser_runs_source_started", "source_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    parser_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    events_received: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    events_parsed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    events_rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))
