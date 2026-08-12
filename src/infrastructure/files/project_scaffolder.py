from __future__ import annotations

import ctypes
import json
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.application.dto.projects import CreateProjectInput
from src.infrastructure.files.project_library import ProjectPaths

SCHEMA_FILENAMES = (
    "AGENTS.md",
    "product-document-template.md",
    "field-conventions.md",
)

AT_FDCWD = -100
RENAME_NOREPLACE = 1
RENAME_EXCL = 0x00000004


def _rename_directory_without_replace(source: Path, destination: Path) -> None:
    """Atomically publish a directory and fail if the destination exists."""
    if os.name == "nt":
        os.rename(source, destination)
        return

    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        arguments = (source_bytes, destination_bytes, RENAME_EXCL)
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        arguments = (
            AT_FDCWD,
            source_bytes,
            AT_FDCWD,
            destination_bytes,
            RENAME_NOREPLACE,
        )
    else:
        raise OSError("atomic no-replace directory rename is unavailable")
    rename.restype = ctypes.c_int
    if rename(*arguments) != 0:
        error_number = ctypes.get_errno()
        if error_number == 0:
            raise OSError("atomic no-replace directory rename failed")
        raise OSError(error_number, os.strerror(error_number), destination)


@dataclass(frozen=True)
class PreparedProject:
    project_id: str
    temp_root: Path
    paths: ProjectPaths


class ProjectScaffolder:
    def __init__(
        self,
        *,
        library_root: Path,
        schema_source: Path,
        now: Callable[[], datetime],
    ) -> None:
        self.library_root = library_root.expanduser().resolve()
        self.schema_source = schema_source.expanduser().resolve()
        self.now = now

    def prepare(self, command: CreateProjectInput) -> PreparedProject:
        paths = ProjectPaths.for_project(self.library_root, command.project_id)
        if paths.project_root.exists():
            raise ValueError(f"project already exists: {command.project_id}")
        self._validate_schema_source()
        self.library_root.mkdir(parents=True, exist_ok=True)
        temp_root = self.library_root / f".{command.project_id}.tmp-{uuid4().hex}"
        prepared = PreparedProject(
            project_id=command.project_id,
            temp_root=temp_root,
            paths=paths,
        )
        try:
            self._build_tree(prepared, command)
            return prepared
        except BaseException:
            self.abort(prepared)
            raise

    def validate(self, prepared: PreparedProject) -> None:
        required_directories = (
            "raw",
            "wiki/current",
            "wiki/drafts",
            "wiki/versions",
            "wiki/topics",
            "schema",
            "exports",
            ".incubator",
        )
        required_files = (
            "wiki/index.md",
            "wiki/log.md",
            ".incubator/project.json",
            ".incubator/source-index.json",
            *(f"schema/{filename}" for filename in SCHEMA_FILENAMES),
        )
        if any(not (prepared.temp_root / item).is_dir() for item in required_directories):
            raise OSError("project scaffold is missing a required directory")
        if any(not (prepared.temp_root / item).is_file() for item in required_files):
            raise OSError("project scaffold is missing a required file")

    def commit(self, prepared: PreparedProject) -> ProjectPaths:
        if prepared.paths.project_root.exists():
            raise ValueError(f"project already exists: {prepared.project_id}")
        _rename_directory_without_replace(prepared.temp_root, prepared.paths.project_root)
        return ProjectPaths.for_project(self.library_root, prepared.project_id)

    def abort(self, prepared: PreparedProject) -> None:
        temp_root = prepared.temp_root.resolve()
        if (
            temp_root.parent == self.library_root
            and temp_root.name.startswith(f".{prepared.project_id}.tmp-")
            and temp_root.exists()
        ):
            shutil.rmtree(temp_root)

    def _validate_schema_source(self) -> None:
        missing = [
            filename
            for filename in SCHEMA_FILENAMES
            if not (self.schema_source / filename).is_file()
        ]
        if missing:
            raise FileNotFoundError(f"incubator schema assets missing: {', '.join(missing)}")

    def _build_tree(self, prepared: PreparedProject, command: CreateProjectInput) -> None:
        for relative_path in (
            "raw",
            "wiki/current",
            "wiki/drafts",
            "wiki/versions",
            "wiki/topics",
            "schema",
            "exports",
            ".incubator",
        ):
            (prepared.temp_root / relative_path).mkdir(parents=True, exist_ok=False)

        for filename in SCHEMA_FILENAMES:
            shutil.copyfile(
                self.schema_source / filename,
                prepared.temp_root / "schema" / filename,
            )

        created_at = self.now().isoformat()
        (prepared.temp_root / "wiki/index.md").write_text(
            f"# {command.name}\n\n当前项目尚未发布生效产品文档。\n",
            encoding="utf-8",
        )
        (prepared.temp_root / "wiki/log.md").write_text(
            f"# 项目日志\n\n- {created_at} 创建产品文档孵化项目。\n",
            encoding="utf-8",
        )
        self._write_json(
            prepared.temp_root / ".incubator/project.json",
            {
                "schema_version": "2.0",
                "product_name": "产品文档孵化器",
                "project_id": command.project_id,
                "name": command.name,
                "description": command.description,
                "initial_display_version": command.initial_display_version,
                "allow_external_model": command.allow_external_model,
                "created_at": created_at,
            },
        )
        self._write_json(
            prepared.temp_root / ".incubator/source-index.json",
            {
                "schema_version": "2.0",
                "project_id": command.project_id,
                "sources": [],
            },
        )

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
