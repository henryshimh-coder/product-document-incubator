from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from src.infrastructure.files.project_library import ProjectPaths


class DocumentStore:
    """Project-contained, atomic storage for candidate Markdown documents."""

    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths

    def write_draft(self, version_id: str, markdown: str) -> tuple[str, str]:
        relative_path = Path("wiki") / "drafts" / version_id / "产品方案.md"
        target = (self.paths.project_root / relative_path).resolve()
        if not target.is_relative_to(self.paths.wiki_root.resolve()):
            raise ValueError("DRAFT_PATH_INVALID")
        if target.exists():
            raise FileExistsError("DRAFT_ALREADY_EXISTS")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.tmp-{uuid4().hex}"
        payload = markdown.encode("utf-8")
        try:
            with temporary.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return relative_path.as_posix(), hashlib.sha256(payload).hexdigest()

    def read_current(self) -> str | None:
        current = (self.paths.wiki_root / "current" / "当前产品方案.md").resolve()
        if not current.is_relative_to(self.paths.wiki_root.resolve()):
            raise ValueError("CURRENT_PATH_INVALID")
        if not current.is_file():
            return None
        return current.read_text(encoding="utf-8")

    def replace_draft(self, markdown_path: str, markdown: str) -> str:
        target = (self.paths.project_root / markdown_path).resolve()
        drafts_root = (self.paths.wiki_root / "drafts").resolve()
        if not target.is_relative_to(drafts_root) or not target.is_file():
            raise ValueError("DRAFT_PATH_INVALID")
        temporary = target.parent / f".{target.name}.tmp-{uuid4().hex}"
        payload = markdown.encode("utf-8")
        try:
            with temporary.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return hashlib.sha256(payload).hexdigest()

    def append_log(self, message: str) -> None:
        log_path = (self.paths.wiki_root / "log.md").resolve()
        if not log_path.is_relative_to(self.paths.wiki_root.resolve()):
            raise ValueError("LOG_PATH_INVALID")
        with log_path.open("a", encoding="utf-8") as output:
            output.write(message)

    def commit_version(
        self,
        *,
        version_id: str,
        markdown: str,
        cards: list[dict],
    ) -> tuple[str, str, str, str]:
        versions_root = (self.paths.wiki_root / "versions").resolve()
        target_dir = (versions_root / version_id).resolve()
        if target_dir.parent != versions_root or target_dir.exists():
            raise ValueError("VERSION_PATH_INVALID")
        target_dir.mkdir(parents=True)
        markdown_path = target_dir / "产品方案.md"
        cards_path = target_dir / "cards.json"
        try:
            self._write_synced(markdown_path, markdown.encode("utf-8"))
            self._write_synced(
                cards_path,
                (json.dumps(cards, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                    "utf-8"
                ),
            )
            self._sync_directory(target_dir)
            return (
                (Path("wiki") / "versions" / version_id / "产品方案.md").as_posix(),
                hashlib.sha256(markdown_path.read_bytes()).hexdigest(),
                (Path("wiki") / "versions" / version_id / "cards.json").as_posix(),
                hashlib.sha256(cards_path.read_bytes()).hexdigest(),
            )
        except BaseException:
            for child in target_dir.iterdir():
                child.unlink(missing_ok=True)
            target_dir.rmdir()
            raise

    def sync_current_from_version(self, version_markdown_path: str) -> None:
        source = (self.paths.project_root / version_markdown_path).resolve()
        current = (self.paths.wiki_root / "current" / "当前产品方案.md").resolve()
        if not source.is_relative_to((self.paths.wiki_root / "versions").resolve()):
            raise ValueError("VERSION_PATH_INVALID")
        if not current.is_relative_to(self.paths.wiki_root.resolve()):
            raise ValueError("CURRENT_PATH_INVALID")
        current.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_replace(current, source.read_bytes())

    def discard_version(self, version_id: str) -> None:
        versions_root = (self.paths.wiki_root / "versions").resolve()
        target_dir = (versions_root / version_id).resolve()
        if target_dir.parent != versions_root:
            raise ValueError("VERSION_PATH_INVALID")
        if target_dir.is_dir():
            shutil.rmtree(target_dir)

    def write_export(self, filename: str, payload: bytes) -> Path:
        target = (self.paths.exports_root / filename).resolve()
        exports_root = self.paths.exports_root.resolve()
        if not target.is_relative_to(exports_root) or target.suffix != ".md":
            raise ValueError("EXPORT_PATH_INVALID")
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_replace(target, payload)
        return target

    @staticmethod
    def _write_synced(path: Path, payload: bytes) -> None:
        with path.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())

    @staticmethod
    def _sync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _atomic_replace(self, target: Path, payload: bytes) -> None:
        temporary = target.parent / f".{target.name}.tmp-{uuid4().hex}"
        try:
            self._write_synced(temporary, payload)
            os.replace(temporary, target)
            self._sync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)
