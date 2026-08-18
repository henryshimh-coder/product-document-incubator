from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from src.domain.models import SourceRecord
from src.domain.wiki import WikiChangeSet, WikiTargetPlan


class WikiChangeSetValidating(Protocol):
    def validate_change_set(self, change_set: WikiChangeSet) -> None: ...


class WikiTargetPlanning(Protocol):
    def build(
        self,
        source: SourceRecord,
        *,
        existing_topic_paths: Sequence[str],
        new_topic_titles: Sequence[str],
    ) -> WikiTargetPlan: ...
