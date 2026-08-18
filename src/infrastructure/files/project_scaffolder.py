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
from uuid import UUID, uuid4

from src.application.dto.projects import CreateProjectInput
from src.infrastructure.files.project_library import ProjectPaths

ROOT_TEMPLATE_MAP = {
    "root-README.md": "README.md",
    "root-AGENTS.md": "AGENTS.md",
}

SCHEMA_FILENAMES = (
    "AGENTS.md",
    "ingest-contract.md",
    "source-page-template.md",
    "topic-page-template.md",
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

    def prepare(self, command: CreateProjectInput, *, parent_root: Path) -> PreparedProject:
        parent = parent_root.expanduser().resolve()
        target = parent / command.project_id
        paths = ProjectPaths.for_registered_root(self.library_root, command.project_id, target)
        if paths.project_root.exists():
            raise ValueError(f"project already exists: {command.project_id}")
        self._validate_schema_source()
        parent.mkdir(parents=True, exist_ok=True)
        temp_root = parent / f".{command.project_id}.tmp-{uuid4().hex}"
        prepared = PreparedProject(
            project_id=command.project_id,
            temp_root=temp_root,
            paths=paths,
        )
        try:
            self._build_tree(temp_root, command)
            return prepared
        except BaseException:
            self.abort(prepared)
            raise

    def validate(self, prepared: PreparedProject) -> None:
        required_directories = (
            "raw",
            "wiki/current",
            "wiki/drafts",
            "wiki/drafts/local-ingest",
            "wiki/versions",
            "wiki/topics",
            "wiki/sources",
            "schema",
            "exports",
            ".incubator",
            ".incubator/transactions",
            ".incubator/locks",
        )
        required_files = (
            "README.md",
            "AGENTS.md",
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
        return prepared.paths

    def abort(self, prepared: PreparedProject) -> None:
        temp_root = prepared.temp_root.resolve()
        if (
            temp_root.parent == prepared.paths.project_root.parent
            and self._is_prepared_temp_name(temp_root.name, prepared.project_id)
            and temp_root.exists()
        ):
            shutil.rmtree(temp_root)

    def _validate_schema_source(self) -> None:
        required_assets = (*ROOT_TEMPLATE_MAP, *SCHEMA_FILENAMES)
        missing = [
            filename
            for filename in required_assets
            if not (self.schema_source / filename).is_file()
        ]
        if missing:
            raise FileNotFoundError(f"incubator schema assets missing: {', '.join(missing)}")

    def _build_tree(self, temp_root: Path, command: CreateProjectInput) -> None:
        for relative_path in (
            "raw",
            "wiki/current",
            "wiki/drafts/local-ingest",
            "wiki/versions",
            "wiki/topics",
            "wiki/sources",
            "schema",
            "exports",
            ".incubator/transactions",
            ".incubator/locks",
        ):
            (temp_root / relative_path).mkdir(parents=True, exist_ok=False)

        for source_filename, destination_filename in ROOT_TEMPLATE_MAP.items():
            shutil.copyfile(
                self.schema_source / source_filename,
                temp_root / destination_filename,
            )

        for filename in SCHEMA_FILENAMES:
            shutil.copyfile(
                self.schema_source / filename,
                temp_root / "schema" / filename,
            )

        created_at = self.now().isoformat()
        (temp_root / "wiki/index.md").write_text(
            f"# {command.name}\n\n当前项目尚未发布生效产品文档。\n",
            encoding="utf-8",
        )
        (temp_root / "wiki/log.md").write_text(
            f"# 项目日志\n\n- {created_at} 创建产品文档孵化项目。\n",
            encoding="utf-8",
        )
        self._write_json(
            temp_root / ".incubator/project.json",
            {
                "schema_version": "2.2",
                "product_name": "产品文档孵化器",
                "project_id": command.project_id,
                "name": command.name,
                "description": command.description,
                "initial_display_version": command.initial_display_version,
                "allow_external_model": command.allow_external_model,
                "created_at": created_at,
                "wiki_initialized": True,
                "wiki_schema_version": "2.2",
                "root_readme_path": "README.md",
                "root_agent_rules_path": "AGENTS.md",
                "ingest_contract_path": "schema/ingest-contract.md",
            },
        )
        self._write_json(
            temp_root / ".incubator/source-index.json",
            {
                "schema_version": "2.2",
                "project_id": command.project_id,
                "sources": [],
            },
        )

    @staticmethod
    def _is_prepared_temp_name(name: str, project_id: str) -> bool:
        prefix = f".{project_id}.tmp-"
        if not name.startswith(prefix):
            return False
        token = name.removeprefix(prefix)
        try:
            return UUID(token).hex == token
        except ValueError:
            return False

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
