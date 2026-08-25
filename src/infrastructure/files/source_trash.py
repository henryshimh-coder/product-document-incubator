from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.domain.models import SourceRecord
from src.infrastructure.files.project_library import (
    ProjectPaths,
    require_canonical_project_path,
)


@dataclass(frozen=True)
class SourceTrashTransaction:
    source_directory: Path
    trash_directory: Path
    sha256: str


class SourceTrash:
    """Moves one canonical Raw source directory into local recoverable trash."""

    def __init__(
        self,
        paths: ProjectPaths,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.paths = paths
        self.now = now or (lambda: datetime.now(UTC))

    def move(self, source: SourceRecord) -> SourceTrashTransaction:
        source_directory, archive_path = self._resolve_source(source)
        actual_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        if actual_sha256 != source.sha256:
            raise ValueError("MATERIAL_SOURCE_INTEGRITY_FAILED")
        trash_root = self._trash_root()
        deleted_at = self.now().astimezone(UTC)
        destination = trash_root / (f"{deleted_at.strftime('%Y%m%dT%H%M%S.%fZ')}-{source.id}")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"source trash destination exists: {destination}")
        os.replace(source_directory, destination)
        transaction = SourceTrashTransaction(
            source_directory=source_directory,
            trash_directory=destination,
            sha256=actual_sha256,
        )
        try:
            moved_archive = destination / archive_path.name
            if hashlib.sha256(moved_archive.read_bytes()).hexdigest() != source.sha256:
                raise ValueError("MATERIAL_SOURCE_INTEGRITY_FAILED")
            self._write_manifest(
                destination,
                {
                    "schema_version": "1.0",
                    "deleted_at": deleted_at.isoformat(),
                    "requested_by": "Owner",
                    "sha256": actual_sha256,
                    "source": source.model_dump(mode="json"),
                },
            )
        except BaseException:
            self.restore(transaction)
            raise
        return transaction

    def restore(self, transaction: SourceTrashTransaction) -> None:
        if not transaction.trash_directory.exists():
            return
        if transaction.source_directory.exists() or transaction.source_directory.is_symlink():
            raise FileExistsError(
                f"source restore destination exists: {transaction.source_directory}"
            )
        transaction.source_directory.parent.mkdir(parents=True, exist_ok=True)
        os.replace(transaction.trash_directory, transaction.source_directory)

    def _resolve_source(self, source: SourceRecord) -> tuple[Path, Path]:
        if source.project_id != self.paths.project_id:
            raise ValueError("MATERIAL_PROJECT_MISMATCH")
        candidate = Path(source.archive_path)
        if not candidate.is_absolute():
            candidate = self.paths.project_root / candidate
        try:
            relative = candidate.relative_to(self.paths.project_root)
        except ValueError:
            raise ValueError("MATERIAL_ARCHIVE_PATH_INVALID") from None
        if (
            len(relative.parts) != 4
            or relative.parts[0] != "raw"
            or len(relative.parts[1]) != 4
            or not relative.parts[1].isdigit()
            or relative.parts[2] != source.id
        ):
            raise ValueError("MATERIAL_ARCHIVE_PATH_INVALID")
        try:
            archive_path = require_canonical_project_path(
                self.paths,
                relative.as_posix(),
                require_file=True,
            )
            source_directory = require_canonical_project_path(
                self.paths,
                Path(*relative.parts[:3]).as_posix(),
                require_directory=True,
            )
        except ValueError:
            raise ValueError("MATERIAL_ARCHIVE_PATH_INVALID") from None
        if archive_path.parent != source_directory or not source_directory.is_dir():
            raise ValueError("MATERIAL_ARCHIVE_PATH_INVALID")
        return source_directory, archive_path

    def _trash_root(self) -> Path:
        try:
            root = require_canonical_project_path(
                self.paths,
                ".incubator",
                require_directory=True,
            )
        except ValueError:
            raise ValueError("MATERIAL_TRASH_PATH_INVALID") from None
        for name in ("trash", "sources"):
            root = root / name
            if root.is_symlink() or (root.exists() and not root.is_dir()):
                raise ValueError("MATERIAL_TRASH_PATH_INVALID")
            root.mkdir(exist_ok=True)
            if root.resolve() != root:
                raise ValueError("MATERIAL_TRASH_PATH_INVALID")
        return root

    @staticmethod
    def _write_manifest(directory: Path, payload: dict) -> None:
        manifest = directory / "manifest.json"
        temporary = directory / f".manifest.json.tmp-{uuid4().hex}"
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, manifest)
        finally:
            temporary.unlink(missing_ok=True)
