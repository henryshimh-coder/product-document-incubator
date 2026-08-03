from __future__ import annotations

import hashlib
from pathlib import Path

from src.domain.enums import BaselineStatus
from src.domain.models import BaselineManifest
from src.infrastructure.db.repositories import (
    SqliteBaselineRepository,
    SqliteProjectRepository,
)


class ManifestIntegrityChecker:
    """Validate authoritative assets and their SQLite mirror without changing either."""

    def __init__(self, *, project_root: Path, db_path: Path, manifest_path: Path) -> None:
        self.project_root = project_root.resolve()
        self.manifest_path = manifest_path
        self.projects = SqliteProjectRepository(db_path)
        self.baselines = SqliteBaselineRepository(db_path)

    def validate(self, manifest: BaselineManifest) -> bool:
        try:
            if not self._asset_matches(
                manifest.full_document_path,
                manifest.full_document_sha256,
            ) or not self._asset_matches(
                manifest.card_snapshot_path,
                manifest.card_snapshot_sha256,
            ):
                return False
            project = self.projects.get(manifest.project_id)
            baseline = self.baselines.get(manifest.current_baseline_id)
            return (
                project.current_baseline_id == manifest.current_baseline_id
                and baseline.project_id == manifest.project_id
                and baseline.version == manifest.current_version
                and baseline.parent_baseline_id == manifest.parent_baseline_id
                and baseline.status == BaselineStatus.EFFECTIVE
                and baseline.full_document_path == manifest.full_document_path
                and baseline.card_snapshot_path == manifest.card_snapshot_path
                and baseline.manifest_sha256 == self._sha256(self.manifest_path)
                and baseline.change_request_id == manifest.change_request_id
                and baseline.approved_by == manifest.approved_by
                and baseline.effective_at == manifest.published_at
            )
        except (KeyError, OSError, ValueError):
            return False

    def _asset_matches(self, relative_path: str, expected_sha256: str) -> bool:
        asset_path = (self.project_root / relative_path).resolve()
        if not asset_path.is_relative_to(self.project_root):
            return False
        return self._sha256(asset_path) == expected_sha256

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
