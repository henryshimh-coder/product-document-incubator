from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from src.domain.incubator import IncubatorSettings

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
        cls._validate_project_id(project_id)
        resolved_library = library_root.expanduser().resolve()
        lexical_project = resolved_library / project_id
        if lexical_project.is_symlink():
            raise ValueError("project root must not be a symlink")
        resolved_project = lexical_project.resolve()
        if resolved_project != lexical_project:
            raise ValueError("project root must resolve to its declared project_id")
        if not resolved_project.is_relative_to(resolved_library):
            raise ValueError("project path resolves outside library_root")
        return cls._build(resolved_library, project_id, resolved_project)

    @classmethod
    def for_registered_root(
        cls, library_root: Path, project_id: str, project_root: Path
    ) -> ProjectPaths:
        cls._validate_project_id(project_id)
        lexical_root = project_root.expanduser().absolute()
        if lexical_root.is_symlink():
            raise ValueError("project root must not be a symlink")
        return cls._build(library_root.expanduser().resolve(), project_id, lexical_root.resolve())

    @staticmethod
    def _validate_project_id(project_id: str) -> None:
        if PROJECT_ID_PATTERN.fullmatch(project_id) is None:
            raise ValueError("project_id must match ^[A-Z0-9][A-Z0-9_-]{0,63}$")

    @classmethod
    def _build(cls, library_root: Path, project_id: str, project_root: Path) -> ProjectPaths:
        def inside_project(relative_path: str) -> Path:
            candidate = (project_root / relative_path).resolve()
            if not candidate.is_relative_to(project_root):
                raise ValueError("derived project path resolves outside library_root")
            return candidate

        system_root = inside_project(".incubator")
        manifest_path = (system_root / "current-baseline.json").resolve()
        if not manifest_path.is_relative_to(project_root):
            raise ValueError("derived project path resolves outside library_root")
        return cls(
            library_root=library_root,
            project_id=project_id,
            project_root=project_root,
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
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.pointer_path = pointer_path
        self.home_directory = home_directory or Path.home()
        self.environ = os.environ if environ is None else environ

    def resolve(self) -> Path:
        environment_root = self.environ.get("INCUBATOR_LIBRARY_ROOT")
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
        temporary_path = self.pointer_path.parent / (f".{self.pointer_path.name}.tmp-{uuid4().hex}")
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


class JsonIncubatorSettingsStore:
    def __init__(self, library_root: Path) -> None:
        self.library_root = library_root.expanduser().resolve()
        self.settings_path = (self.library_root / ".incubator/settings.json").resolve()
        if not self.settings_path.is_relative_to(self.library_root):
            raise ValueError("settings path resolves outside library_root")

    def load(self) -> IncubatorSettings | None:
        if not self.settings_path.is_file():
            return None
        payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        settings = IncubatorSettings.model_validate(payload)
        saved_root = Path(settings.library_root).expanduser().resolve()
        if saved_root != self.library_root:
            raise ValueError("settings library_root does not match settings store")
        return settings

    def save(self, settings: IncubatorSettings) -> None:
        saved_root = Path(settings.library_root).expanduser().resolve()
        if saved_root != self.library_root:
            raise ValueError("settings library_root does not match settings store")
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.settings_path.parent / f".settings.json.tmp-{uuid4().hex}"
        try:
            temporary_path.write_text(
                json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_path, self.settings_path)
        finally:
            temporary_path.unlink(missing_ok=True)
