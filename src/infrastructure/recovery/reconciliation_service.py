from __future__ import annotations

import hashlib
import sqlite3
from contextlib import suppress
from pathlib import Path

from src.domain.enums import BaselineStatus, ChangeStatus
from src.domain.models import Baseline, BaselineManifest, RepairResult
from src.infrastructure.db.connection import connect
from src.infrastructure.files.manifest_store import ManifestStore


class ReconciliationService:
    """Keep the SQLite mirror consistent with the authoritative manifest.

    The manifest is the only source of truth: this service never writes
    SQLite state back into the manifest, and never invents a missing
    Project row.
    """

    def __init__(self, *, manifest_store: ManifestStore, db_path: Path, project_root: Path) -> None:
        self.manifest_store = manifest_store
        self.db_path = db_path
        self.project_root = project_root.resolve()

    def validate_manifest_mirror(self) -> RepairResult:
        try:
            snapshot = self.manifest_store.read_snapshot()
        except ValueError:
            return RepairResult(success=False, repaired_entities=[], error_code="MANIFEST_INVALID")
        manifest = snapshot.manifest
        if not self._assets_match(manifest):
            return RepairResult(
                success=False, repaired_entities=[], error_code="MANIFEST_ASSETS_INVALID"
            )
        if not self._mirror_matches(manifest, snapshot.sha256):
            return RepairResult(success=False, repaired_entities=[], error_code="MIRROR_MISMATCH")
        return RepairResult(success=True, repaired_entities=[], error_code=None)

    def rebuild_current_from_manifest(self) -> RepairResult:
        try:
            snapshot = self.manifest_store.read_snapshot()
        except ValueError:
            return RepairResult(success=False, repaired_entities=[], error_code="MANIFEST_INVALID")
        manifest = snapshot.manifest
        if not self._assets_match(manifest):
            return RepairResult(
                success=False, repaired_entities=[], error_code="MANIFEST_ASSETS_INVALID"
            )
        repaired: list[str] = []
        connection: sqlite3.Connection | None = None
        try:
            connection = connect(self.db_path)
            connection.execute("BEGIN IMMEDIATE")
            project_row = connection.execute(
                "SELECT id, current_baseline_id FROM projects WHERE id = ?",
                (manifest.project_id,),
            ).fetchone()
            if project_row is None:
                return self._fail(connection, "PROJECT_MISSING")
            baseline = Baseline(
                id=manifest.current_baseline_id,
                project_id=manifest.project_id,
                version=manifest.current_version,
                parent_baseline_id=manifest.parent_baseline_id,
                status=BaselineStatus.EFFECTIVE,
                full_document_path=manifest.full_document_path,
                card_snapshot_path=manifest.card_snapshot_path,
                manifest_sha256=snapshot.sha256,
                change_request_id=manifest.change_request_id,
                approved_by=manifest.approved_by,
                effective_at=manifest.published_at,
                created_at=manifest.published_at,
            )
            connection.execute(
                """
                INSERT INTO baselines (
                    id, project_id, version, parent_baseline_id, status,
                    full_document_path, card_snapshot_path, manifest_sha256,
                    change_request_id, approved_by, effective_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id = excluded.project_id,
                    version = excluded.version,
                    parent_baseline_id = excluded.parent_baseline_id,
                    status = excluded.status,
                    full_document_path = excluded.full_document_path,
                    card_snapshot_path = excluded.card_snapshot_path,
                    manifest_sha256 = excluded.manifest_sha256,
                    change_request_id = excluded.change_request_id,
                    approved_by = excluded.approved_by,
                    effective_at = excluded.effective_at
                """,
                (
                    baseline.id,
                    baseline.project_id,
                    baseline.version,
                    baseline.parent_baseline_id,
                    baseline.status.value,
                    baseline.full_document_path,
                    baseline.card_snapshot_path,
                    baseline.manifest_sha256,
                    baseline.change_request_id,
                    baseline.approved_by,
                    manifest.published_at.isoformat(),
                    baseline.created_at.isoformat(),
                ),
            )
            repaired.append("baselines")
            connection.execute(
                "UPDATE baselines SET status = ? WHERE project_id = ? AND status = ? AND id != ?",
                (
                    BaselineStatus.SUPERSEDED.value,
                    manifest.project_id,
                    BaselineStatus.EFFECTIVE.value,
                    manifest.current_baseline_id,
                ),
            )
            connection.execute(
                "UPDATE projects SET current_baseline_id = ?, updated_at = ? WHERE id = ?",
                (
                    manifest.current_baseline_id,
                    manifest.published_at.isoformat(),
                    manifest.project_id,
                ),
            )
            repaired.append("projects")
            if manifest.change_request_id is not None:
                change_row = connection.execute(
                    "SELECT status FROM change_requests WHERE id = ?",
                    (manifest.change_request_id,),
                ).fetchone()
                if change_row is None:
                    return self._fail(connection, "CHANGE_MISSING")
                if change_row["status"] == ChangeStatus.APPROVED.value:
                    connection.execute(
                        "UPDATE change_requests SET status = ?, updated_at = ? WHERE id = ?",
                        (
                            ChangeStatus.PUBLISHED.value,
                            manifest.published_at.isoformat(),
                            manifest.change_request_id,
                        ),
                    )
                    repaired.append("change_requests")
                elif change_row["status"] != ChangeStatus.PUBLISHED.value:
                    return self._fail(connection, "CHANGE_NOT_PUBLISHABLE")
            connection.commit()
        except sqlite3.Error:
            if connection is not None:
                with suppress(Exception):
                    connection.rollback()
            return RepairResult(
                success=False, repaired_entities=[], error_code="REPAIR_WRITE_FAILED"
            )
        finally:
            if connection is not None:
                with suppress(Exception):
                    connection.close()
        return RepairResult(success=True, repaired_entities=repaired, error_code=None)

    def _fail(self, connection: sqlite3.Connection, error_code: str) -> RepairResult:
        with suppress(Exception):
            connection.rollback()
        return RepairResult(success=False, repaired_entities=[], error_code=error_code)

    def _assets_match(self, manifest: BaselineManifest) -> bool:
        for relative_path, expected in (
            (manifest.full_document_path, manifest.full_document_sha256),
            (manifest.card_snapshot_path, manifest.card_snapshot_sha256),
        ):
            asset_path = (self.project_root / relative_path).resolve()
            try:
                if not asset_path.is_relative_to(self.project_root):
                    return False
                if hashlib.sha256(asset_path.read_bytes()).hexdigest() != expected:
                    return False
            except OSError:
                return False
        return True

    def _mirror_matches(self, manifest: BaselineManifest, manifest_sha256: str) -> bool:
        try:
            with connect(self.db_path) as connection:
                project_row = connection.execute(
                    "SELECT current_baseline_id FROM projects WHERE id = ?",
                    (manifest.project_id,),
                ).fetchone()
                if (
                    project_row is None
                    or project_row["current_baseline_id"] != manifest.current_baseline_id
                ):
                    return False
                baseline_row = connection.execute(
                    "SELECT * FROM baselines WHERE id = ?",
                    (manifest.current_baseline_id,),
                ).fetchone()
                if baseline_row is None:
                    return False
                if not (
                    baseline_row["project_id"] == manifest.project_id
                    and baseline_row["version"] == manifest.current_version
                    and baseline_row["parent_baseline_id"] == manifest.parent_baseline_id
                    and baseline_row["status"] == BaselineStatus.EFFECTIVE.value
                    and baseline_row["full_document_path"] == manifest.full_document_path
                    and baseline_row["card_snapshot_path"] == manifest.card_snapshot_path
                    and baseline_row["manifest_sha256"] == manifest_sha256
                    and baseline_row["change_request_id"] == manifest.change_request_id
                    and baseline_row["approved_by"] == manifest.approved_by
                    and baseline_row["effective_at"] == manifest.published_at.isoformat()
                ):
                    return False
                if manifest.change_request_id is not None:
                    change_row = connection.execute(
                        "SELECT status FROM change_requests WHERE id = ?",
                        (manifest.change_request_id,),
                    ).fetchone()
                    if change_row is None or change_row["status"] != ChangeStatus.PUBLISHED.value:
                        return False
        except sqlite3.Error:
            return False
        return True
