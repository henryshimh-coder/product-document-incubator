from __future__ import annotations

from typing import Protocol

from src.domain.services.deterministic_lint import DeterministicRuleFacts


class LintFactReader(Protocol):
    def for_card(
        self,
        *,
        project_id: str,
        baseline_version: str,
        card_id: str,
        source_ids: tuple[str, ...],
    ) -> DeterministicRuleFacts: ...
