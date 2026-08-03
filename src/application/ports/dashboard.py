from __future__ import annotations

from typing import Protocol

from src.domain.models import BaselineManifest


class ManifestReader(Protocol):
    def read_and_validate(self) -> BaselineManifest: ...


class ManifestIntegrity(Protocol):
    def validate(self, manifest: BaselineManifest) -> bool: ...
