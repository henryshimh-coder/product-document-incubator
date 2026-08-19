from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.infrastructure.files.project_library import ProjectPaths


def read_wiki_schema_version(paths: ProjectPaths) -> str:
    """Read the project-local Wiki schema marker, retaining legacy 2.1 behavior."""
    try:
        payload = json.loads((paths.system_root / "project.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "2.1"
    if not isinstance(payload, dict):
        return "2.1"
    return "2.2" if payload.get("wiki_schema_version") == "2.2" else "2.1"


@dataclass(frozen=True)
class ProjectContext:
    """The runtime boundary for a registered project and the central control database."""

    project_id: str
    paths: ProjectPaths
    db_path: Path
    wiki_schema_version: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "wiki_schema_version", read_wiki_schema_version(self.paths))
