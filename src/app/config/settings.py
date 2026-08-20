from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-only runtime configuration; secrets are never given defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://cyber_odds:change_me@localhost:5432/cyber_odds"
    redis_url: str = "redis://localhost:6379/0"

    telegram_bot_token: SecretStr | None = None
    telegram_admin_ids: str = ""
    telegram_alert_chat_id: int | None = None
    telegram_publish_interval_seconds: float = Field(default=2.0, gt=0, le=60)

    fonbet_enabled: bool = True
    fonbet_base_url: str | None = None
    fonbet_scope_market: int = Field(default=1600, gt=0)
    fonbet_language: str = Field(default="ru", min_length=2, max_length=8)

    esb_enabled: bool = False
    esb_base_url: str = "https://football.esportsbattle.com"
    esb_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    h2h_enabled: bool = False
    sis_h2h_enabled: bool = False
    sis_h2h_base_url: str = "https://api-h2h.hudstats.com"
    sis_h2h_sport: str = "fifa"
    sis_h2h_source_timezone: str = "Europe/Moscow"
    sis_h2h_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    uel_enabled: bool = False
    uel_base_url: str = "https://api.unitedleagues.gg"
    uel_sport: str = "efootball"
    uel_source_timezone: str = "Europe/Moscow"
    uel_timeout_seconds: float = Field(default=20.0, gt=0, le=120)

    raw_data_dir: Path = Path("data/raw")

    odds_collection_interval_seconds: float = Field(default=5.0, gt=0, le=3600)
    history_collection_interval_seconds: float = Field(default=300.0, gt=0, le=86400)
    settlement_interval_seconds: float = Field(default=10.0, gt=0, le=3600)
    history_max_tournaments: int = Field(default=5, gt=0, le=100)
    esb_max_tournaments: int = Field(default=1, gt=0, le=20)
    esb_backfill_participants: str = ""
    strategies_path: Path = Path("config/strategies.yaml")

    bankroll: Decimal = Field(default=Decimal("100000"), gt=0)
    default_stake_percent: Decimal = Field(default=Decimal("1.5"), gt=0, le=100)
    min_stake_percent: Decimal = Field(default=Decimal("0.25"), gt=0, le=100)
    max_stake_percent: Decimal = Field(default=Decimal("3.0"), gt=0, le=100)

    alert_minutes_before_start: int = Field(default=10, gt=0, le=1440)

    corridor_rebuild_interval_seconds: float = Field(default=300.0, gt=0, le=86400)
    corridor_max_odds_age_minutes: int = Field(default=15, gt=0, le=1440)
    corridor_bucket_width: Decimal = Field(default=Decimal("0.25"), gt=0)
    corridor_min_samples: int = Field(default=20, gt=0)

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        if self.app_env.casefold() == "production" and "change_me" in self.database_url:
            raise ValueError(
                "production DATABASE_URL must not use the default change_me password"
            )
        if self.min_stake_percent > self.default_stake_percent:
            raise ValueError("MIN_STAKE_PERCENT cannot exceed DEFAULT_STAKE_PERCENT")
        if self.default_stake_percent > self.max_stake_percent:
            raise ValueError("DEFAULT_STAKE_PERCENT cannot exceed MAX_STAKE_PERCENT")
        return self

    @property
    def admin_ids(self) -> tuple[int, ...]:
        if not self.telegram_admin_ids.strip():
            return ()
        try:
            return tuple(int(value.strip()) for value in self.telegram_admin_ids.split(","))
        except ValueError as error:
            raise ValueError("TELEGRAM_ADMIN_IDS must contain comma-separated integers") from error

    @property
    def alert_chat_id(self) -> int | None:
        if self.telegram_alert_chat_id is not None:
            return self.telegram_alert_chat_id
        return self.admin_ids[0] if self.admin_ids else None

    @property
    def esb_participants(self) -> tuple[str, ...]:
        return tuple(
            value.strip()
            for value in self.esb_backfill_participants.split(",")
            if value.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
