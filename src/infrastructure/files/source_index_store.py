from __future__ import annotations

import json
import os
from uuid import uuid4

from src.domain.models import SourceRecord
from src.infrastructure.files.project_library import ProjectPaths


class SourceIndexStore:
    """Atomically mirrors project-local source metadata for file-library inspection."""

    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths
        self.path = paths.system_root / "source-index.json"

    def upsert(self, source: SourceRecord) -> None:
        if source.project_id != self.paths.project_id:
            raise ValueError("source index project_id mismatch")
        current = self._read()
        entries = {
            str(item["source_id"]): item
            for item in current["sources"]
            if isinstance(item, dict) and isinstance(item.get("source_id"), str)
        }
        entries[source.id] = {
            "source_id": source.id,
            "material_name": source.material_name,
            "material_series_id": source.material_series_id,
            "previous_source_id": source.previous_source_id,
            "material_version": source.document_version,
            "filename": source.original_filename,
            "archive_path": source.archive_path,
            "sha256": source.sha256,
            "source_type": source.source_type,
            "authority_level": source.authority_level.value,
            "security_level": source.security_level.value,
            "ingest_status": source.ingest_status,
            "created_at": source.created_at.isoformat(),
        }
        payload = {
            "schema_version": "2.1",
            "project_id": self.paths.project_id,
            "sources": [entries[key] for key in sorted(entries)],
        }
        self._atomic_write(payload)

    def _read(self) -> dict:
        if not self.path.is_file():
            return {"schema_version": "2.1", "project_id": self.paths.project_id, "sources": []}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("project_id") != self.paths.project_id
            or not isinstance(payload.get("sources"), list)
        ):
            raise ValueError("invalid project source index")
        return payload

    def _atomic_write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.parent / f".{self.path.name}.tmp-{uuid4().hex}"
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
