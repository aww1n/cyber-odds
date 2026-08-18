from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.providers.base import ProviderPayload


@dataclass(frozen=True, slots=True)
class ArchivedPayload:
    path: Path
    metadata_path: Path
    content_sha256: str
    size_bytes: int


class FilesystemRawArchive:
    """Append-only filesystem archive for exact external response bytes."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def archive(self, payload: ProviderPayload) -> ArchivedPayload:
        digest = hashlib.sha256(payload.body).hexdigest()
        received = payload.received_at
        target_dir = (
            self._root
            / payload.provider
            / f"{received.year:04d}"
            / f"{received.month:02d}"
            / f"{received.day:02d}"
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = received.strftime("%H%M%S_%fZ")
        safe_kind = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in payload.kind
        )
        nonce = uuid4().hex[:8]
        target = target_dir / f"{timestamp}_{safe_kind}_{digest[:12]}_{nonce}.json"

        self._atomic_write(target, payload.body)

        metadata_path = target.with_suffix(".meta.json")
        safe_headers = {
            key: value
            for key, value in payload.headers.items()
            if key.casefold()
            not in {"set-cookie", "cookie", "authorization", "proxy-authorization"}
        }
        metadata = {
            "provider": payload.provider,
            "kind": payload.kind,
            "url": payload.url,
            "status_code": payload.status_code,
            "content_type": payload.content_type,
            "headers": safe_headers,
            "received_at": payload.received_at.isoformat(),
            "content_sha256": digest,
            "size_bytes": len(payload.body),
            "raw_path": str(target),
            "request_method": payload.request_method,
            "request_body_sha256": (
                hashlib.sha256(payload.request_body).hexdigest()
                if payload.request_body is not None
                else None
            ),
            "request_body": (
                payload.request_body.decode("utf-8", errors="replace")
                if payload.request_body is not None
                else None
            ),
        }
        self._atomic_write(
            metadata_path,
            (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode(),
        )
        return ArchivedPayload(
            path=target,
            metadata_path=metadata_path,
            content_sha256=digest,
            size_bytes=len(payload.body),
        )

    @staticmethod
    def _atomic_write(target: Path, content: bytes) -> None:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, target)
