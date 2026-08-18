from __future__ import annotations

import pytest

from app.config import Settings


def test_settings_parse_admin_ids() -> None:
    settings = Settings(_env_file=None, telegram_admin_ids="123, 456")

    assert settings.admin_ids == (123, 456)
    assert settings.alert_chat_id == 123


def test_explicit_alert_chat_can_differ_from_admin() -> None:
    settings = Settings(
        _env_file=None,
        telegram_admin_ids="123",
        telegram_alert_chat_id=-100123456,
    )

    assert settings.alert_chat_id == -100123456


def test_esb_participants_ignore_blank_values() -> None:
    settings = Settings(_env_file=None, esb_backfill_participants="Artrom, , Pritistreet")

    assert settings.esb_participants == ("Artrom", "Pritistreet")


def test_settings_reject_malformed_admin_ids() -> None:
    settings = Settings(_env_file=None, telegram_admin_ids="123, broken")

    with pytest.raises(ValueError, match="comma-separated integers"):
        _ = settings.admin_ids


def test_fonbet_endpoint_has_no_unverified_code_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.fonbet_base_url is None


def test_esb_endpoint_defaults_to_verified_official_site() -> None:
    settings = Settings(_env_file=None)

    assert settings.esb_base_url == "https://football.esportsbattle.com"


def test_production_rejects_default_database_password() -> None:
    with pytest.raises(ValueError, match="change_me"):
        Settings(_env_file=None, app_env="production")


def test_development_allows_default_database_password() -> None:
    settings = Settings(_env_file=None, app_env="development")
    assert "change_me" in settings.database_url
