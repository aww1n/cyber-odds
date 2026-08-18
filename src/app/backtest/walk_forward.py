from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class WalkForwardSplit:
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime


def walk_forward_splits(
    *,
    start: datetime,
    end: datetime,
    train: timedelta,
    validation: timedelta,
    test: timedelta,
    step: timedelta,
) -> tuple[WalkForwardSplit, ...]:
    if any(duration <= timedelta(0) for duration in (train, validation, test, step)):
        raise ValueError("walk-forward durations must be positive")
    splits: list[WalkForwardSplit] = []
    train_start = start
    while True:
        train_end = train_start + train
        validation_start = train_end
        validation_end = validation_start + validation
        test_start = validation_end
        test_end = test_start + test
        if test_end > end:
            break
        splits.append(
            WalkForwardSplit(
                train_start,
                train_end,
                validation_start,
                validation_end,
                test_start,
                test_end,
            )
        )
        train_start += step
    return tuple(splits)
