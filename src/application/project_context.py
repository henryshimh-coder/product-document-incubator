from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.infrastructure.files.project_library import ProjectPaths


@dataclass(frozen=True)
class ProjectContext:
    """The runtime boundary for a registered project and the central control database."""

    project_id: str
    paths: ProjectPaths
    db_path: Path
