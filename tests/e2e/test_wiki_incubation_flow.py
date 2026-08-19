from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.application.dto.projects import CreateProjectInput
from src.domain.errors import DomainError
from src.domain.wiki import WikiIngestStatus
from tests.e2e.harness import WikiIncubatorHarness


@pytest.fixture
def wiki_harness(tmp_path):
    return WikiIncubatorHarness(tmp_path)


def test_l2_archive_ingest_wiki_incubate_publish(wiki_harness, tmp_path):
    parent = tmp_path / "one"
    parent.mkdir()
    project = wiki_harness.create_project("PROJECT_A", parent)
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
    parent = tmp_path / "one"
    parent.mkdir()
    project = wiki_harness.create_project("PROJECT_A", parent)
    source = wiki_harness.archive_l4(project, "strategy.md", b"# Restricted")
    draft_root = wiki_harness.prepare_local(project, source.source_id)

    wiki_harness.write_valid_local_source_page(draft_root, source.source_id)
    result = wiki_harness.confirm_local(project, source.source_id)

    assert result.status is WikiIngestStatus.INGESTED
    assert wiki_harness.gateway_calls == 0


def test_two_projects_in_different_roots_complete_isolated_lifecycles(wiki_harness, tmp_path):
    parent_a = tmp_path / "one"
    parent_b = tmp_path / "two"
    parent_a.mkdir()
    parent_b.mkdir()
    project_a = wiki_harness.create_project("PROJECT_A", parent_a)
    project_b = wiki_harness.create_project("PROJECT_B", parent_b)
    project_b_before = wiki_harness.tree_hashes(project_b)
    project_b_records_before = wiki_harness.project_records(project_b.project_id)
    source_a = wiki_harness.archive_l2(project_a, "a.md", b"# A\n" * 10_000)

    wiki_harness.ingest(project_a, source_a.source_id)
    draft_a = wiki_harness.incubate(project_a, [source_a.source_id])
    wiki_harness.publish(project_a, draft_a.id)
    export_a = wiki_harness.export(project_a)

    assert wiki_harness.tree_hashes(project_b) == project_b_before
    assert wiki_harness.project_records(project_b.project_id) == project_b_records_before

    project_a_before_b = wiki_harness.tree_hashes(project_a)
    project_a_records_before_b = wiki_harness.project_records(project_a.project_id)
    source_b = wiki_harness.archive_l2(project_b, "b.md", b"# B\n" * 10_000)
    wiki_harness.ingest(project_b, source_b.source_id)
    draft_b = wiki_harness.incubate(project_b, [source_b.source_id])
    wiki_harness.publish(project_b, draft_b.id)
    export_b = wiki_harness.export(project_b)

    assert export_a.content != export_b.content
    assert wiki_harness.tree_hashes(project_a) == project_a_before_b
    assert wiki_harness.project_records(project_a.project_id) == project_a_records_before_b


def test_move_relocate_then_continue_ingest(wiki_harness, tmp_path):
    parent = tmp_path / "one"
    parent.mkdir()
    project = wiki_harness.create_project("PROJECT_A", parent)
    moved = tmp_path / "two" / "PROJECT_A"
    moved.parent.mkdir()
    project.project_root.rename(moved)

    with pytest.raises(DomainError, match="PROJECT_ROOT_UNAVAILABLE"):
        wiki_harness.open_project(project.project_id)

    project = wiki_harness.relocate(project.project_id, moved)
    source = wiki_harness.archive_l2(project, "after.md", b"# After\n" * 10_000)
    assert wiki_harness.ingest(project, source.source_id).status is WikiIngestStatus.INGESTED


def test_legacy_project_remains_openable_without_automatic_writes(wiki_harness, tmp_path):
    parent = tmp_path / "legacy"
    parent.mkdir()
    legacy = wiki_harness.start_legacy_project("LEGACY_A", parent)
    assert not (legacy.project_root / "README.md").exists()
    assert not (legacy.project_root / "AGENTS.md").exists()
    assert not (legacy.wiki_root / "sources").exists()
    assert not (legacy.wiki_root / "topics").exists()
    before_tree = wiki_harness.tree_hashes(legacy)
    before_content = {
        path: digest
        for path, digest in before_tree.items()
        if not path.startswith(".incubator/locks/")
    }
    before_records = wiki_harness.project_records(legacy.project_id)

    container = wiki_harness.restart_container()
    try:
        assert container.active_project is not None
        assert container.active_project.project_id == legacy.project_id
        assert container.active_project.wiki_schema_version == "2.1"
    finally:
        container.close()

    assert wiki_harness.open_project(legacy.project_id).project_root == legacy.project_root
    after_tree = wiki_harness.tree_hashes(legacy)
    after_content = {
        path: digest
        for path, digest in after_tree.items()
        if not path.startswith(".incubator/locks/")
    }
    assert after_content == before_content
    assert set(after_tree).difference(before_tree) <= {".incubator/locks/wiki-ingest.lock"}
    lock = legacy.system_root / "locks" / "wiki-ingest.lock"
    assert not lock.exists() or lock.stat().st_size == 0
    assert wiki_harness.project_records(legacy.project_id) == before_records


def test_root_readme_navigates_to_ingested_source_and_topic(wiki_harness, tmp_path):
    parent = tmp_path / "one"
    parent.mkdir()
    project = wiki_harness.create_project("PROJECT_A", parent)
    source = wiki_harness.archive_l2(project, "navigation.md", b"# Navigation\n" * 10_000)
    wiki_harness.ingest(project, source.source_id)

    readme = (project.project_root / "README.md").read_text(encoding="utf-8")
    index = (project.wiki_root / "index.md").read_text(encoding="utf-8")

    assert "[Wiki 索引](wiki/index.md)" in readme
    stored = wiki_harness.project_records(project.project_id)["sources"][0]
    assert stored["source_page_path"].removesuffix(".md") in index
    assert stored["topic_page_paths"][0].removesuffix(".md") in index
    assert (project.project_root / stored["source_page_path"]).is_file()
    assert (project.project_root / stored["topic_page_paths"][0]).is_file()


def test_project_creation_rejects_invalid_parent_target_and_project_id(wiki_harness, tmp_path):
    with pytest.raises(DomainError, match="PROJECT_ROOT_NOT_WRITABLE"):
        wiki_harness.create_project("PROJECT_A", tmp_path / "missing-parent")

    existing_parent = tmp_path / "existing"
    (existing_parent / "PROJECT_A").mkdir(parents=True)
    with pytest.raises(ValueError, match="project already exists"):
        wiki_harness.create_project("PROJECT_A", existing_parent)

    with pytest.raises(ValidationError):
        CreateProjectInput(
            project_id="../ESCAPE",
            name="invalid",
            description="invalid project id",
            parent_root=tmp_path,
        )
