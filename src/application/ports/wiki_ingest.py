from __future__ import annotations

from typing import Protocol

from src.domain.wiki import WikiChangeSet


class WikiChangeSetValidating(Protocol):
    def validate_change_set(self, change_set: WikiChangeSet) -> None: ...
