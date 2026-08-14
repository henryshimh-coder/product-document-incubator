from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from src.application.dto.materials import SensitiveComparisonInput
from src.application.ports.repositories import SourceRepository
from src.domain.enums import SecurityLevel
from src.infrastructure.files.document_store import DocumentStore
from src.infrastructure.files.extractor import extract_document_bytes
from src.infrastructure.files.project_library import ProjectPaths


@dataclass(frozen=True)
class SensitiveComparisonView:
    left_label: str
    left_markdown: str
    sensitive_text: str


class CompareSensitiveSource:
    """Read sensitive source material locally; this class has no gateway or model dependencies."""

    def __init__(
        self, *, paths: ProjectPaths, sources: SourceRepository, store: DocumentStore
    ) -> None:
        self.paths, self.sources, self.store = paths, sources, store

    def execute(self, command: SensitiveComparisonInput) -> SensitiveComparisonView:
        if command.project_id != self.paths.project_id:
            raise ValueError("SENSITIVE_SOURCE_PROJECT_MISMATCH")
        source = self.sources.get(command.source_id)
        if source.project_id != command.project_id:
            raise ValueError("SENSITIVE_SOURCE_PROJECT_MISMATCH")
        if source.security_level not in (
            SecurityLevel.L3_CONFIDENTIAL,
            SecurityLevel.L4_RESTRICTED,
        ):
            raise ValueError("SENSITIVE_SOURCE_LEVEL_REQUIRED")
        path = Path(source.archive_path).resolve()
        raw_root = self.paths.raw_root.resolve()
        if not path.is_relative_to(raw_root) or not path.is_file():
            raise ValueError("SENSITIVE_SOURCE_PATH_INVALID")
        payload = path.read_bytes()
        if (
            len(payload) != source.size_bytes
            or hashlib.sha256(payload).hexdigest() != source.sha256
        ):
            raise ValueError("SENSITIVE_SOURCE_INTEGRITY_FAILED")
        extracted = extract_document_bytes(
            payload, filename=source.original_filename, source_id=source.id
        )
        current = self.store.read_current()
        if current is None:
            template = self.paths.schema_root / "product-document-template.md"
            current = template.read_text(encoding="utf-8").replace("{产品名称}", command.project_id)
        return SensitiveComparisonView(
            left_label="当前生效方案", left_markdown=current, sensitive_text=extracted.text
        )
