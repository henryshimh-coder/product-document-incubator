from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import ValidationError

from src.domain.models import BaselineManifest


def fsync_file(path: Path) -> None:
    with path.open("rb") as file:
        os.fsync(file.fileno())


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ManifestStore:
    """Reads and atomically replaces the authoritative current-baseline manifest."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read_and_validate(self) -> BaselineManifest:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return BaselineManifest.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise ValueError(f"Invalid baseline manifest at {self.path}: {error}") from error

    def atomic_replace(self, manifest: BaselineManifest) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        payload = (
            json.dumps(
                manifest.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2
            )
            + "\n"
        )
        try:
            temp_path.write_text(payload, encoding="utf-8")
            fsync_file(temp_path)
            os.replace(temp_path, self.path)
            fsync_directory(self.path.parent)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
