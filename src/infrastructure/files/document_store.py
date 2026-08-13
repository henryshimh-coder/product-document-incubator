from __future__ import annotations

import hashlib
import os
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
