from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

PROJECT_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,63}$")


@dataclass(frozen=True)
class ProjectPaths:
    library_root: Path
    project_id: str
    project_root: Path
    raw_root: Path
    wiki_root: Path
    schema_root: Path
    exports_root: Path
    system_root: Path
    manifest_path: Path

    @classmethod
    def for_project(cls, library_root: Path, project_id: str) -> ProjectPaths:
        if PROJECT_ID_PATTERN.fullmatch(project_id) is None:
            raise ValueError("project_id must match ^[A-Z0-9][A-Z0-9_-]{0,63}$")

        resolved_library = library_root.expanduser().resolve()
        resolved_project = (resolved_library / project_id).resolve()
        if not resolved_project.is_relative_to(resolved_library):
            raise ValueError("project path resolves outside library_root")

        def inside_project(relative_path: str) -> Path:
            candidate = (resolved_project / relative_path).resolve()
            if not candidate.is_relative_to(resolved_project):
                raise ValueError("derived project path resolves outside library_root")
            return candidate

        system_root = inside_project(".incubator")
        manifest_path = (system_root / "current-baseline.json").resolve()
        if not manifest_path.is_relative_to(resolved_project):
            raise ValueError("derived project path resolves outside library_root")
        return cls(
            library_root=resolved_library,
            project_id=project_id,
            project_root=resolved_project,
            raw_root=inside_project("raw"),
            wiki_root=inside_project("wiki"),
            schema_root=inside_project("schema"),
            exports_root=inside_project("exports"),
            system_root=system_root,
            manifest_path=manifest_path,
        )


class ProjectLibraryLocator:
    def __init__(
        self,
        pointer_path: Path = Path("data/local_state/incubator-root.json"),
        home_directory: Path | None = None,
    ) -> None:
        self.pointer_path = pointer_path
        self.home_directory = home_directory or Path.home()

    def resolve(self) -> Path:
        environment_root = os.getenv("INCUBATOR_LIBRARY_ROOT")
        if environment_root and environment_root.strip():
            return Path(environment_root).expanduser().resolve()

        if self.pointer_path.is_file():
            payload = json.loads(self.pointer_path.read_text(encoding="utf-8"))
            pointer_root = payload.get("library_root")
            if not isinstance(pointer_root, str) or not pointer_root.strip():
                raise ValueError("incubator library pointer has no valid library_root")
            return Path(pointer_root).expanduser().resolve()

        return (self.home_directory / "Documents/产品文档孵化器项目库").expanduser().resolve()

    def save_pointer(self, library_root: Path) -> Path:
        resolved_root = library_root.expanduser().resolve()
        self.pointer_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.pointer_path.parent / (
            f".{self.pointer_path.name}.tmp-{uuid4().hex}"
        )
        try:
            temporary_path.write_text(
                json.dumps(
                    {"library_root": str(resolved_root)},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_path, self.pointer_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return resolved_root
