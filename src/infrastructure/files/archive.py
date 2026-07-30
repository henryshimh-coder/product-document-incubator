from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from filelock import FileLock

from src.domain.errors import DomainError, ErrorCode
from src.domain.services.file_safety import (
    resolve_source_archive_root,
    sanitize_filename,
    validate_business_id,
    validate_upload,
)


@dataclass(frozen=True)
class ArchiveResult:
    path: Path
    sha256: str
    size_bytes: int
    duplicate: bool


class SourceArchive:
    """Append-only source archive rooted at one project and source identifier."""

    def __init__(self, *, project_id: str, source_id: str) -> None:
        self.root = resolve_source_archive_root()
        self.project_id = validate_business_id(project_id, "project_id")
        self.source_id = validate_business_id(source_id, "source_id")

    def _project_root(self) -> Path:
        project_root = (self.root / self.project_id).resolve()
        if not project_root.is_relative_to(self.root):
            raise DomainError(ErrorCode.FILE_TYPE_NOT_ALLOWED, detail="UNSAFE_ARCHIVE_PATH")
        return project_root

    def _digest_lock(self, digest: str) -> FileLock:
        lock_path = self.root / ".locks" / f"{self.project_id}-{digest}.lock"
        return FileLock(str(lock_path))

    def _find_existing(self, digest: str) -> Path | None:
        project_root = self._project_root()
        if not project_root.exists():
            return None
        for existing in project_root.rglob("*"):
            if not existing.is_file():
                continue
            resolved = existing.resolve()
            if not resolved.is_relative_to(project_root):
                continue
            if sha256(resolved.read_bytes()).hexdigest() == digest:
                return resolved
        return None

    def save(self, filename: str, content: bytes) -> ArchiveResult:
        """Store bytes exactly once and return the existing record for an equal hash."""
        safe_filename = validate_upload(filename, content)
        digest = sha256(content).hexdigest()
        with self._digest_lock(digest):
            existing = self._find_existing(digest)
            if existing is not None:
                return ArchiveResult(existing, digest, len(content), duplicate=True)

            project_root = self._project_root()
            target = (project_root / self.source_id / sanitize_filename(safe_filename)).resolve()
            if not target.is_relative_to(project_root):
                raise DomainError(ErrorCode.FILE_TYPE_NOT_ALLOWED, detail="UNSAFE_ARCHIVE_PATH")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with target.open("xb") as archive_file:
                    archive_file.write(content)
            except FileExistsError:
                existing_digest = sha256(target.read_bytes()).hexdigest()
                if existing_digest == digest:
                    return ArchiveResult(target, digest, len(content), duplicate=True)
                raise DomainError(
                    ErrorCode.DUPLICATE_SOURCE, detail="ARCHIVE_PATH_EXISTS"
                ) from None
            return ArchiveResult(target, digest, len(content), duplicate=False)
