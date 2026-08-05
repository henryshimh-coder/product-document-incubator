from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.domain.errors import DomainError, ErrorCode
from src.domain.models import KnowledgeCard

CARD_SNAPSHOT_DIR = Path("data/obsidian_vault/02_Current_Baseline")
CARD_SNAPSHOT_FILENAME = "cards.json"


def parse_card_snapshot(
    raw_bytes: bytes,
    *,
    project_id: str,
    version: str,
) -> list[KnowledgeCard]:
    """Parse and validate card snapshot bytes exactly once.

    Shared by the query-time reader and reconciliation so the same bytes
    drive hashing and structural validation; callers never re-read the file.
    """
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(payload, list):
            raise ValueError("card snapshot payload is not a list")
        cards = [KnowledgeCard.model_validate(item) for item in payload]
    except (TypeError, ValueError) as error:
        raise DomainError(
            ErrorCode.BASELINE_INTEGRITY_FAILED,
            "CARD_SNAPSHOT_INVALID",
        ) from error
    card_ids = [card.id for card in cards]
    if len(card_ids) != len(set(card_ids)):
        raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED, "CARD_SNAPSHOT_DUPLICATE_ID")
    mismatched = sorted(
        card.id
        for card in cards
        if card.project_id != project_id or card.product_version != version
    )
    if mismatched:
        raise DomainError(
            ErrorCode.BASELINE_INTEGRITY_FAILED,
            f"CARD_SNAPSHOT_VERSION_MISMATCH:{','.join(mismatched)}",
        )
    return cards


class LocalBaselineCardReader:
    """Read baseline card snapshots from the canonical release tree.

    The path must be exactly
    ``data/obsidian_vault/02_Current_Baseline/{version}/cards.json``; the
    manifest/baseline row supplies the expected sha256. Any path escape,
    unreadable file, hash mismatch, structural problem, duplicate card id or
    version mismatch fails closed with BASELINE_INTEGRITY_FAILED.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def read_version_cards(
        self,
        *,
        project_id: str,
        version: str,
        relative_path: str,
        expected_sha256: str,
    ) -> list[KnowledgeCard]:
        expected = CARD_SNAPSHOT_DIR / version / CARD_SNAPSHOT_FILENAME
        candidate = Path(relative_path)
        if (
            not project_id.strip()
            or not version.strip()
            or Path(version).name != version
            or candidate != expected
        ):
            raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED, "CARD_SNAPSHOT_PATH_UNSAFE")
        asset_path = (self.project_root / candidate).resolve()
        if not asset_path.is_relative_to(self.project_root):
            raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED, "CARD_SNAPSHOT_PATH_UNSAFE")
        try:
            raw_bytes = asset_path.read_bytes()
        except OSError as error:
            raise DomainError(
                ErrorCode.BASELINE_INTEGRITY_FAILED,
                "CARD_SNAPSHOT_UNREADABLE",
            ) from error
        if hashlib.sha256(raw_bytes).hexdigest() != expected_sha256:
            raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED, "CARD_SNAPSHOT_HASH_MISMATCH")
        return parse_card_snapshot(raw_bytes, project_id=project_id, version=version)
