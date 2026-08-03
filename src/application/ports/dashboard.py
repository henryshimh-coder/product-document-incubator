from __future__ import annotations

from typing import NamedTuple, Protocol

from src.domain.models import BaselineManifest


class ManifestSnapshot(NamedTuple):
    manifest: BaselineManifest
    sha256: str


class ManifestReader(Protocol):
    def read_snapshot(self) -> ManifestSnapshot: ...


class ManifestIntegrity(Protocol):
    def validate(self, manifest: BaselineManifest) -> bool: ...
