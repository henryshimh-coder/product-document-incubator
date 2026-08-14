from __future__ import annotations

from src.infrastructure.files.project_library import ProjectPaths


class ProjectAuditLog:
    def __init__(self, paths: ProjectPaths) -> None:
        self.path = paths.wiki_root / "log.md"

    def append(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
