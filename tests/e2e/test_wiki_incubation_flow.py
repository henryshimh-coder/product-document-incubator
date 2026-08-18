from __future__ import annotations

import pytest

from src.domain.errors import DomainError
from src.domain.wiki import WikiIngestStatus
from tests.e2e.harness import WikiIncubatorHarness


@pytest.fixture
def wiki_harness(tmp_path):
    return WikiIncubatorHarness(tmp_path)


def test_l2_archive_ingest_wiki_incubate_publish(wiki_harness, tmp_path):
    project = wiki_harness.create_project("PROJECT_A", tmp_path / "one")
    source = wiki_harness.archive_l2(project, "requirements.md", b"# Requirements\nSafe\n" * 1000)
    raw_before = wiki_harness.sha256(source.archive_path)

    ingest = wiki_harness.ingest(project, source.source_id)

    assert ingest.status is WikiIngestStatus.INGESTED
    draft = wiki_harness.incubate(project, [source.source_id])
    assert source.source_id in draft.source_ids
    assert wiki_harness.current_markdown(project) is None
    wiki_harness.publish(project, draft.id)
    assert wiki_harness.current_markdown(project)
    assert wiki_harness.sha256(source.archive_path) == raw_before


def test_l4_archive_local_edit_confirm_without_gateway(wiki_harness, tmp_path):
    project = wiki_harness.create_project("PROJECT_A", tmp_path / "one")
    source = wiki_harness.archive_l4(project, "strategy.md", b"# Restricted")
    draft_root = wiki_harness.prepare_local(project, source.source_id)

    wiki_harness.write_valid_local_source_page(draft_root, source.source_id)
    result = wiki_harness.confirm_local(project, source.source_id)

    assert result.status is WikiIngestStatus.INGESTED
    assert wiki_harness.gateway_calls == 0


def test_two_projects_in_different_roots_never_cross_read(wiki_harness, tmp_path):
    project_a = wiki_harness.create_project("PROJECT_A", tmp_path / "one")
    project_b = wiki_harness.create_project("PROJECT_B", tmp_path / "two")
    source_a = wiki_harness.archive_l2(project_a, "a.md", b"# A\n" * 10_000)
    project_b_before = wiki_harness.tree_hashes(project_b)

    wiki_harness.ingest(project_a, source_a.source_id)

    assert wiki_harness.tree_hashes(project_b) == project_b_before


def test_move_relocate_then_continue_ingest(wiki_harness, tmp_path):
    project = wiki_harness.create_project("PROJECT_A", tmp_path / "one")
    moved = tmp_path / "two" / "PROJECT_A"
    moved.parent.mkdir()
    project.project_root.rename(moved)

    with pytest.raises(DomainError, match="PROJECT_ROOT_UNAVAILABLE"):
        wiki_harness.open_project(project.project_id)

    project = wiki_harness.relocate(project.project_id, moved)
    source = wiki_harness.archive_l2(project, "after.md", b"# After\n" * 10_000)
    assert wiki_harness.ingest(project, source.source_id).status is WikiIngestStatus.INGESTED
