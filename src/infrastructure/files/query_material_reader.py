from __future__ import annotations

import hashlib
from pathlib import Path

from src.domain.models import SourceRecord
from src.infrastructure.files.extractor import extract_document


class LocalQueryMaterialReader:
    """Count the actual trusted local material behind an outbound query payload."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def total_chars(self, baseline_path: str, sources: list[SourceRecord]) -> int:
        documents: dict[str, int] = {}
        baseline = (self.project_root / baseline_path).resolve()
        if not baseline.is_relative_to(self.project_root):
            raise ValueError("baseline path leaves project root")
        baseline_bytes = baseline.read_bytes()
        baseline_text = baseline_bytes.decode("utf-8")
        documents[hashlib.sha256(baseline_bytes).hexdigest()] = len(baseline_text)
        for source in sources:
            extracted = extract_document(Path(source.archive_path), source_id=source.id)
            documents.setdefault(source.sha256, len(extracted.text))
        return sum(documents.values())
