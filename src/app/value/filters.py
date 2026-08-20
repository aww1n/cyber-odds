from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SignalFilters:
    min_samples: int = 0
    min_h2h: int = 0
    min_odds: float = 1.01
    max_odds: float = 100.0
    min_probability: float = 0.0
    max_probability: float = 1.0
    min_value_percent: Decimal | float = 0.0
    allowed_markets: frozenset[str] = frozenset()
    allowed_tournaments: frozenset[str] = frozenset()
    min_match_confidence: float = 0.90
    stale_after: timedelta = timedelta(minutes=2)
    suspicious_edge_percent: float = 50.0
