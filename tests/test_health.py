from __future__ import annotations

import pytest

import app.health as health
from app.config import Settings


class BrokenRedis:
    def __init__(self) -> None:
        self.closed = False

    async def ping(self) -> bool:
        raise ConnectionError("redis unavailable")

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_healthcheck_reports_all_unavailable_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = BrokenRedis()

    def fail_engine(_: str) -> None:
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(health, "build_async_engine", fail_engine)
    monkeypatch.setattr("app.health.Redis.from_url", lambda _: redis)

    result = await health.check_dependencies(Settings())

    assert result == {"database": False, "redis": False}
    assert redis.closed
