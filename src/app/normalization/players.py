from __future__ import annotations

import re

from app.normalization.common import normalize_text

TRAILING_PLAYER = re.compile(r"\((?P<player>[^()]*)\)\s*$")


def normalize_player_name(value: str) -> str:
    """Normalize a player label and unwrap a trailing ``Team (nickname)`` form."""

    match = TRAILING_PLAYER.search(value)
    player = match.group("player") if match and match.group("player").strip() else value
    return normalize_text(player)
