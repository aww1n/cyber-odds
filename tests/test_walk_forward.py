from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.backtest.walk_forward import walk_forward_splits


def test_walk_forward_is_strictly_temporal_and_never_random() -> None:
    splits = walk_forward_splits(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 7, 1, tzinfo=UTC),
        train=timedelta(days=60),
        validation=timedelta(days=30),
        test=timedelta(days=30),
        step=timedelta(days=30),
    )

    assert len(splits) >= 2
    for split in splits:
        assert split.train_end == split.validation_start
        assert split.validation_end == split.test_start
        assert split.train_start < split.train_end < split.validation_end < split.test_end
