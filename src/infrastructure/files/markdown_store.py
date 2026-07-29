from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.domain.models import KnowledgeCard


class MarkdownStore:
    """Stores readable baseline assets inside the local Obsidian vault."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def write_baseline(
        self, version: str, full_document: str, cards: list[KnowledgeCard]
    ) -> tuple[str, str]:
        validated_cards = [KnowledgeCard.model_validate(card) for card in cards]
        relative_dir = Path("data/obsidian_vault/02_Current_Baseline") / version
        target_dir = self.project_root / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        full_path = target_dir / "full.md"
        cards_path = target_dir / "cards.json"
        full_path.write_text(full_document, encoding="utf-8")
        cards_path.write_text(
            json.dumps(
                [card.model_dump(mode="json") for card in validated_cards],
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return (str(relative_dir / "full.md"), str(relative_dir / "cards.json"))

    def read_cards(self, relative_path: str) -> list[KnowledgeCard]:
        payload = json.loads((self.project_root / relative_path).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Baseline card snapshot must be a JSON array")
        return [KnowledgeCard.model_validate(card) for card in payload]

    def sha256_for(self, relative_path: str) -> str:
        path = self.project_root / relative_path
        return hashlib.sha256(path.read_bytes()).hexdigest()
