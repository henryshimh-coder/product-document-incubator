from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.enums import AuthorityLevel, KnowledgeStatus
from src.domain.errors import DomainError
from src.domain.models import KnowledgeCard
from src.infrastructure.files.baseline_card_reader import LocalBaselineCardReader
from src.infrastructure.files.markdown_store import MarkdownStore

NOW = datetime(2026, 8, 3, 7, 0, tzinfo=UTC)
VERSION = "LLD-724_1"
CANONICAL_PATH = f"data/obsidian_vault/02_Current_Baseline/{VERSION}/cards.json"


def _card(
    card_id: str = "RULE-001",
    *,
    version: str = VERSION,
    project_id: str = "LLD",
) -> KnowledgeCard:
    return KnowledgeCard(
        id=card_id,
        project_id=project_id,
        card_type="rule",
        title="目标客群",
        content="当前目标客群是符合准入要求的存量客户。",
        status=KnowledgeStatus.EFFECTIVE,
        product_version=version,
        applicable_scope="演示",
        source_refs=["SRC-BASE"],
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        owner="产品",
        created_at=NOW,
        updated_at=NOW,
    )


def _snapshot(
    tmp_path: Path,
    cards: list[KnowledgeCard],
) -> tuple[LocalBaselineCardReader, str, str]:
    store = MarkdownStore(tmp_path)
    _, cards_path = store.write_baseline(VERSION, "# 基线\n", cards)
    return LocalBaselineCardReader(tmp_path), cards_path, store.sha256_for(cards_path)


def test_reader_returns_cards_from_canonical_snapshot(tmp_path: Path) -> None:
    """Catches the reader rejecting the one canonical snapshot location."""
    reader, relative_path, sha256 = _snapshot(tmp_path, [_card()])

    cards = reader.read_version_cards(
        project_id="LLD",
        version=VERSION,
        relative_path=relative_path,
        expected_sha256=sha256,
    )

    assert [card.id for card in cards] == ["RULE-001"]


@pytest.mark.parametrize(
    "relative_path",
    [
        "data/baselines/LLD-724_1/cards.json",
        "data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md",
        "data/obsidian_vault/02_Current_Baseline/LLD-724_1/nested/cards.json",
        "../02_Current_Baseline/LLD-724_1/cards.json",
        "data/obsidian_vault/02_Current_Baseline/LLD-724_2/cards.json",
    ],
    ids=[
        "non-canonical-dir",
        "wrong-filename",
        "nested-parent",
        "traversal",
        "other-version-dir",
    ],
)
def test_reader_rejects_non_canonical_paths(tmp_path: Path, relative_path: str) -> None:
    """Catches snapshot paths outside 02_Current_Baseline/{version}/cards.json."""
    reader, _, sha256 = _snapshot(tmp_path, [_card()])

    with pytest.raises(DomainError, match="CARD_SNAPSHOT_PATH_UNSAFE"):
        reader.read_version_cards(
            project_id="LLD",
            version=VERSION,
            relative_path=relative_path,
            expected_sha256=sha256,
        )


def test_reader_rejects_version_with_path_traversal(tmp_path: Path) -> None:
    """Catches a version string escaping its directory."""
    reader, relative_path, sha256 = _snapshot(tmp_path, [_card()])

    with pytest.raises(DomainError, match="CARD_SNAPSHOT_PATH_UNSAFE"):
        reader.read_version_cards(
            project_id="LLD",
            version=f"../{VERSION}",
            relative_path=relative_path,
            expected_sha256=sha256,
        )


def test_reader_rejects_unreadable_snapshot(tmp_path: Path) -> None:
    """Catches a missing snapshot being treated as an empty card set."""
    reader = LocalBaselineCardReader(tmp_path)

    with pytest.raises(DomainError, match="CARD_SNAPSHOT_UNREADABLE"):
        reader.read_version_cards(
            project_id="LLD",
            version=VERSION,
            relative_path=CANONICAL_PATH,
            expected_sha256="0" * 64,
        )


def test_reader_rejects_hash_mismatch(tmp_path: Path) -> None:
    """Catches a tampered snapshot passing with the recorded hash."""
    reader, relative_path, _ = _snapshot(tmp_path, [_card()])

    with pytest.raises(DomainError, match="CARD_SNAPSHOT_HASH_MISMATCH"):
        reader.read_version_cards(
            project_id="LLD",
            version=VERSION,
            relative_path=relative_path,
            expected_sha256="0" * 64,
        )


def test_reader_rejects_invalid_structure(tmp_path: Path) -> None:
    """Catches structurally invalid JSON passing as a card snapshot."""
    reader, relative_path, _ = _snapshot(tmp_path, [_card()])
    target = tmp_path / relative_path
    target.write_text('{"not": "a list"}', encoding="utf-8")
    sha256 = hashlib.sha256(target.read_bytes()).hexdigest()

    with pytest.raises(DomainError, match="CARD_SNAPSHOT_INVALID"):
        reader.read_version_cards(
            project_id="LLD",
            version=VERSION,
            relative_path=relative_path,
            expected_sha256=sha256,
        )


def test_reader_rejects_duplicate_card_ids(tmp_path: Path) -> None:
    """Catches duplicate card ids making citations ambiguous."""
    reader, relative_path, sha256 = _snapshot(tmp_path, [_card("RULE-001"), _card("RULE-001")])

    with pytest.raises(DomainError, match="CARD_SNAPSHOT_DUPLICATE_ID"):
        reader.read_version_cards(
            project_id="LLD",
            version=VERSION,
            relative_path=relative_path,
            expected_sha256=sha256,
        )


def test_reader_rejects_cards_from_other_versions_or_projects(tmp_path: Path) -> None:
    """Catches a mixed snapshot leaking another version's cards into a query."""
    cards = [_card("RULE-001"), _card("RULE-002", version="LLD-999_9"), _card("RULE-003")]
    reader, relative_path, sha256 = _snapshot(tmp_path, cards)

    with pytest.raises(DomainError, match="CARD_SNAPSHOT_VERSION_MISMATCH:RULE-002"):
        reader.read_version_cards(
            project_id="LLD",
            version=VERSION,
            relative_path=relative_path,
            expected_sha256=sha256,
        )
