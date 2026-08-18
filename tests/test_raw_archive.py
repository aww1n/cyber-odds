from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.providers.base import ProviderPayload
from app.storage import FilesystemRawArchive


def test_archive_preserves_exact_response_and_is_append_only(tmp_path: Path) -> None:
    body = b'{"events": []}\n'
    payload = ProviderPayload(
        provider="fonbet",
        kind="events_list_base",
        url="https://line.example/events/listBase",
        status_code=200,
        content_type="application/json",
        headers={},
        received_at=datetime(2026, 8, 17, 12, 30, 1, 123456, tzinfo=UTC),
        body=body,
        data=json.loads(body),
    )
    archive = FilesystemRawArchive(tmp_path)

    first = archive.archive(payload)
    second = archive.archive(payload)

    assert first.path.read_bytes() == body
    assert second.path.read_bytes() == body
    assert first.path != second.path
    assert first.content_sha256 == second.content_sha256
    assert first.path.parent == tmp_path / "fonbet" / "2026" / "08" / "17"
    metadata = json.loads(first.metadata_path.read_text(encoding="utf-8"))
    assert metadata["received_at"] == "2026-08-17T12:30:01.123456+00:00"
    assert metadata["content_sha256"] == first.content_sha256
    assert metadata["url"] == payload.url
    assert metadata["request_method"] == "GET"
    assert metadata["request_body"] is None
