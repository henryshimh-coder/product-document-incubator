from __future__ import annotations

from pathlib import Path

import pytest

from src.domain.enums import DocumentGenerationMode
from src.domain.errors import DomainError
from src.domain.wiki import WikiChangeSet, WikiPageChange
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.files.wiki_validator import WikiValidator

SHA_A = "a" * 64


@pytest.fixture
def paths(tmp_path: Path) -> ProjectPaths:
    project_root = tmp_path / "PROJECT-A"
    for directory in ("raw", "wiki", "schema", "exports", ".incubator"):
        (project_root / directory).mkdir(parents=True, exist_ok=True)
    return ProjectPaths.for_registered_root(tmp_path, "PROJECT-A", project_root)


@pytest.fixture
def change_set_factory():
    def factory(*, markdown: str | None = None, **overrides: object) -> WikiChangeSet:
        source_markdown = markdown or """---
project_id: PROJECT-A
source_id: SRC-PROJECT-A-001
material_series_id: SERIES-PROJECT-A
material_version: v1
raw_path: raw/2026/SRC-PROJECT-A-001.md
raw_sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
source_type: product_document
authority_level: formal_decision
security_level: L2
schema_version: '2.2'
generation_mode: external_ai
ingested_at: '2026-08-17T00:00:00+00:00'
---
# Source
"""
        changes = [
            WikiPageChange(
                relative_path="wiki/sources/SRC-PROJECT-A-material.md",
                operation="create",
                before_sha256=None,
                markdown=source_markdown,
                after_sha256=SHA_A,
            ),
            WikiPageChange(
                relative_path="wiki/index.md",
                operation="replace",
                before_sha256=SHA_A,
                markdown="# Index\n",
                after_sha256=SHA_A,
            ),
            WikiPageChange(
                relative_path="wiki/log.md",
                operation="replace",
                before_sha256=SHA_A,
                markdown="# Log\n",
                after_sha256=SHA_A,
            ),
            WikiPageChange(
                relative_path=".incubator/source-index.json",
                operation="replace",
                before_sha256=SHA_A,
                markdown='{"schema_version": "2.2"}',
                after_sha256=SHA_A,
            ),
        ]
        payload: dict[str, object] = {
            "transaction_id": "TXN-PROJECT-A-001",
            "project_id": "PROJECT-A",
            "source_id": "SRC-PROJECT-A-001",
            "idempotency_key": SHA_A,
            "schema_version": "2.2",
            "generation_mode": DocumentGenerationMode.EXTERNAL_AI,
            "page_changes": changes,
            "source_page_path": "wiki/sources/SRC-PROJECT-A-material.md",
            "topic_page_paths": [],
            "conflict_count": 0,
            "evidence_gap_count": 0,
            "result_digest": SHA_A,
        }
        payload.update(overrides)
        return WikiChangeSet(**payload)

    return factory


def test_validator_accepts_governed_change_set(paths, change_set_factory):
    """Catches rejecting a complete change set that can safely stay within this project."""
    WikiValidator(paths).validate_change_set(change_set_factory())


def test_validator_rejects_cross_project_source_id(paths, change_set_factory):
    """Catches source-page metadata that could attribute another project's source here."""
    change = change_set_factory(markdown="source_id: SRC-PROJECT-B-001")

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        WikiValidator(paths).validate_change_set(change)


def test_validator_rejects_broken_obsidian_link(paths, change_set_factory):
    """Catches a Wiki page linking to a topic absent from the transaction and project."""
    change = change_set_factory(markdown="[[wiki/topics/missing]]")

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        WikiValidator(paths).validate_change_set(change)


def test_validator_rejects_source_frontmatter_without_raw_integrity(paths, change_set_factory):
    """Catches source pages that omit the recorded immutable Raw digest."""
    change = change_set_factory(
        markdown="""---
project_id: PROJECT-A
source_id: SRC-PROJECT-A-001
---
# Source
"""
    )

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        WikiValidator(paths).validate_change_set(change)


def test_validator_rejects_blank_required_source_frontmatter_value(paths, change_set_factory):
    """Catches a source page whose nominal metadata cannot identify its material version."""
    markdown = change_set_factory().page_changes[0].markdown.replace(
        "material_version: v1", "material_version: ''"
    )

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        WikiValidator(paths).validate_change_set(change_set_factory(markdown=markdown))


def test_validator_accepts_link_to_changed_topic(paths, change_set_factory):
    """Catches rejecting a source page link whose target is safely included in this commit."""
    topic = WikiPageChange(
        relative_path="wiki/topics/pricing.md",
        operation="create",
        before_sha256=None,
        markdown="# Pricing\n",
        after_sha256=SHA_A,
    )
    source_markdown = change_set_factory().page_changes[0].markdown + "\n[[wiki/topics/pricing]]\n"
    change = change_set_factory(
        markdown=source_markdown,
        page_changes=[*change_set_factory().page_changes, topic],
        topic_page_paths=["wiki/topics/pricing.md"],
    )

    WikiValidator(paths).validate_change_set(change)
