from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from src.application.ports.dashboard import ManifestSnapshot
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import BaselineManifest, ChangeRequest


class ManifestDurabilityUncertainError(RuntimeError):
    """The replacement occurred, but its directory sync could not be confirmed."""


def fsync_file(path: Path) -> None:
    with path.open("rb") as file:
        os.fsync(file.fileno())


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ManifestStore:
    """Reads and atomically replaces the authoritative current-baseline manifest."""

    def __init__(self, path: Path, *, project_root: Path | None = None) -> None:
        self.path = path
        self.project_root = None if project_root is None else project_root.resolve()

    def read_and_validate(self) -> BaselineManifest:
        return self.read_snapshot().manifest

    def build_candidate(
        self,
        *,
        current: BaselineManifest,
        change: ChangeRequest,
        approved_by: str,
        published_at: datetime,
        full_document_path: str,
        card_snapshot_path: str,
        full_document_sha256: str,
        card_snapshot_sha256: str,
    ) -> BaselineManifest:
        return BaselineManifest(
            schema_version=current.schema_version,
            project_id=current.project_id,
            current_baseline_id=f"BASE-{change.target_version}",
            current_version=change.target_version,
            parent_baseline_id=current.current_baseline_id,
            full_document_path=full_document_path,
            card_snapshot_path=card_snapshot_path,
            full_document_sha256=full_document_sha256,
            card_snapshot_sha256=card_snapshot_sha256,
            change_request_id=change.id,
            approved_by=approved_by,
            published_at=published_at,
        )

    def validate_candidate(
        self,
        candidate: BaselineManifest,
        *,
        current: BaselineManifest,
        staging_dir: Path,
    ) -> None:
        """Fail closed unless the candidate links to current and matches staged files."""
        if candidate.project_id != current.project_id:
            raise DomainError(ErrorCode.RELEASE_FAILED, "CANDIDATE_PROJECT_MISMATCH")
        if candidate.parent_baseline_id != current.current_baseline_id:
            raise DomainError(ErrorCode.RELEASE_FAILED, "CANDIDATE_PARENT_MISMATCH")
        if candidate.current_version == current.current_version:
            raise DomainError(ErrorCode.TARGET_VERSION_ALREADY_EFFECTIVE)
        expected_dir = Path("data/obsidian_vault/02_Current_Baseline") / candidate.current_version
        if candidate.full_document_path != str(
            expected_dir / "full.md"
        ) or candidate.card_snapshot_path != str(expected_dir / "cards.json"):
            raise DomainError(ErrorCode.RELEASE_FAILED, "CANDIDATE_PATH_MISMATCH")
        for path in (candidate.full_document_path, candidate.card_snapshot_path):
            resolved = Path(path)
            if resolved.is_absolute() or ".." in resolved.parts:
                raise DomainError(ErrorCode.RELEASE_FAILED, "CANDIDATE_PATH_UNSAFE")
        if self._sha256(staging_dir / "full.md") != candidate.full_document_sha256:
            raise DomainError(ErrorCode.RELEASE_FAILED, "CANDIDATE_FULL_HASH_MISMATCH")
        if self._sha256(staging_dir / "cards.json") != candidate.card_snapshot_sha256:
            raise DomainError(ErrorCode.RELEASE_FAILED, "CANDIDATE_CARDS_HASH_MISMATCH")

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def read_snapshot(self) -> ManifestSnapshot:
        try:
            raw = self.path.read_bytes()
            payload = json.loads(raw)
            return ManifestSnapshot(
                manifest=BaselineManifest.model_validate(payload),
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise ValueError(f"Invalid baseline manifest at {self.path}: {error}") from error

    def atomic_replace(self, manifest: BaselineManifest) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        payload = (
            json.dumps(
                manifest.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2
            )
            + "\n"
        )
        try:
            temp_path.write_text(payload, encoding="utf-8")
            fsync_file(temp_path)
            os.replace(temp_path, self.path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        try:
            fsync_directory(self.path.parent)
        except OSError as error:
            raise ManifestDurabilityUncertainError(
                f"Manifest replacement completed but durability is uncertain: {self.path}"
            ) from error
