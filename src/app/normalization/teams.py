from __future__ import annotations

from app.normalization.common import normalize_text


def normalize_team_name(value: str) -> str:
    return normalize_text(value)
