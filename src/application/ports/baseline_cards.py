from __future__ import annotations

from typing import Protocol

from src.domain.models import KnowledgeCard


class BaselineCardReader(Protocol):
    """Read the authoritative per-version card snapshot with integrity checks."""

    def read_version_cards(
        self,
        *,
        project_id: str,
        version: str,
        relative_path: str,
        expected_sha256: str,
    ) -> list[KnowledgeCard]: ...
