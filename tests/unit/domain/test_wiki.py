from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.domain.enums import DocumentGenerationMode
from src.domain.wiki import WikiChangeSet, WikiPageChange

SHA_A = "a" * 64
NOW = datetime(2026, 8, 17, tzinfo=UTC)


@pytest.fixture
def change_set_factory():
    def factory(
        *,
        paths: list[str] | None = None,
        markdown: str = "# Wiki page\n",
        operation: str = "create",
        before_sha256: str | None = None,
        **overrides: object,
    ) -> WikiChangeSet:
        page_paths = paths or [
            "wiki/sources/SRC-PROJECT-A-material.md",
            "wiki/index.md",
            "wiki/log.md",
            ".incubator/source-index.json",
        ]
        source_page_path = next(
            (path for path in page_paths if path.startswith("wiki/sources/")),
            "wiki/sources/SRC-PROJECT-A-material.md",
        )
        topic_page_paths = [path for path in page_paths if path.startswith("wiki/topics/")]
        payload: dict[str, object] = {
            "transaction_id": "TXN-PROJECT-A-001",
            "project_id": "PROJECT-A",
            "source_id": "SRC-PROJECT-A-001",
            "idempotency_key": SHA_A,
            "schema_version": "2.2",
            "generation_mode": DocumentGenerationMode.EXTERNAL_AI,
            "page_changes": [
                WikiPageChange(
                    relative_path=path,
                    operation=operation,
                    before_sha256=before_sha256,
                    markdown=markdown,
                    after_sha256=SHA_A,
                )
                for path in page_paths
            ],
            "source_page_path": source_page_path,
            "topic_page_paths": topic_page_paths,
            "conflict_count": 0,
            "evidence_gap_count": 0,
            "result_digest": SHA_A,
        }
        payload.update(overrides)
        return WikiChangeSet(**payload)

    return factory


def test_change_set_requires_one_source_page_index_log_and_safe_targets(change_set_factory):
    """Catches commits that omit a required governed Wiki artifact."""
    change_set = change_set_factory(
        paths=[
            "wiki/sources/SRC-A-material.md",
            "wiki/index.md",
            "wiki/log.md",
            ".incubator/source-index.json",
        ]
    )

    change_set.validate_contract()


@pytest.mark.parametrize(
    "forbidden",
    [
        "raw/2026/SRC-A/a.md",
        "wiki/current/当前产品方案.md",
        "wiki/versions/1.0.md",
        ".incubator/current-baseline.json",
        "../escape.md",
    ],
)
def test_change_set_rejects_forbidden_target(change_set_factory, forbidden):
    """Catches a change set escaping the sole writable Wiki target allowlist."""
    with pytest.raises(ValueError, match="WIKI_CHANGESET_TARGET_FORBIDDEN"):
        change_set_factory(paths=[forbidden]).validate_contract()


def test_change_set_rejects_missing_source_page(change_set_factory):
    """Catches transactions that could commit index/log without one source page."""
    with pytest.raises(ValueError, match="WIKI_CHANGESET_SOURCE_PAGE_REQUIRED"):
        change_set_factory(
            paths=["wiki/index.md", "wiki/log.md", ".incubator/source-index.json"]
        ).validate_contract()


def test_replace_requires_before_digest(change_set_factory):
    """Catches replace operations that cannot detect an Owner's concurrent edit."""
    with pytest.raises(ValueError, match="WIKI_CHANGESET_BEFORE_SHA_REQUIRED"):
        change_set_factory(
            operation="replace",
            before_sha256=None,
        ).validate_contract()


def test_create_rejects_before_digest(change_set_factory):
    """Catches create operations that falsely claim an existing target precondition."""
    with pytest.raises(ValueError, match="WIKI_CHANGESET_CREATE_BEFORE_SHA_FORBIDDEN"):
        change_set_factory(before_sha256=SHA_A).validate_contract()
