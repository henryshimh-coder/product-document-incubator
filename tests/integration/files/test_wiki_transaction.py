from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from filelock import FileLock

from src.domain.enums import AuthorityLevel, DocumentGenerationMode, SecurityLevel
from src.domain.errors import DomainError
from src.domain.models import Project, SourceRecord
from src.domain.wiki import WikiChangeSet, WikiIngestRun, WikiPageChange
from src.infrastructure.db.connection import connect
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteProjectRepository,
    SqliteSourceRepository,
    SqliteWikiIngestRunRepository,
)
from src.infrastructure.files.project_audit_log import ProjectAuditLog
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.files.wiki_change_set_store import (
    COMMITTED,
    DATABASE_COMMITTED,
    FILES_COMMITTED,
    PREPARED,
    ROLLED_BACK,
    WikiChangeSetStore,
    WikiTransactionCoordinator,
)
from src.infrastructure.files.wiki_validator import WikiValidator

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


def _sha256(content: bytes | str) -> str:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class _Snapshot:
    wiki_hashes: dict[str, str]
    raw_sha256: str
    protected_hashes: dict[str, str]


class _TransactionFixture:
    def __init__(self, tmp_path: Path) -> None:
        library_root = tmp_path / "library"
        self.paths = ProjectPaths.for_project(library_root, "PROJECT_A")
        self.paths.project_root.mkdir(parents=True)
        self.db_path = tmp_path / "control" / "product_incubator.db"
        migrate(self.db_path)
        self.raw_path = self.paths.raw_root / "SRC-A" / "material.md"
        self.raw_path.parent.mkdir(parents=True)
        self.raw_path.write_bytes(b"Raw\x00bytes\xff")
        self.page("wiki/topics/pricing.md").parent.mkdir(parents=True)
        self.page("wiki/topics/pricing.md").write_text("Old pricing\n", encoding="utf-8")
        self.page("wiki/index.md").write_text("# Wiki\n", encoding="utf-8")
        self.page("wiki/log.md").write_text("# Log\n", encoding="utf-8")
        self.page(".incubator/source-index.json").parent.mkdir(parents=True)
        self.page(".incubator/source-index.json").write_text(
            '{"schema_version":"2.2","sources":[]}\n', encoding="utf-8"
        )
        protected = {
            "wiki/current/current.md": "Published current\n",
            "wiki/versions/v1.md": "Published version\n",
            "wiki/drafts/candidate.md": "Owner candidate\n",
            ".incubator/current-baseline.json": '{"version":"v1"}\n',
            ".incubator/candidate-manifest.json": '{"candidate":"draft"}\n',
        }
        for relative_path, content in protected.items():
            self.page(relative_path).parent.mkdir(parents=True, exist_ok=True)
            self.page(relative_path).write_text(content, encoding="utf-8")
        project = Project(
            id="PROJECT_A",
            name="Project A",
            product_line="Test",
            stage="demo",
            current_baseline_id=None,
            allow_external_model=True,
            created_at=NOW,
            updated_at=NOW,
            project_root_path=str(self.paths.project_root),
        )
        self._source = SourceRecord(
            id="SRC-A",
            project_id="PROJECT_A",
            original_filename="material.md",
            archive_path="raw/SRC-A/material.md",
            sha256=_sha256(self.raw_path.read_bytes()),
            mime_type="text/markdown",
            size_bytes=self.raw_path.stat().st_size,
            source_type="formal_document",
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            source_department="Product",
            provider=None,
            document_date=date(2026, 8, 17),
            document_version="1.0",
            applicable_baseline_version="BASE-1",
            security_level=SecurityLevel.L1_PUBLIC_SIMULATED,
            is_redacted=True,
            allow_external_model=True,
            is_sandbox=False,
            ingest_status="ingesting",
            created_at=NOW,
            material_name="material",
            material_series_id="MAT-A",
            ingest_schema_version="2.2",
            generation_mode=DocumentGenerationMode.EXTERNAL_AI,
        )
        SqliteProjectRepository(self.db_path).add(project)
        SqliteSourceRepository(self.db_path).add(self._source)
        self.change_set = self._change_set()
        self.coordinator = WikiTransactionCoordinator(
            paths=self.paths,
            db_path=self.db_path,
            validator=WikiValidator(
                self.paths,
                self._source,
                existing_topic_paths=["wiki/topics/pricing.md"],
            ),
            clock=lambda: NOW,
        )

    def page(self, relative_path: str) -> Path:
        return self.paths.project_root / relative_path

    def source(self) -> SourceRecord:
        return SqliteSourceRepository(self.db_path).get("SRC-A")

    def raw_sha256(self) -> str:
        return _sha256(self.raw_path.read_bytes())

    def succeeded_run(self):
        return SqliteWikiIngestRunRepository(self.db_path).get_succeeded_by_idempotency(
            self.change_set.idempotency_key
        )

    def fail_at(self, failure_stage: str) -> None:
        def inject(current_stage: str) -> None:
            if current_stage == failure_stage:
                raise RuntimeError(f"INJECTED:{failure_stage}")

        self.coordinator.failure_injector = inject

    def owner_edits(self, relative_path: str, content: str) -> None:
        self.page(relative_path).write_text(content, encoding="utf-8")

    def restarted_coordinator(self) -> WikiTransactionCoordinator:
        return WikiTransactionCoordinator(
            paths=self.paths,
            db_path=self.db_path,
            validator=WikiValidator(
                self.paths,
                self._source,
                existing_topic_paths=["wiki/topics/pricing.md"],
            ),
            clock=lambda: NOW,
        )

    def seed_interrupted(
        self, journal_state: str, *, db_succeeded: bool
    ) -> WikiChangeSetStore:
        self.coordinator._persist_recovery_binding(self.change_set, state="building")
        store = WikiChangeSetStore(self.paths, self.change_set.transaction_id)
        store.prepare(self.change_set, self.coordinator.wiki, NOW)
        self.coordinator._persist_recovery_binding_from_journal(store)
        if journal_state in {FILES_COMMITTED, DATABASE_COMMITTED, COMMITTED}:
            for change in self.change_set.page_changes:
                self.coordinator.wiki.commit_staged(change, store.staged_root)
            self.coordinator._set_transaction_state(store, FILES_COMMITTED)
        if db_succeeded:
            source = self.source().model_copy(
                update={
                    "ingest_status": "ingested",
                    "ingested_at": NOW,
                    "source_page_path": self.change_set.source_page_path,
                    "topic_page_paths": self.change_set.topic_page_paths,
                    "ingest_result_digest": self.change_set.result_digest,
                    "ingest_error_code": None,
                }
            )
            SqliteSourceRepository(self.db_path).update(source)
            SqliteWikiIngestRunRepository(self.db_path).add(
                WikiIngestRun(
                    id="RUN-RECOVERY",
                    project_id=self.change_set.project_id,
                    source_id=self.change_set.source_id,
                    transaction_id=self.change_set.transaction_id,
                    idempotency_key=self.change_set.idempotency_key,
                    schema_version="2.2",
                    generation_mode=self.change_set.generation_mode,
                    status="ingested",
                    source_page_path=self.change_set.source_page_path,
                    topic_page_paths=self.change_set.topic_page_paths,
                    result_digest=self.change_set.result_digest,
                    started_at=NOW,
                    finished_at=NOW,
                )
            )
        elif journal_state == ROLLED_BACK:
            failed_source = self.source().model_copy(
                update={
                    "ingest_status": "ingest_failed",
                    "ingest_error_code": "WIKI_TRANSACTION_FAILED",
                }
            )
            SqliteSourceRepository(self.db_path).update(failed_source)
            SqliteWikiIngestRunRepository(self.db_path).add(
                WikiIngestRun(
                    id="RUN-RECOVERY",
                    project_id=self.change_set.project_id,
                    source_id=self.change_set.source_id,
                    transaction_id=self.change_set.transaction_id,
                    idempotency_key=self.change_set.idempotency_key,
                    schema_version="2.2",
                    generation_mode=self.change_set.generation_mode,
                    status="ingest_failed",
                    error_code="WIKI_TRANSACTION_FAILED",
                    started_at=NOW,
                    finished_at=NOW,
                )
            )
        self.coordinator._set_transaction_state(store, journal_state)
        return store

    def snapshot(self) -> _Snapshot:
        wiki_hashes = {
            str(path.relative_to(self.paths.project_root)): _sha256(path.read_bytes())
            for path in sorted(self.paths.wiki_root.rglob("*"))
            if path.is_file()
        }
        source_index = self.page(".incubator/source-index.json")
        wiki_hashes[".incubator/source-index.json"] = _sha256(source_index.read_bytes())
        protected_paths = (
            "wiki/current/current.md",
            "wiki/versions/v1.md",
            "wiki/drafts/candidate.md",
            ".incubator/current-baseline.json",
            ".incubator/candidate-manifest.json",
        )
        protected_hashes = {
            relative_path: _sha256(self.page(relative_path).read_bytes())
            for relative_path in protected_paths
        }
        return _Snapshot(
            wiki_hashes=wiki_hashes,
            raw_sha256=self.raw_sha256(),
            protected_hashes=protected_hashes,
        )

    def _change_set(self) -> WikiChangeSet:
        idempotency_key = "d" * 64
        source_markdown = f"""---
project_id: PROJECT_A
source_id: SRC-A
material_series_id: MAT-A
material_version: '1.0'
raw_path: raw/SRC-A/material.md
raw_sha256: {self._source.sha256}
source_type: formal_document
authority_level: formal_effective
security_level: l1_public_simulated
schema_version: '2.2'
generation_mode: external_ai
ingested_at: '2026-08-17T12:00:00Z'
---
# Material
""".strip()
        contents = {
            "wiki/sources/SRC-A-material.md": source_markdown,
            "wiki/topics/pricing.md": "# Pricing\n\nUpdated from SRC-A.",
            "wiki/index.md": "# Wiki\n\n- [[wiki/sources/SRC-A-material]]",
            "wiki/log.md": f"# Log\n\n- {idempotency_key} SRC-A ingested",
            ".incubator/source-index.json": (
                '{"schema_version":"2.2","sources":[{"id":"SRC-A",'
                '"ingest_status":"ingested"}]}'
            ),
        }
        changes: list[WikiPageChange] = []
        for relative_path, markdown in contents.items():
            target = self.page(relative_path)
            changes.append(
                WikiPageChange(
                    relative_path=relative_path,
                    operation="replace" if target.is_file() else "create",
                    before_sha256=_sha256(target.read_bytes()) if target.is_file() else None,
                    markdown=markdown,
                    after_sha256=_sha256(markdown),
                )
            )
        return WikiChangeSet(
            transaction_id="TXN-A",
            project_id="PROJECT_A",
            source_id="SRC-A",
            raw_path="raw/SRC-A/material.md",
            raw_sha256=self._source.sha256,
            raw_size_bytes=self._source.size_bytes,
            idempotency_key=idempotency_key,
            schema_version="2.2",
            generation_mode=DocumentGenerationMode.EXTERNAL_AI,
            page_changes=changes,
            source_page_path="wiki/sources/SRC-A-material.md",
            topic_page_paths=["wiki/topics/pricing.md"],
            conflict_count=0,
            evidence_gap_count=0,
            result_digest="e" * 64,
        )

    def rebase_change_set(self, transaction_id: str, digest_character: str) -> WikiChangeSet:
        idempotency_key = digest_character * 64
        changes: list[WikiPageChange] = []
        for previous in self.change_set.page_changes:
            target = self.page(previous.relative_path)
            markdown = target.read_text(encoding="utf-8")
            if previous.relative_path == "wiki/log.md":
                markdown = (
                    f"{markdown.rstrip()}\n\n- {idempotency_key} SRC-A ingested"
                )
            changes.append(
                WikiPageChange(
                    relative_path=previous.relative_path,
                    operation="replace",
                    before_sha256=_sha256(target.read_bytes()),
                    markdown=markdown,
                    after_sha256=_sha256(markdown),
                )
            )
        return self.change_set.model_copy(
            update={
                "transaction_id": transaction_id,
                "idempotency_key": idempotency_key,
                "page_changes": changes,
                "result_digest": digest_character * 64,
            }
        )


@pytest.fixture
def transaction_fixture(tmp_path: Path) -> _TransactionFixture:
    return _TransactionFixture(tmp_path)


def test_commit_replaces_all_targets_and_database_once(
    transaction_fixture: _TransactionFixture,
) -> None:
    """Catches partial Wiki success that omits a target or authoritative DB update."""
    before = transaction_fixture.snapshot()

    result = transaction_fixture.coordinator.commit(transaction_fixture.change_set)

    assert result.status == "committed"
    assert transaction_fixture.source().ingest_status == "ingested"
    assert transaction_fixture.page("wiki/sources/SRC-A-material.md").is_file()
    assert transaction_fixture.page("wiki/log.md").read_text(encoding="utf-8").count(
        result.idempotency_key
    ) == 1
    assert transaction_fixture.raw_sha256() == before.raw_sha256
    assert transaction_fixture.snapshot().protected_hashes == before.protected_hashes


@pytest.mark.parametrize(
    "failure_stage",
    ["after_prepare", "after_first_file", "after_files", "before_database_commit"],
)
def test_failure_restores_files_and_leaves_no_success_run(
    transaction_fixture: _TransactionFixture,
    failure_stage: str,
) -> None:
    """Catches any injected commit failure leaving partial files or a success run."""
    before = transaction_fixture.snapshot()
    transaction_fixture.fail_at(failure_stage)

    with pytest.raises(RuntimeError, match="WIKI_TRANSACTION_FAILED"):
        transaction_fixture.coordinator.commit(transaction_fixture.change_set)

    assert transaction_fixture.snapshot().wiki_hashes == before.wiki_hashes
    assert transaction_fixture.raw_sha256() == before.raw_sha256
    assert transaction_fixture.source().ingest_status == "ingest_failed"


def test_raw_mutation_at_database_boundary_requires_recovery_and_never_succeeds(
    transaction_fixture,
) -> None:
    """A changed Raw cannot be converted into a successful Wiki transaction."""

    def mutate_raw(stage: str) -> None:
        if stage == "before_database_commit":
            transaction_fixture.raw_path.write_bytes(b"changed after verification")

    transaction_fixture.coordinator.failure_injector = mutate_raw

    with pytest.raises(RuntimeError, match="WIKI_RECOVERY_REQUIRED"):
        transaction_fixture.coordinator.commit(transaction_fixture.change_set)

    journal = next(
        (transaction_fixture.paths.system_root / "transactions").iterdir()
    ) / "journal.json"
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "recovery_required"
    assert transaction_fixture.succeeded_run() is None


@pytest.mark.parametrize(
    ("journal_state", "db_succeeded", "expected"),
    [
        (PREPARED, False, "rolled_back"),
        (FILES_COMMITTED, False, "rolled_back"),
        (FILES_COMMITTED, True, "committed"),
        (DATABASE_COMMITTED, True, "committed"),
        (DATABASE_COMMITTED, False, "recovery_required"),
        (COMMITTED, True, "committed"),
        (COMMITTED, False, "recovery_required"),
        (ROLLED_BACK, False, "rolled_back"),
        (ROLLED_BACK, True, "recovery_required"),
    ],
)
def test_recovery_matrix(
    transaction_fixture: _TransactionFixture,
    journal_state: str,
    db_succeeded: bool,
    expected: str,
) -> None:
    """Catches recovery guessing against the durable journal/DB truth matrix."""
    before = transaction_fixture.snapshot()
    store = transaction_fixture.seed_interrupted(
        journal_state, db_succeeded=db_succeeded
    )

    result = transaction_fixture.coordinator.recover()

    assert result is not None
    assert result.status == expected
    journal = json.loads(store.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == expected
    assert transaction_fixture.raw_sha256() == before.raw_sha256
    assert transaction_fixture.snapshot().protected_hashes == before.protected_hashes
    assert (transaction_fixture.succeeded_run() is not None) is db_succeeded
    if expected == "committed":
        for change in transaction_fixture.change_set.page_changes:
            assert _sha256(transaction_fixture.page(change.relative_path).read_bytes()) == (
                change.after_sha256
            )
        assert transaction_fixture.source().ingest_status == "ingested"
        assert store.result_path.is_file()
        assert not store.staged_root.exists()
        assert not store.backup_root.exists()
    elif expected == "rolled_back":
        assert transaction_fixture.snapshot().wiki_hashes == before.wiki_hashes
        assert transaction_fixture.source().ingest_status == "ingest_failed"
        assert store.result_path.is_file()
        assert not store.staged_root.exists()
        assert not store.backup_root.exists()
    else:
        assert store.result_path.is_file()
        assert store.staged_root.is_dir()
        assert store.backup_root.is_dir()
        with pytest.raises(RuntimeError, match="WIKI_RECOVERY_REQUIRED"):
            transaction_fixture.coordinator.commit(transaction_fixture.change_set)


@pytest.mark.parametrize(
    "field",
    ["source_id", "raw_sha256", "raw_size_bytes", "targets", "state"],
)
def test_tampered_journal_requires_recovery_before_restore_or_db_update(
    transaction_fixture: _TransactionFixture,
    field: str,
) -> None:
    before = transaction_fixture.snapshot()
    store = transaction_fixture.seed_interrupted(PREPARED, db_succeeded=False)
    journal = json.loads(store.journal_path.read_text(encoding="utf-8"))
    if field == "source_id":
        journal["source_id"] = "SRC-TAMPERED"
    elif field == "raw_sha256":
        journal["raw"]["sha256"] = "0" * 64
    elif field == "raw_size_bytes":
        journal["raw"]["size_bytes"] = 1
    elif field == "state":
        journal["state"] = "prepared" if journal["state"] != "prepared" else "rolled_back"
    else:
        journal["targets"][0]["after_sha256"] = "0" * 64
    store.write_journal(journal)

    result = transaction_fixture.coordinator.recover()

    assert result is not None
    assert result.status == "recovery_required"
    assert transaction_fixture.snapshot().wiki_hashes == before.wiki_hashes
    assert transaction_fixture.raw_sha256() == before.raw_sha256
    assert transaction_fixture.source().ingest_status == "ingesting"
    with connect(transaction_fixture.db_path) as connection:
        run = connection.execute(
            "SELECT status FROM wiki_ingest_runs WHERE transaction_id = ?",
            (transaction_fixture.change_set.transaction_id,),
        ).fetchone()
    assert run is None


@pytest.mark.parametrize(
    ("terminal_state", "db_succeeded", "expected"),
    [
        (COMMITTED, True, "committed"),
        (ROLLED_BACK, False, "rolled_back"),
    ],
)
def test_recovery_completes_terminal_crash_window_idempotently(
    transaction_fixture: _TransactionFixture,
    terminal_state: str,
    db_succeeded: bool,
    expected: str,
) -> None:
    """Catches a crash after terminal journal state leaving content or no result summary."""
    store = transaction_fixture.seed_interrupted(
        terminal_state, db_succeeded=db_succeeded
    )
    assert not store.result_path.exists()
    assert store.staged_root.is_dir()
    assert store.backup_root.is_dir()

    first = transaction_fixture.restarted_coordinator().recover()

    assert first is not None
    assert first.status == expected
    result_bytes = store.result_path.read_bytes()
    assert not store.staged_root.exists()
    assert not store.backup_root.exists()

    second = transaction_fixture.restarted_coordinator().recover()

    assert second is not None
    assert second.status == expected
    assert store.result_path.read_bytes() == result_bytes


@pytest.mark.parametrize("journal_payload", ["{not-json", "[]"])
def test_invalid_journal_uses_stable_recovery_required_boundary(
    transaction_fixture: _TransactionFixture,
    journal_payload: str,
) -> None:
    """Catches corrupt journal parsing errors escaping the safe recovery boundary."""
    store = WikiChangeSetStore(transaction_fixture.paths, "TXN-BAD-JOURNAL")
    store.transaction_root.mkdir(parents=True)
    store.journal_path.write_text(journal_payload, encoding="utf-8")

    with pytest.raises(RuntimeError, match="WIKI_RECOVERY_REQUIRED"):
        transaction_fixture.coordinator.recover()
    with pytest.raises(RuntimeError, match="WIKI_RECOVERY_REQUIRED"):
        transaction_fixture.coordinator.commit(transaction_fixture.change_set)


def test_before_hash_change_aborts_without_overwriting_owner_edit(
    transaction_fixture: _TransactionFixture,
) -> None:
    """Catches a commit overwriting an Owner edit made after proposal generation."""
    transaction_fixture.owner_edits("wiki/topics/pricing.md", "Owner new text")

    with pytest.raises(DomainError, match="WIKI_CONCURRENT_MODIFICATION"):
        transaction_fixture.coordinator.commit(transaction_fixture.change_set)

    assert transaction_fixture.page("wiki/topics/pricing.md").read_text(
        encoding="utf-8"
    ) == "Owner new text"


def test_committed_transaction_keeps_only_content_free_summary(
    transaction_fixture: _TransactionFixture,
) -> None:
    """Catches retained staged Wiki/Raw content leaking into durable transaction metadata."""
    transaction_fixture.coordinator.commit(transaction_fixture.change_set)
    transaction_root = (
        transaction_fixture.paths.system_root
        / "transactions"
        / transaction_fixture.change_set.transaction_id
    )

    assert not (transaction_root / "staged").exists()
    assert not (transaction_root / "backup").exists()
    journal = json.loads((transaction_root / "journal.json").read_text(encoding="utf-8"))
    result = json.loads((transaction_root / "result.json").read_text(encoding="utf-8"))
    serialized = json.dumps({"journal": journal, "result": result}, ensure_ascii=False)
    assert "markdown" not in serialized
    assert "Updated from SRC-A" not in serialized
    assert result["target_count"] == 5


def test_audit_log_renders_one_deterministic_ingest_entry(
    transaction_fixture: _TransactionFixture,
) -> None:
    """Catches retries duplicating the same successful Wiki ingest log entry."""
    audit = ProjectAuditLog(transaction_fixture.paths)
    existing = "# Log\n"
    rendered = audit.render_ingest(
        existing,
        transaction_id="TXN-A",
        idempotency_key="d" * 64,
        source_id="SRC-A",
        committed_at=NOW,
    )

    assert rendered == (
        "# Log\n\n"
        "- 2026-08-17T12:00:00+00:00 | Wiki Ingest | SRC-A | TXN-A | "
        f"{'d' * 64}\n"
    )
    assert (
        audit.render_ingest(
            rendered,
            transaction_id="TXN-A",
            idempotency_key="d" * 64,
            source_id="SRC-A",
            committed_at=NOW,
        )
        == rendered
    )


def test_project_lock_rejects_concurrent_commit(
    transaction_fixture: _TransactionFixture,
) -> None:
    """Catches two Wiki commits entering the same project transaction concurrently."""
    transaction_fixture.coordinator.lock_path.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(transaction_fixture.coordinator.lock_path, timeout=0):
        with pytest.raises(DomainError, match="WIKI_INGEST_ALREADY_RUNNING"):
            transaction_fixture.coordinator.commit(transaction_fixture.change_set)


def test_rollback_never_overwrites_unknown_owner_edit_and_blocks_next_commit(
    transaction_fixture: _TransactionFixture,
) -> None:
    """Catches rollback guessing over an Owner edit made during a partial file commit."""
    source_page = transaction_fixture.page("wiki/sources/SRC-A-material.md")

    def edit_then_fail(stage: str) -> None:
        if stage == "after_first_file":
            source_page.write_text("Owner recovery text", encoding="utf-8")
            raise RuntimeError("INJECTED:OWNER_EDIT")

    transaction_fixture.coordinator.failure_injector = edit_then_fail
    with pytest.raises(RuntimeError, match="WIKI_RECOVERY_REQUIRED"):
        transaction_fixture.coordinator.commit(transaction_fixture.change_set)

    assert source_page.read_text(encoding="utf-8") == "Owner recovery text"
    journal_path = (
        transaction_fixture.paths.system_root
        / "transactions/TXN-A/journal.json"
    )
    assert json.loads(journal_path.read_text(encoding="utf-8"))["state"] == (
        "recovery_required"
    )
    with pytest.raises(RuntimeError, match="WIKI_RECOVERY_REQUIRED"):
        transaction_fixture.coordinator.commit(transaction_fixture.change_set)
    assert source_page.read_text(encoding="utf-8") == "Owner recovery text"


def test_coordinator_rejects_project_local_database(
    transaction_fixture: _TransactionFixture,
) -> None:
    """Catches the transaction layer creating a second SQLite truth inside a project."""
    with pytest.raises(ValueError, match="WIKI_DATABASE_PATH_INVALID"):
        WikiTransactionCoordinator(
            paths=transaction_fixture.paths,
            db_path=transaction_fixture.paths.project_root / "local.db",
            validator=transaction_fixture.coordinator.validator,
        )

    assert not (transaction_fixture.paths.project_root / "local.db").exists()


def test_transaction_id_cannot_escape_fixed_transaction_directory(
    transaction_fixture: _TransactionFixture,
) -> None:
    """Catches an untrusted transaction ID redirecting journal/staging outside its root."""
    escaped = transaction_fixture.change_set.model_copy(
        update={"transaction_id": "../escaped"}
    )

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        transaction_fixture.coordinator.commit(escaped)

    assert not (transaction_fixture.paths.system_root / "escaped").exists()


def test_later_commits_do_not_revalidate_terminal_transaction_hashes(
    transaction_fixture: _TransactionFixture,
) -> None:
    """Catches an old committed journal blocking a later valid index/log replacement."""
    transaction_fixture.coordinator.commit(transaction_fixture.change_set)
    second = transaction_fixture.rebase_change_set("TXN-B", "b")
    assert transaction_fixture.coordinator.commit(second).status == "committed"
    third = transaction_fixture.rebase_change_set("TXN-C", "c")

    result = transaction_fixture.coordinator.commit(third)

    assert result.status == "committed"


def test_fixed_wiki_symlink_cannot_redirect_commit_into_current(
    transaction_fixture: _TransactionFixture,
) -> None:
    """Catches wiki/index.md resolving to and overwriting a protected current document."""
    index = transaction_fixture.page("wiki/index.md")
    current = transaction_fixture.page("wiki/current/current.md")
    current.write_bytes(index.read_bytes())
    protected = current.read_bytes()
    index.unlink()
    index.symlink_to("current/current.md")

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        transaction_fixture.coordinator.commit(transaction_fixture.change_set)

    assert current.read_bytes() == protected


def test_success_run_remains_durable_fact_when_source_starts_reingest(
    transaction_fixture: _TransactionFixture,
) -> None:
    """Catches a source's next ingest state invalidating its prior successful transaction."""
    transaction_fixture.coordinator.commit(transaction_fixture.change_set)
    sources = SqliteSourceRepository(transaction_fixture.db_path)
    sources.update(
        transaction_fixture.source().model_copy(update={"ingest_status": "ingesting"})
    )
    reingest = transaction_fixture.rebase_change_set("TXN-REINGEST", "a")

    result = transaction_fixture.coordinator.commit(reingest)

    assert result.status == "committed"


def test_stale_orphan_run_does_not_downgrade_a_newer_ingested_source(
    transaction_fixture: _TransactionFixture,
) -> None:
    """Catches orphan cleanup replacing a newer authoritative source success."""
    sources = SqliteSourceRepository(transaction_fixture.db_path)
    sources.update(
        transaction_fixture.source().model_copy(update={"ingest_status": "ingested"})
    )
    runs = SqliteWikiIngestRunRepository(transaction_fixture.db_path)
    runs.add(
        WikiIngestRun(
            id="RUN-STALE-OLD",
            project_id="PROJECT_A",
            source_id="SRC-A",
            transaction_id="TXN-STALE-OLD",
            idempotency_key="9" * 64,
            schema_version="2.2",
            generation_mode="external_ai",
            status="ingesting",
            started_at=NOW - timedelta(hours=1),
        )
    )

    transaction_fixture.coordinator.recover()

    assert sources.get("SRC-A").ingest_status == "ingested"
    recovered_run = runs.get_by_transaction("TXN-STALE-OLD")
    assert recovered_run is not None
    assert recovered_run.status == "ingest_failed"
