from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import inspect

from app.database import Base, build_async_engine
from app.database import models as database_models  # noqa: F401

EXPECTED_TABLES = {
    "corridor_observations",
    "event_matches",
    "event_participants",
    "events",
    "markets",
    "model_predictions",
    "normalization_reviews",
    "odds_snapshots",
    "odds_corridors",
    "parser_runs",
    "player_aliases",
    "players",
    "raw_payloads",
    "results",
    "settlements",
    "signals",
    "sources",
    "team_aliases",
    "teams",
    "tournaments",
}


def test_schema_contains_required_tables_and_indexes() -> None:
    assert set(Base.metadata.tables) >= EXPECTED_TABLES

    assert {index.name for index in Base.metadata.tables["events"].indexes} >= {
        "ix_events_started_at",
        "ix_events_external_id",
        "ix_events_source_id",
    }
    assert {index.name for index in Base.metadata.tables["odds_snapshots"].indexes} >= {
        "ix_odds_snapshots_event_id",
        "ix_odds_snapshots_received_at",
        "ix_odds_contract_received",
    }
    assert "ix_model_predictions_event_id" in {
        index.name for index in Base.metadata.tables["model_predictions"].indexes
    }
    assert "ix_model_predictions_event_match_id" in {
        index.name for index in Base.metadata.tables["model_predictions"].indexes
    }
    assert "ix_players_normalized_name" in {
        index.name for index in Base.metadata.tables["players"].indexes
    }
    assert "ix_player_aliases_alias" in {
        index.name for index in Base.metadata.tables["player_aliases"].indexes
    }
    participant_columns = Base.metadata.tables["event_participants"].columns
    assert {
        "external_player_key",
        "external_participant_id",
        "external_team_id",
        "raw_player_name",
        "raw_team_name",
        "raw_team_name_alt",
    } <= set(participant_columns.keys())
    assert Base.metadata.tables["results"].columns["settled_at"].nullable
    assert Base.metadata.tables["results"].columns["observed_at"].nullable
    assert "family" in Base.metadata.tables["tournaments"].columns
    assert "telegram_notified_at" in Base.metadata.tables["settlements"].columns
    assert "alert_key" in Base.metadata.tables["signals"].columns
    assert "expires_at" in Base.metadata.tables["signals"].columns
    assert "mapping_reversed_sides" in Base.metadata.tables["model_predictions"].columns
    assert "returns" in Base.metadata.tables["odds_corridors"].columns
    assert "line" in Base.metadata.tables["corridor_observations"].columns
    assert "ux_signals_alert_key" in {
        index.name for index in Base.metadata.tables["signals"].indexes
    }
    assert {
        "ux_event_matches_matched_source",
        "ux_event_matches_matched_bookmaker",
    } <= {index.name for index in Base.metadata.tables["event_matches"].indexes}
    assert "ux_corridor_observation_contract" in {
        index.name for index in Base.metadata.tables["corridor_observations"].indexes
    }


def test_schema_can_be_created_with_async_engine(tmp_path: Path) -> None:
    async def create_and_inspect() -> set[str]:
        engine = build_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'schema.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        await engine.dispose()
        return tables

    assert asyncio.run(create_and_inspect()) >= EXPECTED_TABLES
