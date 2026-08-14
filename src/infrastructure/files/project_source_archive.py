from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock

from src.domain.errors import DomainError, ErrorCode
from src.domain.services.file_safety import sanitize_filename, validate_upload
from src.infrastructure.files.project_library import ProjectPaths


@dataclass(frozen=True)
class ProjectArchiveResult:
    path: Path
    sha256: str
    size_bytes: int
    duplicate: bool


class ProjectSourceArchive:
    """Append-only raw archive contained by one product project's local root."""

    def __init__(self, *, paths: ProjectPaths, source_id: str, year: int) -> None:
        self.paths = paths
        self.source_id = source_id
        self.year = year

    def copy_from(self, local_path: Path) -> ProjectArchiveResult:
        source = local_path.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"source file not found: {local_path}")
        return self.save(source.name, source.read_bytes())

    def save(self, filename: str, payload: bytes) -> ProjectArchiveResult:
        filename = validate_upload(filename, payload)
        digest = hashlib.sha256(payload).hexdigest()
        raw_root = self.paths.raw_root.resolve()
        project_root = self.paths.project_root.resolve()
        if not raw_root.is_relative_to(project_root):
            raise DomainError(ErrorCode.FILE_TYPE_NOT_ALLOWED, "UNSAFE_PROJECT_RAW_ROOT")
        target = (
            raw_root / str(self.year) / self.source_id / sanitize_filename(filename)
        ).resolve()
        if not target.is_relative_to(raw_root):
            raise DomainError(ErrorCode.FILE_TYPE_NOT_ALLOWED, "UNSAFE_PROJECT_RAW_PATH")
        lock = FileLock(str(self.paths.system_root / "locks" / f"raw-{digest}.lock"))
        with lock:
            if target.is_file():
                return self._existing_or_conflict(target, digest, len(payload))
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary.write(payload)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    temporary_path = Path(temporary.name)
                try:
                    os.link(temporary_path, target)
                except FileExistsError:
                    return self._existing_or_conflict(target, digest, len(payload))
                finally:
                    temporary_path.unlink(missing_ok=True)
                    temporary_path = None
                archived = target.read_bytes()
                if hashlib.sha256(archived).hexdigest() != digest:
                    quarantine = self._quarantine(target)
                    raise RuntimeError(f"ARCHIVE_HASH_MISMATCH:{quarantine}")
                return ProjectArchiveResult(
                    path=target,
                    sha256=digest,
                    size_bytes=len(payload),
                    duplicate=False,
                )
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def discard_uncommitted(self, archived: ProjectArchiveResult) -> None:
        raw_root = self.paths.raw_root.resolve()
        path = archived.path.resolve()
        if not archived.duplicate and path.is_relative_to(raw_root):
            path.unlink(missing_ok=True)

    def _existing_or_conflict(
        self,
        target: Path,
        expected_sha256: str,
        size_bytes: int,
    ) -> ProjectArchiveResult:
        actual_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise DomainError(ErrorCode.DUPLICATE_SOURCE, "ARCHIVE_PATH_EXISTS")
        return ProjectArchiveResult(
            path=target,
            sha256=actual_sha256,
            size_bytes=size_bytes,
            duplicate=True,
        )

    def _quarantine(self, target: Path) -> Path:
        quarantine = self.paths.system_root / "quarantine" / target.name
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        os.replace(target, quarantine)
        return quarantine
