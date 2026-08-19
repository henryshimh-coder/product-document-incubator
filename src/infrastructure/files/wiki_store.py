from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from uuid import uuid4

from src.domain.errors import DomainError, ErrorCode
from src.domain.wiki import WikiPageChange
from src.infrastructure.files.project_library import ProjectPaths


class WikiStore:
    """Own safe, hash-checked file operations below one resolved project root."""

    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths
        self.project_root = paths.project_root.resolve()

    @staticmethod
    def sha256_bytes(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def sha256_file(self, path: Path) -> str:
        return self.sha256_bytes(path.read_bytes())

    def target(self, relative_path: str) -> Path:
        if not self._is_governed_target(relative_path):
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "TARGET_NOT_GOVERNED")
        lexical_target = self.project_root / relative_path
        target = lexical_target.resolve()
        if not target.is_relative_to(self.project_root):
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "TARGET_OUTSIDE_PROJECT")
        if target != lexical_target:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "TARGET_SYMLINK_FORBIDDEN")
        return target

    def verify_before(self, change: WikiPageChange) -> None:
        target = self.target(change.relative_path)
        if change.before_sha256 is None:
            matches = not target.exists()
        else:
            matches = target.is_file() and self.sha256_file(target) == change.before_sha256
        if not matches:
            raise DomainError(
                ErrorCode.WIKI_CONCURRENT_MODIFICATION,
                f"TARGET_CHANGED:{change.relative_path}",
            )

    def stage(self, change: WikiPageChange, staged_root: Path) -> None:
        payload = change.markdown.encode("utf-8")
        if self.sha256_bytes(payload) != change.after_sha256:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "AFTER_SHA256_MISMATCH")
        staged_path = self._transaction_path(staged_root, change.relative_path)
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(staged_path, payload)

    def backup(self, change: WikiPageChange, backup_root: Path) -> None:
        if change.before_sha256 is None:
            return
        target = self.target(change.relative_path)
        backup_path = self._transaction_path(backup_root, change.relative_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target, backup_path)
        if self.sha256_file(backup_path) != change.before_sha256:
            raise DomainError(ErrorCode.WIKI_CONCURRENT_MODIFICATION, "BACKUP_SHA256_MISMATCH")

    def commit_staged(self, change: WikiPageChange, staged_root: Path) -> None:
        self.verify_before(change)
        staged_path = self._transaction_path(staged_root, change.relative_path)
        if not staged_path.is_file() or self.sha256_file(staged_path) != change.after_sha256:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "STAGED_SHA256_MISMATCH")
        target = self.target(change.relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, staged_path.read_bytes())

    def verify_after(self, change: WikiPageChange) -> None:
        target = self.target(change.relative_path)
        if not target.is_file() or self.sha256_file(target) != change.after_sha256:
            raise RuntimeError("WIKI_RECOVERY_REQUIRED: AFTER_SHA256_MISMATCH")

    def restore(self, change: WikiPageChange, backup_root: Path) -> None:
        target = self.target(change.relative_path)
        if change.before_sha256 is None:
            if not target.exists():
                return
            if not target.is_file() or self.sha256_file(target) != change.after_sha256:
                raise RuntimeError("WIKI_RECOVERY_REQUIRED: TARGET_HAS_OWNER_EDIT")
            target.unlink()
            return

        if not target.is_file():
            raise RuntimeError("WIKI_RECOVERY_REQUIRED: TARGET_MISSING")
        target_sha256 = self.sha256_file(target)
        if target_sha256 == change.before_sha256:
            return
        if target_sha256 != change.after_sha256:
            raise RuntimeError("WIKI_RECOVERY_REQUIRED: TARGET_HAS_OWNER_EDIT")
        backup_path = self._transaction_path(backup_root, change.relative_path)
        if not backup_path.is_file() or self.sha256_file(backup_path) != change.before_sha256:
            raise RuntimeError("WIKI_RECOVERY_REQUIRED: BACKUP_MISSING_OR_INVALID")
        self._atomic_write(target, backup_path.read_bytes())

    @staticmethod
    def _is_governed_target(relative_path: str) -> bool:
        path = Path(relative_path)
        if (
            path.is_absolute()
            or "\\" in relative_path
            or path.as_posix() != relative_path
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            return False
        if relative_path in {
            "wiki/index.md",
            "wiki/log.md",
            ".incubator/source-index.json",
        }:
            return True
        return relative_path.endswith(".md") and (
            relative_path.startswith("wiki/sources/")
            or relative_path.startswith("wiki/topics/")
        )

    @staticmethod
    def _transaction_path(root: Path, relative_path: str) -> Path:
        resolved_root = root.resolve()
        path = (resolved_root / relative_path).resolve()
        if not path.is_relative_to(resolved_root):
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "TRANSACTION_PATH_ESCAPE")
        return path

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        temporary = path.parent / f".{path.name}.tmp-{uuid4().hex}"
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
