from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class MarkdownStore:
    """Stores readable baseline assets inside the local Obsidian vault."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def write_baseline(
        self, version: str, full_document: str, cards: list[dict[str, Any]]
    ) -> tuple[str, str]:
        relative_dir = Path("data/obsidian_vault/02_Current_Baseline") / version
        target_dir = self.project_root / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        full_path = target_dir / "full.md"
        cards_path = target_dir / "cards.json"
        full_path.write_text(full_document, encoding="utf-8")
        cards_path.write_text(
            json.dumps(cards, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return (str(relative_dir / "full.md"), str(relative_dir / "cards.json"))

    def sha256_for(self, relative_path: str) -> str:
        path = self.project_root / relative_path
        return hashlib.sha256(path.read_bytes()).hexdigest()
