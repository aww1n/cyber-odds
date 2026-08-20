from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.backtest.baseline_evaluation import (
    BaselineEvaluationReport,
    DatabaseBaselineEvaluator,
)
from app.backtest.walk_forward import walk_forward_splits
from app.config import Settings
from app.database import build_async_engine, build_session_factory
from app.database.models import Event, Source
from app.normalization.event_repository import EventMatchingRepository
from app.normalization.repository import SourceParticipantNormalizer
from app.providers import (
    ESportsBattleProvider,
    FonbetProvider,
    SISH2HProvider,
    UELProvider,
)
from app.providers.h2h import SISH2HSport
from app.providers.uel import UELSport
from app.research.reverse_engineering import analyze_file
from app.storage import FilesystemRawArchive
from app.workers.h2h_history_worker import SISH2HBackfillResult, SISH2HHistoryCollector
from app.workers.history_worker import ESBBackfillResult, ESBHistoryCollector
from app.workers.odds_worker import CollectionResult, FonbetOddsCollector
from app.workers.uel_history_worker import UELBackfillResult, UELHistoryCollector

if TYPE_CHECKING:
    from app.models.ml_v1 import MLEvaluationReport


LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app")
    subparsers = parser.add_subparsers(dest="command", required=True)

    research = subparsers.add_parser(
        "research-alerts", help="Analyze known bot alert observations"
    )
    research.add_argument(
        "--input",
        type=Path,
        default=Path("research/known_alerts.json"),
        help="Path to the observed alerts JSON file",
    )
    research.add_argument(
        "--output-dir",
        type=Path,
        default=Path("research/output"),
        help="Directory for generated CSV and JSON tables",
    )

    collect = subparsers.add_parser("collect", help="Collect one current Fonbet snapshot")
    collect.add_argument(
        "--raw-only",
        action="store_true",
        help="Fetch, archive and parse without writing domain rows to the database",
    )

    backfill = subparsers.add_parser(
        "backfill", help="Backfill verified historical sources"
    )
    backfill.add_argument("--source", choices=("esb", "uel", "sis-h2h"), default="esb")
    backfill.add_argument(
        "--participant",
        help="Exact ESportsBattle nickname used by the official participant route",
    )
    backfill.add_argument("--page", type=int, default=1)
    backfill.add_argument("--items-per-page", type=int, default=10)
    backfill.add_argument("--max-tournaments", type=int, default=1)
    backfill.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Calendar day (YYYY-MM-DD), required for the SIS H2H daily schedule",
    )
    backfill.add_argument(
        "--include-unfinished",
        action="store_true",
        help="Include tournament statuses that are not yet verified as finished",
    )
    backfill.add_argument(
        "--raw-only",
        action="store_true",
        help="Fetch, archive and parse without writing domain rows to the database",
    )

    normalize = subparsers.add_parser(
        "normalize", help="Normalize source participants with review-safe matching"
    )
    normalize.add_argument("--source", required=True)

    match_events = subparsers.add_parser(
        "match-events", help="Match normalized historical and bookmaker events"
    )
    match_events.add_argument("--historical-source", required=True)
    match_events.add_argument("--bookmaker-source", default="fonbet")
    match_events.add_argument(
        "--live",
        action="store_true",
        help="Match only upcoming scheduled bookmaker events against source schedule/history",
    )
    match_events.add_argument(
        "--completed",
        action="store_true",
        help="Reconcile completed source results with saved pre-match bookmaker odds",
    )

    backtest = subparsers.add_parser(
        "backtest",
        help="Run leakage-safe walk-forward probability baselines",
    )
    backtest.add_argument("--source", default="uel_ef")
    backtest.add_argument("--min-samples", type=int, default=20)
    backtest.add_argument("--train-days", type=int, default=8)
    backtest.add_argument("--validation-days", type=int, default=4)
    backtest.add_argument("--test-days", type=int, default=4)
    backtest.add_argument("--step-days", type=int, default=4)
    backtest.add_argument(
        "--output",
        type=Path,
        default=Path("research/output/baseline_backtest.json"),
    )

    train = subparsers.add_parser(
        "train",
        help="Run leakage-safe Logistic Regression with validation-only calibration",
    )
    train.add_argument("--source", default="uel_ef")
    train.add_argument("--min-samples", type=int, default=20)
    train.add_argument("--train-days", type=int, default=8)
    train.add_argument("--validation-days", type=int, default=4)
    train.add_argument("--test-days", type=int, default=4)
    train.add_argument("--step-days", type=int, default=4)
    train.add_argument(
        "--output",
        type=Path,
        default=Path("research/output/ml_v1_backtest.json"),
    )

    subparsers.add_parser("telegram", help="Run the admin bot and signal publisher")

    run = subparsers.add_parser("run", help="Run isolated recurring collectors")
    run.add_argument("--role", choices=("odds", "history", "all"), default="all")

    subparsers.add_parser("settle", help="Settle all eligible persisted alert signals once")
    subparsers.add_parser(
        "predict",
        help="Persist baseline predictions and alert/skip decisions for matched events",
    )
    subparsers.add_parser("health", help="Check PostgreSQL and Redis dependencies")
    subparsers.add_parser("db-audit", help="Run read-only database integrity checks")
    build_corridors = subparsers.add_parser(
        "build-corridors",
        help="Materialize observations and rebuild odds corridors once",
    )
    build_corridors.add_argument("--bookmaker", default="fonbet")
    subparsers.add_parser(
        "corridor-progress",
        help="Show materialized corridor coverage",
    )
    corridor_stats = subparsers.add_parser(
        "corridor-stats",
        help="Show best active corridor buckets",
    )
    corridor_stats.add_argument("--bookmaker", default="fonbet")
    corridor_stats.add_argument("--limit", type=int, default=20)
    return parser


async def collect_once(settings: Settings, *, raw_only: bool) -> CollectionResult:
    if not settings.fonbet_enabled:
        raise RuntimeError("Fonbet collection is disabled by FONBET_ENABLED")
    if settings.fonbet_base_url is None:
        raise RuntimeError("FONBET_BASE_URL is required; no endpoint is hardcoded")

    provider = FonbetProvider(
        base_url=settings.fonbet_base_url,
        scope_market=settings.fonbet_scope_market,
        language=settings.fonbet_language,
    )
    collector = FonbetOddsCollector(
        provider=provider,
        archive=FilesystemRawArchive(settings.raw_data_dir),
    )
    try:
        if raw_only:
            return await collector.collect_once()

        engine = build_async_engine(settings.database_url)
        try:
            sessions = build_session_factory(engine)
            async with sessions.begin() as session:
                return await collector.collect_once(session)
        finally:
            await engine.dispose()
    finally:
        await provider.close()


def _print_collection(result: CollectionResult) -> None:
    persisted = result.persisted
    print(
        json.dumps(
            {
                "raw_path": str(result.archive.path),
                "raw_metadata_path": str(result.archive.metadata_path),
                "raw_sha256": result.archive.content_sha256,
                "packet_version": result.catalog.packet_version,
                "events_received": result.catalog.received_event_count,
                "target_tournaments": len(result.catalog.tournaments),
                "events_parsed": len(result.catalog.events),
                "events_rejected": result.catalog.rejected_event_count,
                "quotes_parsed": len(result.catalog.quotes),
                "latency_ms": result.latency_ms,
                "database": (
                    {
                        "source_id": persisted.source_id,
                        "raw_payload_id": persisted.raw_payload_id,
                        "events_upserted": persisted.events_upserted,
                        "odds_snapshots_inserted": persisted.odds_snapshots_inserted,
                    }
                    if persisted is not None
                    else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def backfill_esb(
    settings: Settings,
    *,
    participant: str,
    page: int,
    max_tournaments: int,
    finished_only: bool,
    raw_only: bool,
) -> ESBBackfillResult:
    if not settings.esb_enabled:
        raise RuntimeError("ESportsBattle collection is disabled by ESB_ENABLED")

    provider = ESportsBattleProvider(
        base_url=settings.esb_base_url,
        timeout_seconds=settings.esb_timeout_seconds,
    )
    collector = ESBHistoryCollector(
        provider=provider,
        archive=FilesystemRawArchive(settings.raw_data_dir),
    )
    try:
        if raw_only:
            return await collector.collect_participant_page(
                nickname=participant,
                page=page,
                max_tournaments=max_tournaments,
                finished_only=finished_only,
            )

        engine = build_async_engine(settings.database_url)
        try:
            sessions = build_session_factory(engine)
            async with sessions.begin() as session:
                return await collector.collect_participant_page(
                    nickname=participant,
                    page=page,
                    max_tournaments=max_tournaments,
                    finished_only=finished_only,
                    session=session,
                )
        finally:
            await engine.dispose()
    finally:
        await provider.close()


def _print_esb_backfill(result: ESBBackfillResult) -> None:
    persisted = result.persisted
    print(
        json.dumps(
            {
                "source": "esb",
                "participant": result.participant,
                "page": result.page,
                "total_pages": result.tournament_page.total_pages,
                "tournaments_received": result.tournament_page.received_count,
                "tournaments_collected": len(result.histories),
                "events_parsed": result.events_parsed,
                "results_parsed": result.results_parsed,
                "raw_paths": [str(item.path) for item in result.archives],
                "latency_ms": result.latency_ms,
                "database": (
                    {
                        "source_id": persisted.source_id,
                        "raw_payloads_inserted": persisted.raw_payloads_inserted,
                        "events_upserted": persisted.events_upserted,
                        "results_upserted": persisted.results_upserted,
                    }
                    if persisted is not None
                    else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def backfill_uel(
    settings: Settings,
    *,
    page: int,
    items_per_page: int,
    max_tournaments: int,
    finished_only: bool,
    raw_only: bool,
) -> UELBackfillResult:
    if not settings.uel_enabled:
        raise RuntimeError("UEL collection is disabled by UEL_ENABLED")
    if settings.uel_sport not in {"efootball", "ehockey"}:
        raise RuntimeError("UEL_SPORT must be efootball or ehockey")
    provider = UELProvider(
        base_url=settings.uel_base_url,
        sport=cast(UELSport, settings.uel_sport),
        timeout_seconds=settings.uel_timeout_seconds,
    )
    collector = UELHistoryCollector(
        provider=provider,
        archive=FilesystemRawArchive(settings.raw_data_dir),
        source_timezone=settings.uel_source_timezone,
    )
    try:
        if raw_only:
            return await collector.collect_page(
                page=page,
                items_per_page=items_per_page,
                max_tournaments=max_tournaments,
                finished_only=finished_only,
            )
        engine = build_async_engine(settings.database_url)
        try:
            sessions = build_session_factory(engine)
            async with sessions.begin() as session:
                return await collector.collect_page(
                    page=page,
                    items_per_page=items_per_page,
                    max_tournaments=max_tournaments,
                    finished_only=finished_only,
                    session=session,
                )
        finally:
            await engine.dispose()
    finally:
        await provider.close()


def _print_uel_backfill(result: UELBackfillResult) -> None:
    persisted = result.persisted
    print(
        json.dumps(
            {
                "source": "uel",
                "page": result.page,
                "total_pages": result.tours_page.total_pages,
                "total_items": result.tours_page.total_items,
                "tournaments_received": result.tours_page.received_count,
                "tournaments_collected": len(result.histories),
                "events_parsed": result.events_parsed,
                "results_parsed": result.results_parsed,
                "raw_paths": [str(item.path) for item in result.archives],
                "latency_ms": result.latency_ms,
                "database": (
                    {
                        "source_id": persisted.source_id,
                        "raw_payloads_inserted": persisted.raw_payloads_inserted,
                        "events_upserted": persisted.events_upserted,
                        "results_upserted": persisted.results_upserted,
                    }
                    if persisted is not None
                    else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def backfill_sis_h2h(
    settings: Settings,
    *,
    day: date,
    raw_only: bool,
) -> SISH2HBackfillResult:
    if not settings.sis_h2h_enabled:
        raise RuntimeError("SIS H2H collection is disabled by SIS_H2H_ENABLED")
    if settings.sis_h2h_sport not in {"fifa", "nba", "nfl"}:
        raise RuntimeError("SIS_H2H_SPORT must be fifa, nba or nfl")
    provider = SISH2HProvider(
        base_url=settings.sis_h2h_base_url,
        sport=cast(SISH2HSport, settings.sis_h2h_sport),
        source_timezone=settings.sis_h2h_source_timezone,
        timeout_seconds=settings.sis_h2h_timeout_seconds,
    )
    collector = SISH2HHistoryCollector(
        provider=provider,
        archive=FilesystemRawArchive(settings.raw_data_dir),
    )
    try:
        if raw_only:
            return await collector.collect_day(day)
        engine = build_async_engine(settings.database_url)
        try:
            sessions = build_session_factory(engine)
            async with sessions.begin() as session:
                return await collector.collect_day(day, session=session)
        finally:
            await engine.dispose()
    finally:
        await provider.close()


def _print_sis_h2h_backfill(result: SISH2HBackfillResult) -> None:
    persisted = result.persisted
    print(
        json.dumps(
            {
                "source": "sis_h2h",
                "day": result.history.day.isoformat(),
                "api_sport": result.history.api_sport,
                "events_received": result.history.received_match_count,
                "events_parsed": result.events_parsed,
                "events_rejected": len(result.history.rejection_reasons),
                "results_parsed": result.results_parsed,
                "raw_path": str(result.archive.path),
                "latency_ms": result.latency_ms,
                "database": (
                    {
                        "source_id": persisted.source_id,
                        "raw_payload_id": persisted.raw_payload_id,
                        "tournaments_upserted": persisted.tournaments_upserted,
                        "events_upserted": persisted.events_upserted,
                        "results_upserted": persisted.results_upserted,
                    }
                    if persisted is not None
                    else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def normalize_source(settings: Settings, *, source_code: str) -> dict[str, object]:
    engine = build_async_engine(settings.database_url)
    try:
        sessions = build_session_factory(engine)
        async with sessions.begin() as session:
            stats = await SourceParticipantNormalizer().normalize_source(
                session,
                source_code=source_code,
            )
        return asdict(stats)
    finally:
        await engine.dispose()


async def match_events(
    settings: Settings,
    *,
    historical_source_code: str,
    bookmaker_source_code: str,
    live_only: bool = False,
    completed_only: bool = False,
) -> dict[str, object]:
    engine = build_async_engine(settings.database_url)
    try:
        sessions = build_session_factory(engine)
        async with sessions.begin() as session:
            stats = await EventMatchingRepository().match_sources(
                session,
                historical_source_code=historical_source_code,
                bookmaker_source_code=bookmaker_source_code,
                live_only=live_only,
                completed_only=completed_only,
            )
        return asdict(stats)
    finally:
        await engine.dispose()


async def db_audit(settings: Settings) -> dict[str, object]:
    from app.database.audit import audit_database

    engine = build_async_engine(settings.database_url)
    try:
        sessions = build_session_factory(engine)
        async with sessions() as session:
            report = await audit_database(session)
        return {
            "healthy": report.healthy,
            "counts": report.counts,
            "problems": report.problems,
        }
    finally:
        await engine.dispose()


async def build_corridors_once(
    settings: Settings,
    *,
    bookmaker_code: str = "fonbet",
) -> dict[str, object]:
    from app.corridors.repository import CorridorRepository

    engine = build_async_engine(settings.database_url)
    try:
        sessions = build_session_factory(engine)
        async with sessions.begin() as session:
            report = await CorridorRepository().rebuild(
                session,
                bookmaker_code=bookmaker_code,
                bucket_width=settings.corridor_bucket_width,
                min_samples=settings.corridor_min_samples,
                max_odds_age=timedelta(
                    minutes=settings.corridor_max_odds_age_minutes
                ),
            )
        return asdict(report)
    finally:
        await engine.dispose()


async def corridor_progress(settings: Settings) -> dict[str, int]:
    from app.database.models import CorridorObservation, OddsCorridor

    engine = build_async_engine(settings.database_url)
    try:
        sessions = build_session_factory(engine)
        async with sessions() as session:
            return {
                "observations": int(
                    await session.scalar(
                        select(func.count(CorridorObservation.id)).where(
                            CorridorObservation.is_active.is_(True)
                        )
                    )
                    or 0
                ),
                "active_buckets": int(
                    await session.scalar(
                        select(func.count(OddsCorridor.id)).where(
                            OddsCorridor.is_active.is_(True)
                        )
                    )
                    or 0
                ),
            }
    finally:
        await engine.dispose()


async def corridor_stats(
    settings: Settings,
    *,
    bookmaker_code: str,
    limit: int,
) -> list[dict[str, object]]:
    from app.corridors.repository import CorridorRepository

    engine = build_async_engine(settings.database_url)
    try:
        sessions = build_session_factory(engine)
        async with sessions() as session:
            rows = await CorridorRepository().top_corridors(
                session,
                bookmaker_code=bookmaker_code,
                min_samples=settings.corridor_min_samples,
                limit=limit,
            )
        return [
            {
                "scope": item.scope_type,
                "scope_value": item.scope_value,
                "market": item.market_code,
                "selection": item.selection,
                "line": str(item.line) if item.line is not None else None,
                "odds_min": str(item.odds_min),
                "odds_max": str(item.odds_max),
                "sample_size": item.sample_size,
                "wins": item.wins,
                "losses": item.losses,
                "returns": item.returns,
                "roi_percent": str(item.roi_percent),
                "yield_percent": str(item.roi_percent),
                "confidence": str(item.confidence),
            }
            for item in rows
        ]
    finally:
        await engine.dispose()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def run_baseline_backtest(
    settings: Settings,
    *,
    source_code: str,
    min_samples: int,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
) -> BaselineEvaluationReport:
    durations = (train_days, validation_days, test_days, step_days)
    if any(value <= 0 for value in durations):
        raise ValueError("walk-forward day counts must be positive")
    engine = build_async_engine(settings.database_url)
    try:
        sessions = build_session_factory(engine)
        async with sessions() as session:
            source = await session.scalar(select(Source).where(Source.code == source_code))
            if source is None:
                raise ValueError(f"Unknown source: {source_code}")
            start, end = (
                await session.execute(
                    select(func.min(Event.started_at), func.max(Event.started_at)).where(
                        Event.source_id == source.id
                    )
                )
            ).one()
            if start is None or end is None:
                raise ValueError(f"Source has no events: {source_code}")
            splits = walk_forward_splits(
                start=_aware_utc(start),
                end=_aware_utc(end) + timedelta(seconds=1),
                train=timedelta(days=train_days),
                validation=timedelta(days=validation_days),
                test=timedelta(days=test_days),
                step=timedelta(days=step_days),
            )
            if not splits:
                raise ValueError("Date range is too short for the requested walk-forward split")
            return await DatabaseBaselineEvaluator().run(
                session,
                source_code=source_code,
                min_player_samples=min_samples,
                splits=splits,
            )
    finally:
        await engine.dispose()


def _write_baseline_report(
    report: BaselineEvaluationReport,
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            asdict(report),
            ensure_ascii=False,
            indent=2,
            default=lambda value: value.isoformat()
            if isinstance(value, datetime)
            else str(value),
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "source": report.source,
                "candidates_seen": report.candidates_seen,
                "eligible_events": report.eligible_events,
                "folds": len({item.fold for item in report.folds}),
                "out_of_sample": {
                    name: asdict(metrics)
                    for name, metrics in report.out_of_sample.items()
                },
                "market_backtest": {
                    name: asdict(metrics)
                    for name, metrics in report.market_backtest.items()
                },
                "note": report.note,
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def run_ml_backtest(
    settings: Settings,
    *,
    source_code: str,
    min_samples: int,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
) -> MLEvaluationReport:
    try:
        from app.models.ml_v1 import DatabaseMLDatasetBuilder, LogisticMLV1Evaluator
    except ModuleNotFoundError as error:
        if error.name in {"numpy", "sklearn"}:
            raise RuntimeError(
                "ML dependencies are missing; install the project with the 'ml' extra"
            ) from error
        raise

    durations = (train_days, validation_days, test_days, step_days)
    if any(value <= 0 for value in durations):
        raise ValueError("walk-forward day counts must be positive")
    engine = build_async_engine(settings.database_url)
    try:
        sessions = build_session_factory(engine)
        async with sessions() as session:
            source = await session.scalar(select(Source).where(Source.code == source_code))
            if source is None:
                raise ValueError(f"Unknown source: {source_code}")
            start, end = (
                await session.execute(
                    select(func.min(Event.started_at), func.max(Event.started_at)).where(
                        Event.source_id == source.id
                    )
                )
            ).one()
            if start is None or end is None:
                raise ValueError(f"Source has no events: {source_code}")
            splits = walk_forward_splits(
                start=_aware_utc(start),
                end=_aware_utc(end) + timedelta(seconds=1),
                train=timedelta(days=train_days),
                validation=timedelta(days=validation_days),
                test=timedelta(days=test_days),
                step=timedelta(days=step_days),
            )
            if not splits:
                raise ValueError("Date range is too short for the requested walk-forward split")
            dataset = await DatabaseMLDatasetBuilder().build(
                session,
                source_code=source_code,
                min_player_samples=min_samples,
            )
            return LogisticMLV1Evaluator().run(dataset, splits)
    finally:
        await engine.dispose()


def _write_ml_report(report: MLEvaluationReport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            asdict(report),
            ensure_ascii=False,
            indent=2,
            default=lambda value: value.isoformat()
            if isinstance(value, datetime)
            else str(value),
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "model": report.model,
                "source": report.source,
                "features": report.feature_count,
                "candidates_seen": report.candidates_seen,
                "eligible_events": report.eligible_events,
                "folds": len(report.folds),
                "selected_calibration": [
                    item.selected_calibration for item in report.folds
                ],
                "out_of_sample": asdict(report.out_of_sample),
                "note": report.note,
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def run_telegram(settings: Settings) -> None:
    try:
        from app.telegram.bot import run_telegram_bot
    except ModuleNotFoundError as error:
        if error.name == "aiogram":
            raise RuntimeError(
                "Telegram dependencies are missing; install the project with the "
                "'telegram' extra"
            ) from error
        raise
    asyncio.run(run_telegram_bot(settings))


async def settle_once(settings: Settings) -> dict[str, int]:
    from app.workers.settlement_worker import SettlementWorker

    engine = build_async_engine(settings.database_url)
    try:
        sessions = build_session_factory(engine)
        async with sessions.begin() as session:
            result = await SettlementWorker().settle_once(session)
        return asdict(result)
    finally:
        await engine.dispose()


async def predict_once(settings: Settings) -> dict[str, dict[str, int]]:
    from app.config import load_strategies
    from app.workers.prediction_worker import PredictionSignalWorker

    configured = load_strategies(settings.strategies_path)
    engine = build_async_engine(settings.database_url)
    output: dict[str, dict[str, int]] = {}
    try:
        sessions = build_session_factory(engine)
        async with sessions.begin() as session:
            worker = PredictionSignalWorker()
            for name, strategy in configured.strategies.items():
                if not strategy.prediction_enabled:
                    continue
                batch = await worker.generate_once(
                    session,
                    strategy_name=name,
                    strategy=strategy,
                )
                output[name] = asdict(batch)
        return output
    finally:
        await engine.dispose()


async def run_recurring_services(settings: Settings, *, role: str) -> None:
    from app.workers.scheduler import IsolatedScheduler, RecurringJob

    jobs: list[RecurringJob] = []

    uel_backfill_page = 2

    async def odds_job() -> None:
        result = await collect_once(settings, raw_only=False)
        normalized = await normalize_source(settings, source_code="fonbet")
        LOGGER.info(
            "Fonbet cycle: parsed=%s rejected=%s normalized=%s",
            len(result.catalog.events),
            result.catalog.rejected_event_count,
            normalized,
        )

    async def uel_job() -> None:
        nonlocal uel_backfill_page
        items_per_page = max(10, settings.history_max_tournaments)

        # Always refresh page 1 because it contains the freshest/current tours.
        fresh = await backfill_uel(
            settings,
            page=1,
            items_per_page=items_per_page,
            max_tournaments=settings.history_max_tournaments,
            finished_only=False,
            raw_only=False,
        )

        # Walk older pages in the background instead of reading page 1 forever.
        # This eventually covers the full UEL catalogue while keeping current
        # tournaments fresh on every history cycle.
        if fresh.tours_page.total_pages > 1:
            if uel_backfill_page > fresh.tours_page.total_pages:
                uel_backfill_page = 2
            if uel_backfill_page <= fresh.tours_page.total_pages:
                backfill_page = uel_backfill_page
                older = await backfill_uel(
                    settings,
                    page=backfill_page,
                    items_per_page=items_per_page,
                    max_tournaments=settings.history_max_tournaments,
                    finished_only=False,
                    raw_only=False,
                )
                LOGGER.info(
                    "UEL background page %s/%s: events=%s results=%s",
                    backfill_page,
                    fresh.tours_page.total_pages,
                    older.events_parsed,
                    older.results_parsed,
                )
                uel_backfill_page += 1

        source_code = "uel_ef" if settings.uel_sport == "efootball" else "uel_eh"
        normalized = await normalize_source(settings, source_code=source_code)
        LOGGER.info(
            "UEL current page: tours=%s/%s events=%s results=%s normalized=%s",
            len(fresh.histories),
            fresh.tours_page.total_items,
            fresh.events_parsed,
            fresh.results_parsed,
            normalized,
        )

    async def sis_h2h_job() -> None:
        source_day = datetime.now(ZoneInfo(settings.sis_h2h_source_timezone)).date()
        await backfill_sis_h2h(settings, day=source_day, raw_only=False)
        game = {"fifa": "esoccer", "nba": "ebasketball", "nfl": "eamericanfootball"}[
            settings.sis_h2h_sport
        ]
        await normalize_source(settings, source_code=f"sis_h2h_{game}")

    async def esb_job() -> None:
        for participant in settings.esb_participants:
            await backfill_esb(
                settings,
                participant=participant,
                page=1,
                max_tournaments=settings.esb_max_tournaments,
                finished_only=True,
                raw_only=False,
            )
        await normalize_source(settings, source_code="esb_ef")

    async def settlement_job() -> None:
        result = await settle_once(settings)
        if result.get("candidates") or result.get("settled"):
            LOGGER.info("Settlement cycle: %s", result)

    async def prediction_job() -> None:
        matching = await match_events(
            settings,
            historical_source_code="uel_ef",
            bookmaker_source_code="fonbet",
            live_only=True,
        )
        prediction = await predict_once(settings)
        LOGGER.info("Matching cycle: %s", matching)
        LOGGER.info("Prediction cycle: %s", prediction)

    async def corridor_job() -> None:
        reconciliation = await match_events(
            settings,
            historical_source_code="uel_ef",
            bookmaker_source_code="fonbet",
            completed_only=True,
        )
        corridors = await build_corridors_once(settings)
        LOGGER.info("Historical reconciliation cycle: %s", reconciliation)
        LOGGER.info("Corridor rebuild cycle: %s", corridors)

    if role in {"odds", "all"} and settings.fonbet_enabled:
        if settings.fonbet_base_url is None:
            raise RuntimeError("FONBET_BASE_URL is required for the odds worker")
        jobs.append(
            RecurringJob(
                "fonbet-odds",
                settings.odds_collection_interval_seconds,
                odds_job,
            )
        )
    if role in {"history", "all"}:
        if settings.uel_enabled:
            jobs.append(
                RecurringJob(
                    "uel-history",
                    settings.history_collection_interval_seconds,
                    uel_job,
                )
            )
            jobs.append(
                RecurringJob(
                    "uel-predictions",
                    settings.odds_collection_interval_seconds,
                    prediction_job,
                )
            )
            jobs.append(
                RecurringJob(
                    "odds-corridors",
                    settings.corridor_rebuild_interval_seconds,
                    corridor_job,
                )
            )
        if settings.sis_h2h_enabled:
            jobs.append(
                RecurringJob(
                    "sis-h2h-history",
                    settings.history_collection_interval_seconds,
                    sis_h2h_job,
                )
            )
        if settings.esb_enabled and settings.esb_participants:
            jobs.append(
                RecurringJob(
                    "esb-history",
                    settings.history_collection_interval_seconds,
                    esb_job,
                )
            )
        jobs.append(
            RecurringJob(
                "settlement",
                settings.settlement_interval_seconds,
                settlement_job,
            )
        )
    await IsolatedScheduler().run(tuple(jobs))


async def run_healthcheck(settings: Settings) -> dict[str, bool]:
    from app.health import check_dependencies

    return await check_dependencies(settings)


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_parser().parse_args()
    if args.command == "research-alerts":
        outputs = analyze_file(args.input, args.output_dir)
        print(f"Analyzed {outputs.row_count} observations")
        print(f"CSV: {outputs.csv_path}")
        print(f"JSON: {outputs.json_path}")
    elif args.command == "collect":
        _print_collection(asyncio.run(collect_once(settings, raw_only=args.raw_only)))
    elif args.command == "backfill":
        if args.source == "esb":
            if not args.participant:
                raise SystemExit("--participant is required for --source esb")
            _print_esb_backfill(
                asyncio.run(
                    backfill_esb(
                        settings,
                        participant=args.participant,
                        page=args.page,
                        max_tournaments=args.max_tournaments,
                        finished_only=not args.include_unfinished,
                        raw_only=args.raw_only,
                    )
                )
            )
        elif args.source == "uel":
            _print_uel_backfill(
                asyncio.run(
                    backfill_uel(
                        settings,
                        page=args.page,
                        items_per_page=args.items_per_page,
                        max_tournaments=args.max_tournaments,
                        finished_only=not args.include_unfinished,
                        raw_only=args.raw_only,
                    )
                )
            )
        else:
            if args.date is None:
                raise SystemExit("--date is required for --source sis-h2h")
            _print_sis_h2h_backfill(
                asyncio.run(
                    backfill_sis_h2h(
                        settings,
                        day=args.date,
                        raw_only=args.raw_only,
                    )
                )
            )
    elif args.command == "normalize":
        print(
            json.dumps(
                asyncio.run(normalize_source(settings, source_code=args.source)),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "match-events":
        if args.live and args.completed:
            raise SystemExit("--live and --completed are mutually exclusive")
        print(
            json.dumps(
                asyncio.run(
                    match_events(
                        settings,
                        historical_source_code=args.historical_source,
                        bookmaker_source_code=args.bookmaker_source,
                        live_only=args.live,
                        completed_only=args.completed,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "backtest":
        report = asyncio.run(
            run_baseline_backtest(
                settings,
                source_code=args.source,
                min_samples=args.min_samples,
                train_days=args.train_days,
                validation_days=args.validation_days,
                test_days=args.test_days,
                step_days=args.step_days,
            )
        )
        _write_baseline_report(report, args.output)
    elif args.command == "train":
        ml_report = asyncio.run(
            run_ml_backtest(
                settings,
                source_code=args.source,
                min_samples=args.min_samples,
                train_days=args.train_days,
                validation_days=args.validation_days,
                test_days=args.test_days,
                step_days=args.step_days,
            )
        )
        _write_ml_report(ml_report, args.output)
    elif args.command == "telegram":
        run_telegram(settings)
    elif args.command == "run":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(run_recurring_services(settings, role=args.role))
    elif args.command == "settle":
        print(json.dumps(asyncio.run(settle_once(settings)), indent=2))
    elif args.command == "predict":
        print(json.dumps(asyncio.run(predict_once(settings)), indent=2))
    elif args.command == "health":
        health = asyncio.run(run_healthcheck(settings))
        print(json.dumps(health, indent=2))
        if not all(health.values()):
            raise SystemExit(1)
    elif args.command == "db-audit":
        audit_report = asyncio.run(db_audit(settings))
        print(json.dumps(audit_report, ensure_ascii=False, indent=2))
        if not audit_report["healthy"]:
            raise SystemExit(1)
    elif args.command == "build-corridors":
        print(
            json.dumps(
                asyncio.run(
                    build_corridors_once(settings, bookmaker_code=args.bookmaker)
                ),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    elif args.command == "corridor-progress":
        print(
            json.dumps(
                asyncio.run(corridor_progress(settings)),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "corridor-stats":
        print(
            json.dumps(
                asyncio.run(
                    corridor_stats(
                        settings,
                        bookmaker_code=args.bookmaker,
                        limit=args.limit,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
