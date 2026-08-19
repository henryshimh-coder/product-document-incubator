from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

from src.application.container import build_container
from src.domain.wiki import WikiChangeSet, WikiIngestRun, WikiPageChange
from src.infrastructure.db.repositories import (
    SqliteSourceRepository,
    SqliteWikiIngestRunRepository,
)
from src.infrastructure.files.wiki_change_set_store import (
    FILES_COMMITTED,
    WikiChangeSetStore,
    WikiTransactionCoordinator,
)
from src.infrastructure.files.wiki_validator import WikiValidator
from tests.e2e.test_incubator_full_success import NOW, IncubatorHarness


def _sha256(payload: bytes | str) -> str:
    encoded = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(encoded).hexdigest()


def test_active_project_is_restored_after_container_restart(tmp_path) -> None:
    harness = IncubatorHarness(tmp_path)
    paths = harness.create_project("PROJECT_A", "产品 A")
    harness.manager.switch(paths.project_id)

    container = build_container(environ={"INCUBATOR_LIBRARY_ROOT": str(harness.library_root)})

    assert container.active_project is not None
    assert container.active_project.project_id == "PROJECT_A"
    assert container.archive_raw_source is not None
    assert container.export_current_document is not None
    container.close()


def test_project_restart_automatically_fails_orphaned_ingest_run(tmp_path) -> None:
    """Catches restart leaving a stale, lockless ingest permanently in progress."""
    harness = IncubatorHarness(tmp_path)
    paths = harness.create_project("PROJECT_A", "产品 A")
    archived = harness.archive(paths, "material.md", b"restart recovery material")
    sources = SqliteSourceRepository(harness.db_path)
    source = sources.get(archived.source_id).model_copy(update={"ingest_status": "ingesting"})
    sources.update(source)
    runs = SqliteWikiIngestRunRepository(harness.db_path)
    runs.add(
        WikiIngestRun(
            id="RUN-ORPHAN",
            project_id=paths.project_id,
            source_id=source.id,
            transaction_id="TXN-ORPHAN",
            idempotency_key="f" * 64,
            schema_version="2.2",
            generation_mode="external_ai",
            status="ingesting",
            started_at=source.created_at - timedelta(hours=1),
        )
    )
    harness.manager.switch(paths.project_id)

    container = build_container(
        environ={"INCUBATOR_LIBRARY_ROOT": str(harness.library_root)}
    )
    container.close()

    recovered_source = sources.get(source.id)
    recovered_run = runs.get_by_transaction("TXN-ORPHAN")
    assert recovered_source.ingest_status == "ingest_failed"
    assert recovered_source.ingest_error_code == "WIKI_INGEST_INTERRUPTED"
    assert recovered_run is not None
    assert recovered_run.status == "ingest_failed"
    assert recovered_run.error_code == "WIKI_INGEST_INTERRUPTED"


def test_project_restart_automatically_rolls_back_interrupted_file_commit(tmp_path) -> None:
    """Catches restart failing to restore files from a durable files_committed journal."""
    harness = IncubatorHarness(tmp_path)
    paths = harness.create_project("PROJECT_A", "产品 A")
    archived = harness.archive(paths, "material.md", b"interrupted commit material")
    sources = SqliteSourceRepository(harness.db_path)
    source = sources.get(archived.source_id).model_copy(update={"ingest_status": "ingesting"})
    sources.update(source)
    assert source.material_series_id is not None
    source_page_path = f"wiki/sources/{source.id}-material.md"
    source_markdown = f"""---
project_id: PROJECT_A
source_id: {source.id}
material_series_id: {source.material_series_id}
material_version: '{source.document_version}'
raw_path: {source.archive_path}
raw_sha256: {source.sha256}
source_type: {source.source_type}
authority_level: {source.authority_level.value}
security_level: {source.security_level.value}
schema_version: '2.2'
generation_mode: external_ai
ingested_at: '2026-08-12T12:00:00Z'
---
# Material
""".strip()
    contents = {
        source_page_path: source_markdown,
        "wiki/index.md": f"# Wiki\n\n- [[{source_page_path.removesuffix('.md')}]]",
        "wiki/log.md": f"# Log\n\n- {'e' * 64} {source.id} ingested",
        ".incubator/source-index.json": (
            f'{{"schema_version":"2.2","sources":[{{"id":"{source.id}"}}]}}'
        ),
    }
    changes: list[WikiPageChange] = []
    before_hashes: dict[str, str] = {}
    for relative_path, markdown in contents.items():
        target = paths.project_root / relative_path
        if target.is_file():
            before_hashes[relative_path] = _sha256(target.read_bytes())
        changes.append(
            WikiPageChange(
                relative_path=relative_path,
                operation="replace" if target.is_file() else "create",
                before_sha256=_sha256(target.read_bytes()) if target.is_file() else None,
                markdown=markdown,
                after_sha256=_sha256(markdown),
            )
        )
    change_set = WikiChangeSet(
        transaction_id="TXN-CRASHED",
        project_id=paths.project_id,
        source_id=source.id,
        raw_path=Path(source.archive_path).relative_to(paths.project_root).as_posix(),
        raw_sha256=source.sha256,
        raw_size_bytes=source.size_bytes,
        idempotency_key="e" * 64,
        schema_version="2.2",
        generation_mode="external_ai",
        page_changes=changes,
        source_page_path=source_page_path,
        topic_page_paths=[],
        conflict_count=0,
        evidence_gap_count=0,
        result_digest="f" * 64,
    )
    runs = SqliteWikiIngestRunRepository(harness.db_path)
    runs.add(
        WikiIngestRun(
            id="RUN-CRASHED",
            project_id=paths.project_id,
            source_id=source.id,
            transaction_id=change_set.transaction_id,
            idempotency_key=change_set.idempotency_key,
            schema_version="2.2",
            generation_mode="external_ai",
            status="ingesting",
            started_at=NOW,
        )
    )
    validator = WikiValidator(paths, source)
    crashed = WikiTransactionCoordinator(
        paths=paths,
        db_path=harness.db_path,
        validator=validator,
        clock=lambda: NOW,
    )
    crashed._persist_recovery_binding(change_set)
    store = WikiChangeSetStore(paths, change_set.transaction_id)
    store.prepare(change_set, crashed.wiki, NOW)
    for change in change_set.page_changes:
        crashed.wiki.commit_staged(change, store.staged_root)
    store.set_state(FILES_COMMITTED, NOW)

    harness.manager.switch(paths.project_id)

    container = build_container(
        environ={"INCUBATOR_LIBRARY_ROOT": str(harness.library_root)}
    )
    container.close()

    assert not (paths.project_root / source_page_path).exists()
    for relative_path, expected_hash in before_hashes.items():
        assert _sha256((paths.project_root / relative_path).read_bytes()) == expected_hash
    assert sources.get(source.id).ingest_status == "ingest_failed"
    recovered_run = runs.get_by_transaction(change_set.transaction_id)
    assert recovered_run is not None
    assert recovered_run.status == "ingest_failed"
    assert recovered_run.error_code == "WIKI_INGEST_INTERRUPTED"
