from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from src.domain.enums import AuthorityLevel, DocumentGenerationMode, SecurityLevel
from src.domain.errors import DomainError
from src.domain.models import SourceRecord
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
                relative_path="wiki/sources/SRC-PROJECT-A-001-material.md",
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
            "source_page_path": "wiki/sources/SRC-PROJECT-A-001-material.md",
            "topic_page_paths": [],
            "conflict_count": 0,
            "evidence_gap_count": 0,
            "result_digest": SHA_A,
        }
        payload.update(overrides)
        return WikiChangeSet(**payload)

    return factory


@pytest.fixture
def source() -> SourceRecord:
    return SourceRecord(
        id="SRC-PROJECT-A-001",
        project_id="PROJECT-A",
        original_filename="material.md",
        archive_path="raw/2026/SRC-PROJECT-A-001.md",
        sha256=SHA_A,
        mime_type="text/markdown",
        size_bytes=10,
        source_type="product_document",
        authority_level=AuthorityLevel.FORMAL_DECISION,
        source_department="产品",
        provider=None,
        document_date=date(2026, 8, 17),
        document_version="v1",
        applicable_baseline_version="PROJECT-A-1.0",
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted=True,
        allow_external_model=True,
        is_sandbox=False,
        ingest_status="pending_ingest",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        material_name="material",
    )


@pytest.fixture
def validator_factory(paths, source):
    def factory(
        *,
        existing_topic_paths: list[str] | None = None,
        new_topic_count: int = 0,
    ):
        return WikiValidator(
            paths,
            source,
            existing_topic_paths=existing_topic_paths or [],
            new_topic_count=new_topic_count,
        )

    return factory


def test_validator_accepts_governed_change_set(paths, change_set_factory, validator_factory):
    """Catches rejecting a complete change set that can safely stay within this project."""
    validator_factory().validate_change_set(change_set_factory())


def test_validator_rejects_cross_project_source_id(paths, change_set_factory, validator_factory):
    """Catches source-page metadata that could attribute another project's source here."""
    change = change_set_factory(markdown="source_id: SRC-PROJECT-B-001")

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        validator_factory().validate_change_set(change)


def test_validator_rejects_broken_obsidian_link(paths, change_set_factory, validator_factory):
    """Catches a Wiki page linking to a topic absent from the transaction and project."""
    change = change_set_factory(markdown="[[wiki/topics/missing]]")

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        validator_factory().validate_change_set(change)


def test_validator_rejects_source_frontmatter_without_raw_integrity(
    paths, change_set_factory, validator_factory
):
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
        validator_factory().validate_change_set(change)


def test_validator_rejects_blank_required_source_frontmatter_value(
    paths, change_set_factory, validator_factory
):
    """Catches a source page whose nominal metadata cannot identify its material version."""
    markdown = change_set_factory().page_changes[0].markdown.replace(
        "material_version: v1", "material_version: ''"
    )

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        validator_factory().validate_change_set(change_set_factory(markdown=markdown))


@pytest.mark.parametrize(
    "unapproved_path",
    [
        "wiki/sources/SRC-OTHER-material.md",
        "wiki/topics/unapproved-topic.md",
    ],
)
def test_validator_rejects_allowlisted_but_unapproved_target(
    paths, change_set_factory, validator_factory, unapproved_path
):
    """Catches model output selecting a governed-looking page outside the local target plan."""
    change_set = change_set_factory()
    page_changes = list(change_set.page_changes)
    if unapproved_path.startswith("wiki/sources/"):
        page_changes[0] = page_changes[0].model_copy(update={"relative_path": unapproved_path})
        change_set = change_set.model_copy(
            update={"page_changes": page_changes, "source_page_path": unapproved_path}
        )
    else:
        page_changes.append(
            WikiPageChange(
                relative_path=unapproved_path,
                operation="create",
                before_sha256=None,
                markdown="# Unapproved topic\n",
                after_sha256=SHA_A,
            )
        )
        change_set = change_set.model_copy(
            update={"page_changes": page_changes, "topic_page_paths": [unapproved_path]}
        )

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        validator_factory().validate_change_set(change_set)


@pytest.mark.parametrize(
    "unapproved_path",
    [
        "wiki/sources/SRC-OTHER-material.md",
        "wiki/topics/unapproved-topic.md",
    ],
)
def test_validator_rejects_model_path_without_caller_target_plan(
    paths, source, change_set_factory, unapproved_path
):
    """Catches a change set asserting an allowlisted path the validator did not derive."""
    change_set = change_set_factory()
    page_changes = list(change_set.page_changes)
    new_topic_count = 0
    if unapproved_path.startswith("wiki/sources/"):
        page_changes[0] = page_changes[0].model_copy(update={"relative_path": unapproved_path})
        change_set = change_set.model_copy(
            update={"page_changes": page_changes, "source_page_path": unapproved_path}
        )
    else:
        new_topic_count = 1
        page_changes.append(
            WikiPageChange(
                relative_path=unapproved_path,
                operation="create",
                before_sha256=None,
                markdown="# Unapproved topic\n",
                after_sha256=SHA_A,
            )
        )
        change_set = change_set.model_copy(
            update={"page_changes": page_changes, "topic_page_paths": [unapproved_path]}
        )

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        WikiValidator(
            paths, source, new_topic_count=new_topic_count
        ).validate_change_set(change_set)


def test_validator_accepts_link_to_changed_topic(paths, change_set_factory, validator_factory):
    """Catches rejecting a source page link whose target is safely included in this commit."""
    topic = WikiPageChange(
        relative_path="wiki/topics/SRC-PROJECT-A-001-topic-1.md",
        operation="create",
        before_sha256=None,
        markdown="# Pricing\n",
        after_sha256=SHA_A,
    )
    source_markdown = change_set_factory().page_changes[0].markdown + (
        "\n[[wiki/topics/SRC-PROJECT-A-001-topic-1]]\n"
    )
    change = change_set_factory(
        markdown=source_markdown,
        page_changes=[*change_set_factory().page_changes, topic],
        topic_page_paths=["wiki/topics/SRC-PROJECT-A-001-topic-1.md"],
    )

    validator_factory(new_topic_count=1).validate_change_set(change)


def test_validator_rejects_missing_existing_topic(paths, source):
    """Catches model output claiming an existing topic that is absent from this project."""
    with pytest.raises(ValueError, match="WIKI_TARGET_TOPIC_UNAUTHORIZED"):
        WikiValidator(
            paths,
            source,
            existing_topic_paths=["wiki/topics/unapproved-topic.md"],
            new_topic_count=0,
        )


def test_validator_rejects_existing_topic_symlink_outside_topics(paths, source):
    """Catches a topic alias resolving to a non-topic Wiki page."""
    topics_root = paths.wiki_root / "topics"
    topics_root.mkdir()
    (paths.wiki_root / "index.md").write_text("# Index\n", encoding="utf-8")
    (topics_root / "alias.md").symlink_to("../index.md")

    with pytest.raises(ValueError, match="WIKI_TARGET_TOPIC_UNAUTHORIZED"):
        WikiValidator(
            paths,
            source,
            existing_topic_paths=["wiki/topics/alias.md"],
            new_topic_count=0,
        )
