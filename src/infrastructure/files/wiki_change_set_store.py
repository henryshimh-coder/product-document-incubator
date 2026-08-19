from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout

from src.application.ports.wiki_ingest import WikiChangeSetValidating
from src.domain.errors import DomainError, ErrorCode
from src.domain.wiki import WikiChangeSet, WikiTransactionResult
from src.infrastructure.db.connection import connect
from src.infrastructure.files.project_library import (
    ProjectPaths,
    require_canonical_project_path,
    require_safe_project_roles,
)
from src.infrastructure.files.wiki_store import WikiStore

BUILDING = "building"
PREPARED = "prepared"
FILES_COMMITTED = "files_committed"
DATABASE_COMMITTED = "database_committed"
COMMITTED = "committed"
ROLLING_BACK = "rolling_back"
ROLLED_BACK = "rolled_back"
RECOVERY_REQUIRED = "recovery_required"

FailureInjector = Callable[[str], None]
Clock = Callable[[], datetime]
_TRANSACTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Version semantics:
# 0 = migrated pre-binding_state identity rows whose binding_state was backfilled
#     with a placeholder default during migrate(); only terminal journals can be
#     trusted enough to reopen these rows.
# 1 = current identity-only binding written by this coordinator.
_BINDING_VERSION_MIGRATED_IDENTITY = 0
_BINDING_VERSION_TRUSTED_IDENTITY = 1
_MIGRATED_BINDING_ALLOWED_TERMINAL_STATES = {
    COMMITTED,
    ROLLED_BACK,
    RECOVERY_REQUIRED,
}
_RECOVERY_STATE_BY_LAGGED_TERMINAL_JOURNAL = {
    (DATABASE_COMMITTED, COMMITTED): DATABASE_COMMITTED,
    (ROLLING_BACK, ROLLED_BACK): ROLLING_BACK,
}
_ALLOWED_JOURNAL_STATES_BY_BINDING = {
    BUILDING: {BUILDING, PREPARED, ROLLING_BACK, RECOVERY_REQUIRED},
    PREPARED: {PREPARED, FILES_COMMITTED, ROLLING_BACK, RECOVERY_REQUIRED},
    FILES_COMMITTED: {
        FILES_COMMITTED,
        DATABASE_COMMITTED,
        ROLLING_BACK,
        RECOVERY_REQUIRED,
    },
    DATABASE_COMMITTED: {DATABASE_COMMITTED, COMMITTED, RECOVERY_REQUIRED},
    COMMITTED: {COMMITTED},
    ROLLING_BACK: {ROLLING_BACK, ROLLED_BACK, RECOVERY_REQUIRED},
    ROLLED_BACK: {ROLLED_BACK},
    RECOVERY_REQUIRED: {RECOVERY_REQUIRED},
}


@dataclass(frozen=True)
class _JournalTarget:
    relative_path: str
    before_sha256: str | None
    after_sha256: str


@dataclass(frozen=True)
class _JournalRawEvidence:
    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class _TrustedRecoveryBinding:
    transaction_id: str
    project_id: str
    source_id: str
    idempotency_key: str
    binding_state: str
    binding_sha256: str
    binding_version: int


class WikiChangeSetStore:
    """Persist one content-free transaction journal and its staged file tree."""

    def __init__(self, paths: ProjectPaths, transaction_id: str) -> None:
        if _TRANSACTION_ID_PATTERN.fullmatch(transaction_id) is None:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "TRANSACTION_ID_INVALID")
        transactions_root = paths.system_root / "transactions"
        resolved_transactions_root = transactions_root.resolve()
        resolved_project_root = paths.project_root.resolve()
        transaction_root = transactions_root / transaction_id
        resolved_transaction_root = transaction_root.resolve()
        if (
            not resolved_transactions_root.is_relative_to(resolved_project_root)
            or resolved_transaction_root.parent != resolved_transactions_root
            or transaction_root.is_symlink()
        ):
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "TRANSACTION_PATH_INVALID")
        self.transaction_root = transaction_root
        self.journal_path = self.transaction_root / "journal.json"
        self.staged_root = self.transaction_root / "staged"
        self.backup_root = self.transaction_root / "backup"
        self.result_path = self.transaction_root / "result.json"

    def prepare(self, change_set: WikiChangeSet, wiki: WikiStore, now: datetime) -> None:
        self.staged_root.mkdir(parents=True, exist_ok=False)
        self.backup_root.mkdir(parents=True, exist_ok=False)
        created_at = now.isoformat()
        journal = {
            "transaction_id": change_set.transaction_id,
            "project_id": change_set.project_id,
            "source_id": change_set.source_id,
            "raw": {
                "relative_path": change_set.raw_path,
                "sha256": change_set.raw_sha256,
                "size_bytes": change_set.raw_size_bytes,
            },
            "idempotency_key": change_set.idempotency_key,
            "schema_version": change_set.schema_version,
            "generation_mode": change_set.generation_mode.value,
            "state": BUILDING,
            "created_at": created_at,
            "updated_at": created_at,
            "error_code": None,
            "targets": [
                {
                    "relative_path": change.relative_path,
                    "before_sha256": change.before_sha256,
                    "after_sha256": change.after_sha256,
                }
                for change in change_set.page_changes
            ],
        }
        self.write_journal(journal)
        for change in change_set.page_changes:
            wiki.stage(change, self.staged_root)
            wiki.backup(change, self.backup_root)
        self.set_state(PREPARED, now)

    def read_journal(self) -> dict[str, object]:
        payload = json.loads(self.journal_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("WIKI_JOURNAL_INVALID")
        return payload

    def set_state(self, state: str, now: datetime, *, error_code: str | None = None) -> None:
        journal = self.read_journal()
        journal["state"] = state
        journal["updated_at"] = now.isoformat()
        journal["error_code"] = error_code
        self.write_journal(journal)

    def write_result(self, change_set: WikiChangeSet, now: datetime) -> None:
        result = {
            "transaction_id": change_set.transaction_id,
            "project_id": change_set.project_id,
            "source_id": change_set.source_id,
            "idempotency_key": change_set.idempotency_key,
            "status": COMMITTED,
            "result_digest": change_set.result_digest,
            "source_page_path": change_set.source_page_path,
            "topic_page_paths": change_set.topic_page_paths,
            "target_count": len(change_set.page_changes),
            "finished_at": now.isoformat(),
        }
        self._atomic_json(self.result_path, result)

    def write_recovery_result(
        self,
        journal: dict[str, object],
        status: str,
        now: datetime,
    ) -> None:
        targets = journal.get("targets")
        result = {
            "transaction_id": journal.get("transaction_id"),
            "project_id": journal.get("project_id"),
            "source_id": journal.get("source_id"),
            "idempotency_key": journal.get("idempotency_key"),
            "status": status,
            "target_count": len(targets) if isinstance(targets, list) else 0,
            "finished_at": now.isoformat(),
        }
        self._atomic_json(self.result_path, result)

    def ensure_recovery_result(
        self,
        journal: dict[str, object],
        status: str,
        now: datetime,
    ) -> None:
        if not self.result_path.is_file():
            self.write_recovery_result(journal, status, now)

    def write_journal(self, journal: dict[str, object]) -> None:
        self.transaction_root.mkdir(parents=True, exist_ok=True)
        self._atomic_json(self.journal_path, journal)

    def cleanup_payloads(self) -> None:
        shutil.rmtree(self.staged_root, ignore_errors=True)
        shutil.rmtree(self.backup_root, ignore_errors=True)

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, object]) -> None:
        temporary = path.parent / f".{path.name}.tmp-{uuid4().hex}"
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class WikiTransactionCoordinator:
    """Coordinate recoverable project files with one authoritative SQLite commit."""

    def __init__(
        self,
        *,
        paths: ProjectPaths,
        db_path: Path,
        validator: WikiChangeSetValidating | None,
        clock: Clock | None = None,
        failure_injector: FailureInjector | None = None,
        interrupted_after: timedelta = timedelta(minutes=15),
    ) -> None:
        self.paths = paths
        lexical_db_path = db_path.expanduser().absolute()
        resolved_db_path = lexical_db_path.resolve()
        project_root = paths.project_root.resolve()
        if (
            lexical_db_path.is_relative_to(project_root)
            or resolved_db_path.is_relative_to(project_root)
            or not resolved_db_path.is_file()
        ):
            raise ValueError("WIKI_DATABASE_PATH_INVALID")
        self.db_path = resolved_db_path
        self.validator = validator
        self.clock = clock or (lambda: datetime.now(UTC))
        self.failure_injector = failure_injector or (lambda _stage: None)
        if interrupted_after <= timedelta(0):
            raise ValueError("interrupted_after must be positive")
        self.interrupted_after = interrupted_after
        self.wiki = WikiStore(paths)
        self.lock_path = paths.system_root / "locks" / "wiki-ingest.lock"

    def commit(self, change_set: WikiChangeSet) -> WikiTransactionResult:
        self._ensure_lock_root()
        try:
            with FileLock(self.lock_path, timeout=0):
                return self._commit_locked(change_set)
        except Timeout as error:
            raise DomainError(ErrorCode.WIKI_INGEST_ALREADY_RUNNING) from error

    def recover(self) -> WikiTransactionResult | None:
        self._ensure_lock_root()
        try:
            with FileLock(self.lock_path, timeout=0):
                result = self._recover_all_locked()
                if result is None or result.status != RECOVERY_REQUIRED:
                    self._mark_interrupted_runs_locked()
                return result
        except Timeout as error:
            raise DomainError(ErrorCode.WIKI_INGEST_ALREADY_RUNNING) from error

    def _ensure_lock_root(self) -> None:
        try:
            require_safe_project_roles(self.paths)
            lock_root = require_canonical_project_path(
                self.paths,
                ".incubator/locks",
                require_directory=True,
            )
        except ValueError:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCK_PATH_INVALID") from None
        lock_root.mkdir(parents=True, exist_ok=True)

    def _commit_locked(self, change_set: WikiChangeSet) -> WikiTransactionResult:
        recovered = self._recover_all_locked()
        if recovered is not None and recovered.status == RECOVERY_REQUIRED:
            raise RuntimeError("WIKI_RECOVERY_REQUIRED")
        if self.validator is None:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "WIKI_VALIDATOR_REQUIRED")
        self._verify_raw_evidence(
            _JournalRawEvidence(
                change_set.raw_path,
                change_set.raw_sha256,
                change_set.raw_size_bytes,
            )
        )
        self.validator.validate_change_set(change_set)
        for change in change_set.page_changes:
            self.wiki.verify_before(change)
        self._persist_recovery_binding(change_set, state=BUILDING)

        store = WikiChangeSetStore(self.paths, change_set.transaction_id)
        try:
            store.prepare(change_set, self.wiki, self.clock())
            self._set_transaction_state(store, PREPARED)
            self.failure_injector("after_prepare")
            for index, change in enumerate(change_set.page_changes):
                self.wiki.commit_staged(change, store.staged_root)
                if index == 0:
                    self.failure_injector("after_first_file")
            self._set_transaction_state(store, FILES_COMMITTED)
            self.failure_injector("after_files")
            self.failure_injector("before_database_commit")
            self._verify_raw_evidence(
                _JournalRawEvidence(
                    change_set.raw_path,
                    change_set.raw_sha256,
                    change_set.raw_size_bytes,
                )
            )
            self._commit_database(change_set)
            self._set_transaction_state(store, DATABASE_COMMITTED)
            self._verify_raw_evidence(
                _JournalRawEvidence(
                    change_set.raw_path,
                    change_set.raw_sha256,
                    change_set.raw_size_bytes,
                )
            )
            for change in change_set.page_changes:
                self.wiki.verify_after(change)
            self._set_transaction_state(store, COMMITTED)
            store.write_result(change_set, self.clock())
            store.cleanup_payloads()
            return WikiTransactionResult(
                transaction_id=change_set.transaction_id,
                idempotency_key=change_set.idempotency_key,
                status=COMMITTED,
            )
        except Exception as error:
            try:
                raw = _JournalRawEvidence(
                    change_set.raw_path,
                    change_set.raw_sha256,
                    change_set.raw_size_bytes,
                )
                self._verify_raw_evidence(raw)
                self._set_transaction_state(
                    store,
                    ROLLING_BACK,
                    error_code="WIKI_TRANSACTION_FAILED",
                )
                for change in reversed(change_set.page_changes):
                    self.wiki.restore(change, store.backup_root)
                self._verify_raw_evidence(raw)
                self._record_failure(change_set, "WIKI_TRANSACTION_FAILED")
                self._set_transaction_state(
                    store,
                    ROLLED_BACK,
                    error_code="WIKI_TRANSACTION_FAILED",
                )
                store.write_recovery_result(
                    store.read_journal(),
                    ROLLED_BACK,
                    self.clock(),
                )
                store.cleanup_payloads()
            except Exception as rollback_error:
                self._set_transaction_state(
                    store,
                    RECOVERY_REQUIRED,
                    error_code="WIKI_RECOVERY_REQUIRED",
                )
                raise RuntimeError("WIKI_RECOVERY_REQUIRED") from rollback_error
            raise RuntimeError("WIKI_TRANSACTION_FAILED") from error

    def _recover_all_locked(self) -> WikiTransactionResult | None:
        transactions_root = self.paths.system_root / "transactions"
        if not transactions_root.is_dir():
            return None
        stores: list[tuple[str, WikiChangeSetStore, dict[str, object]]] = []
        for transaction_root in transactions_root.iterdir():
            if not transaction_root.is_dir() or transaction_root.is_symlink():
                continue
            try:
                store = WikiChangeSetStore(self.paths, transaction_root.name)
                if not store.journal_path.is_file():
                    continue
                journal = store.read_journal()
            except Exception as error:
                raise RuntimeError("WIKI_RECOVERY_REQUIRED: JOURNAL_INVALID") from error
            created_at = journal.get("created_at")
            stores.append((str(created_at), store, journal))
        result: WikiTransactionResult | None = None
        stores.sort(key=lambda item: (item[0], item[1].transaction_root.name))
        for _, store, journal in stores:
            current = self._recover_one(store, journal)
            if current is not None:
                result = current
            if result is not None and result.status == RECOVERY_REQUIRED:
                break
        return result

    def _recover_one(
        self,
        store: WikiChangeSetStore,
        journal: dict[str, object],
    ) -> WikiTransactionResult:
        try:
            transaction_id, project_id, source_id, idempotency_key, state, targets, raw = (
                self._validate_journal(store, journal)
            )
            trusted = self._load_recovery_binding(transaction_id)
            if trusted is None or not self._journal_matches_binding(
                trusted,
                state=state,
                project_id=project_id,
                source_id=source_id,
                idempotency_key=idempotency_key,
                raw=raw,
                targets=targets,
            ):
                return self._require_recovery(
                    store,
                    journal,
                    transaction_id,
                    trusted.idempotency_key if trusted is not None else idempotency_key,
                )
            idempotency_key = trusted.idempotency_key
        except Exception as error:
            with_context = store.journal_path.is_file()
            if with_context:
                store.set_state(
                    RECOVERY_REQUIRED,
                    self.clock(),
                    error_code="WIKI_RECOVERY_REQUIRED",
                )
            raise RuntimeError("WIKI_RECOVERY_REQUIRED: JOURNAL_INVALID") from error

        try:
            self._verify_raw_evidence(raw)
        except Exception:
            return self._require_recovery(store, journal, transaction_id, idempotency_key)

        recovery_state = self._recovery_state_for_binding(trusted, state)
        db_succeeded = self._database_succeeded(
            transaction_id,
            trusted.project_id,
            trusted.source_id,
            trusted.idempotency_key,
        )
        if recovery_state == RECOVERY_REQUIRED:
            return self._result(transaction_id, idempotency_key, RECOVERY_REQUIRED)
        if recovery_state == ROLLED_BACK:
            if db_succeeded:
                return self._require_recovery(
                    store,
                    journal,
                    transaction_id,
                    idempotency_key,
                )
            try:
                self._verify_raw_evidence(raw)
            except Exception:
                return self._require_recovery(store, journal, transaction_id, idempotency_key)
            if trusted.binding_version == _BINDING_VERSION_MIGRATED_IDENTITY:
                self._set_transaction_state(store, ROLLED_BACK)
            store.ensure_recovery_result(journal, ROLLED_BACK, self.clock())
            store.cleanup_payloads()
            return self._result(transaction_id, idempotency_key, ROLLED_BACK)
        if recovery_state == COMMITTED:
            if not db_succeeded:
                return self._require_recovery(
                    store,
                    journal,
                    transaction_id,
                    idempotency_key,
                )
            try:
                self._verify_raw_evidence(raw)
            except Exception:
                return self._require_recovery(store, journal, transaction_id, idempotency_key)
            if trusted.binding_version == _BINDING_VERSION_MIGRATED_IDENTITY:
                self._set_transaction_state(store, COMMITTED)
            store.ensure_recovery_result(journal, COMMITTED, self.clock())
            store.cleanup_payloads()
            return self._result(transaction_id, idempotency_key, COMMITTED)

        if recovery_state in {BUILDING, PREPARED, ROLLING_BACK} or (
            recovery_state == FILES_COMMITTED and not db_succeeded
        ):
            try:
                self._set_transaction_state(
                    store,
                    ROLLING_BACK,
                    error_code="WIKI_INGEST_INTERRUPTED",
                )
                for target in reversed(targets):
                    self.wiki.restore(target, store.backup_root)
                self._verify_raw_evidence(raw)
                self._record_recovery_failure(
                    transaction_id,
                    trusted.project_id,
                    trusted.source_id,
                )
                self._set_transaction_state(
                    store,
                    ROLLED_BACK,
                    error_code="WIKI_INGEST_INTERRUPTED",
                )
                store.write_recovery_result(journal, ROLLED_BACK, self.clock())
                store.cleanup_payloads()
                return self._result(transaction_id, idempotency_key, ROLLED_BACK)
            except Exception:
                return self._require_recovery(store, journal, transaction_id, idempotency_key)

        if recovery_state == DATABASE_COMMITTED and not db_succeeded:
            return self._require_recovery(store, journal, transaction_id, idempotency_key)

        if recovery_state in {FILES_COMMITTED, DATABASE_COMMITTED} and db_succeeded:
            try:
                for target in targets:
                    self.wiki.verify_after(target)
                self._verify_raw_evidence(raw)
                if recovery_state == FILES_COMMITTED:
                    self._set_transaction_state(store, DATABASE_COMMITTED)
                self._set_transaction_state(store, COMMITTED)
                store.write_recovery_result(journal, COMMITTED, self.clock())
                store.cleanup_payloads()
                return self._result(transaction_id, idempotency_key, COMMITTED)
            except Exception:
                return self._require_recovery(store, journal, transaction_id, idempotency_key)

        return self._require_recovery(store, journal, transaction_id, idempotency_key)

    def _validate_journal(
        self,
        store: WikiChangeSetStore,
        journal: dict[str, object],
    ) -> tuple[str, str, str, str, str, list[_JournalTarget], _JournalRawEvidence]:
        transaction_id = journal.get("transaction_id")
        project_id = journal.get("project_id")
        source_id = journal.get("source_id")
        idempotency_key = journal.get("idempotency_key")
        state = journal.get("state")
        raw_targets = journal.get("targets")
        raw_evidence = journal.get("raw")
        if (
            not isinstance(transaction_id, str)
            or transaction_id != store.transaction_root.name
            or not isinstance(project_id, str)
            or not isinstance(source_id, str)
            or not isinstance(idempotency_key, str)
            or len(idempotency_key) != 64
            or not isinstance(state, str)
            or not isinstance(raw_targets, list)
            or not raw_targets
            or not isinstance(raw_evidence, dict)
        ):
            raise ValueError("WIKI_JOURNAL_INVALID")
        raw_path = raw_evidence.get("relative_path")
        raw_sha256 = raw_evidence.get("sha256")
        raw_size_bytes = raw_evidence.get("size_bytes")
        if (
            not isinstance(raw_path, str)
            or not self._is_sha256(raw_sha256)
            or not isinstance(raw_size_bytes, int)
            or raw_size_bytes < 0
        ):
            raise ValueError("WIKI_JOURNAL_RAW_INVALID")
        targets: list[_JournalTarget] = []
        for item in raw_targets:
            if not isinstance(item, dict):
                raise ValueError("WIKI_JOURNAL_TARGET_INVALID")
            relative_path = item.get("relative_path")
            before_sha256 = item.get("before_sha256")
            after_sha256 = item.get("after_sha256")
            if (
                not isinstance(relative_path, str)
                or (before_sha256 is not None and not self._is_sha256(before_sha256))
                or not self._is_sha256(after_sha256)
            ):
                raise ValueError("WIKI_JOURNAL_TARGET_INVALID")
            self.wiki.target(relative_path)
            targets.append(_JournalTarget(relative_path, before_sha256, after_sha256))
        return (
            transaction_id,
            project_id,
            source_id,
            idempotency_key,
            state,
            targets,
            _JournalRawEvidence(raw_path, raw_sha256, raw_size_bytes),
        )

    def _verify_raw_evidence(self, evidence: _JournalRawEvidence) -> None:
        """Verify the same immutable Raw bytes at every durable boundary."""

        try:
            require_safe_project_roles(self.paths)
        except ValueError as error:
            raise RuntimeError("WIKI_RAW_PATH_INVALID") from error
        relative = Path(evidence.relative_path)
        if (
            relative.is_absolute()
            or "\\" in evidence.relative_path
            or relative.as_posix() != evidence.relative_path
            or not evidence.relative_path.startswith("raw/")
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise RuntimeError("WIKI_RAW_PATH_INVALID")
        lexical = self.paths.project_root / relative
        resolved = lexical.resolve()
        if (
            lexical.is_symlink()
            or resolved != lexical
            or not resolved.is_relative_to(self.paths.raw_root)
            or not resolved.is_file()
        ):
            raise RuntimeError("WIKI_RAW_PATH_INVALID")
        try:
            payload = resolved.read_bytes()
        except OSError as error:
            raise RuntimeError("WIKI_RAW_READ_FAILED") from error
        if (
            len(payload) != evidence.size_bytes
            or hashlib.sha256(payload).hexdigest() != evidence.sha256
        ):
            raise RuntimeError("WIKI_RAW_INTEGRITY_FAILED")

    @staticmethod
    def _is_sha256(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    def _database_succeeded(
        self,
        transaction_id: str,
        project_id: str,
        source_id: str,
        idempotency_key: str,
    ) -> bool:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT status, idempotency_key
                FROM wiki_ingest_runs AS run
                WHERE run.transaction_id = ? AND run.project_id = ? AND run.source_id = ?
                """,
                (transaction_id, project_id, source_id),
            ).fetchone()
        return bool(
            row is not None
            and row["status"] == "ingested"
            and row["idempotency_key"] == idempotency_key
        )

    def _record_recovery_failure(
        self,
        transaction_id: str,
        project_id: str,
        source_id: str,
    ) -> None:
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE source_records
                SET ingest_status = 'ingest_failed', ingest_error_code = 'WIKI_INGEST_INTERRUPTED'
                WHERE id = ? AND project_id = ?
                """,
                (source_id, project_id),
            )
            connection.execute(
                """
                UPDATE wiki_ingest_runs
                SET status = 'ingest_failed', error_code = 'WIKI_INGEST_INTERRUPTED',
                    finished_at = ?
                WHERE transaction_id = ?
                """,
                (self.clock().isoformat(), transaction_id),
            )

    def _persist_recovery_binding(self, change_set: WikiChangeSet, *, state: str) -> None:
        self._write_recovery_binding(
            transaction_id=change_set.transaction_id,
            project_id=change_set.project_id,
            source_id=change_set.source_id,
            idempotency_key=change_set.idempotency_key,
            state=state,
            raw=_JournalRawEvidence(
                change_set.raw_path,
                change_set.raw_sha256,
                change_set.raw_size_bytes,
            ),
            targets=[
                _JournalTarget(
                    change.relative_path,
                    change.before_sha256,
                    change.after_sha256,
                )
                for change in change_set.page_changes
            ],
        )

    def _write_recovery_binding(
        self,
        *,
        transaction_id: str,
        project_id: str,
        source_id: str,
        idempotency_key: str,
        state: str,
        raw: _JournalRawEvidence,
        targets: list[_JournalTarget],
    ) -> None:
        payload = self._binding_identity_payload(
            transaction_id=transaction_id,
            project_id=project_id,
            source_id=source_id,
            idempotency_key=idempotency_key,
            raw=raw,
            targets=targets,
        )
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO wiki_transaction_bindings (
                    transaction_id, project_id, source_id, idempotency_key,
                    binding_state, binding_sha256, binding_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    project_id,
                    source_id,
                    idempotency_key,
                    state,
                    self._binding_sha256(payload),
                    _BINDING_VERSION_TRUSTED_IDENTITY,
                    self.clock().isoformat(),
                ),
            )

    def _set_transaction_state(
        self,
        store: WikiChangeSetStore,
        state: str,
        *,
        error_code: str | None = None,
    ) -> None:
        journal = store.read_journal()
        transaction_id, project_id, source_id, idempotency_key, journal_state, targets, raw = (
            self._validate_journal(store, journal)
        )
        trusted = self._load_recovery_binding(transaction_id)
        if trusted is None:
            raise RuntimeError("WIKI_RECOVERY_REQUIRED")
        match_kind = self._binding_match_kind(
            trusted,
            state=journal_state,
            project_id=project_id,
            source_id=source_id,
            idempotency_key=idempotency_key,
            raw=raw,
            targets=targets,
        )
        if match_kind != "identity":
            raise RuntimeError("WIKI_RECOVERY_REQUIRED")
        store.set_state(state, self.clock(), error_code=error_code)
        next_binding_version = (
            _BINDING_VERSION_TRUSTED_IDENTITY
            if trusted.binding_version == _BINDING_VERSION_MIGRATED_IDENTITY
            else trusted.binding_version
        )
        with connect(self.db_path) as connection:
            updated = connection.execute(
                """
                UPDATE wiki_transaction_bindings
                SET binding_state = ?, binding_version = ?
                WHERE transaction_id = ? AND project_id = ? AND source_id = ?
                  AND idempotency_key = ? AND binding_sha256 = ? AND binding_version = ?
                """,
                (
                    state,
                    next_binding_version,
                    trusted.transaction_id,
                    trusted.project_id,
                    trusted.source_id,
                    trusted.idempotency_key,
                    trusted.binding_sha256,
                    trusted.binding_version,
                ),
            )
        if updated.rowcount != 1:
            raise RuntimeError("WIKI_RECOVERY_REQUIRED")

    def _load_recovery_binding(self, transaction_id: str) -> _TrustedRecoveryBinding | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT transaction_id, project_id, source_id, idempotency_key,
                       binding_state, binding_sha256, binding_version
                FROM wiki_transaction_bindings
                WHERE transaction_id = ?
                """,
                (transaction_id,),
            ).fetchone()
        if row is None:
            return None
        return _TrustedRecoveryBinding(
            transaction_id=str(row["transaction_id"]),
            project_id=str(row["project_id"]),
            source_id=str(row["source_id"]),
            idempotency_key=str(row["idempotency_key"]),
            binding_state=str(row["binding_state"]),
            binding_sha256=str(row["binding_sha256"]),
            binding_version=int(row["binding_version"]),
        )

    def _journal_matches_binding(
        self,
        binding: _TrustedRecoveryBinding,
        *,
        state: str,
        project_id: str,
        source_id: str,
        idempotency_key: str,
        raw: _JournalRawEvidence,
        targets: list[_JournalTarget],
    ) -> bool:
        if (
            project_id != binding.project_id
            or source_id != binding.source_id
            or idempotency_key != binding.idempotency_key
        ):
            return False
        if (
            self._binding_match_kind(
                binding,
                state=state,
                project_id=project_id,
                source_id=source_id,
                idempotency_key=idempotency_key,
                raw=raw,
                targets=targets,
            )
            != "identity"
        ):
            return False
        return state in self._allowed_journal_states(binding)

    @staticmethod
    def _allowed_journal_states(binding: _TrustedRecoveryBinding) -> set[str]:
        if binding.binding_version == _BINDING_VERSION_MIGRATED_IDENTITY:
            return set(_MIGRATED_BINDING_ALLOWED_TERMINAL_STATES)
        if binding.binding_version == _BINDING_VERSION_TRUSTED_IDENTITY:
            return set(_ALLOWED_JOURNAL_STATES_BY_BINDING.get(binding.binding_state, set()))
        return set()

    @staticmethod
    def _recovery_state_for_binding(binding: _TrustedRecoveryBinding, journal_state: str) -> str:
        if binding.binding_version == _BINDING_VERSION_MIGRATED_IDENTITY:
            return journal_state
        return _RECOVERY_STATE_BY_LAGGED_TERMINAL_JOURNAL.get(
            (binding.binding_state, journal_state),
            journal_state,
        )

    def _binding_match_kind(
        self,
        binding: _TrustedRecoveryBinding,
        *,
        state: str,
        project_id: str,
        source_id: str,
        idempotency_key: str,
        raw: _JournalRawEvidence,
        targets: list[_JournalTarget],
    ) -> str | None:
        identity_payload = self._binding_identity_payload(
            transaction_id=binding.transaction_id,
            project_id=project_id,
            source_id=source_id,
            idempotency_key=idempotency_key,
            raw=raw,
            targets=targets,
        )
        if self._binding_sha256(identity_payload) == binding.binding_sha256:
            return "identity"
        return None

    @staticmethod
    def _binding_identity_payload(
        *,
        transaction_id: str,
        project_id: str,
        source_id: str,
        idempotency_key: str,
        raw: _JournalRawEvidence,
        targets: list[_JournalTarget],
    ) -> dict[str, object]:
        return {
            "transaction_id": transaction_id,
            "project_id": project_id,
            "source_id": source_id,
            "idempotency_key": idempotency_key,
            "raw": {
                "relative_path": raw.relative_path,
                "sha256": raw.sha256,
                "size_bytes": raw.size_bytes,
            },
            "targets": [
                {
                    "relative_path": target.relative_path,
                    "before_sha256": target.before_sha256,
                    "after_sha256": target.after_sha256,
                }
                for target in targets
            ],
        }

    @staticmethod
    def _binding_legacy_stateful_payload(
        *,
        transaction_id: str,
        project_id: str,
        source_id: str,
        idempotency_key: str,
        state: str,
        raw: _JournalRawEvidence,
        targets: list[_JournalTarget],
    ) -> dict[str, object]:
        payload = WikiTransactionCoordinator._binding_identity_payload(
            transaction_id=transaction_id,
            project_id=project_id,
            source_id=source_id,
            idempotency_key=idempotency_key,
            raw=raw,
            targets=targets,
        )
        payload["state"] = state
        return payload

    @staticmethod
    def _binding_sha256(payload: dict[str, object]) -> str:
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _mark_interrupted_runs_locked(self) -> None:
        cutoff = self.clock() - self.interrupted_after
        now = self.clock().isoformat()
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT transaction_id, source_id
                FROM wiki_ingest_runs
                WHERE project_id = ? AND status = 'ingesting' AND started_at < ?
                ORDER BY started_at, id
                """,
                (self.paths.project_id, cutoff.isoformat()),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE source_records
                    SET ingest_status = 'ingest_failed',
                        ingest_error_code = 'WIKI_INGEST_INTERRUPTED'
                    WHERE id = ? AND project_id = ? AND ingest_status = 'ingesting'
                    """,
                    (row["source_id"], self.paths.project_id),
                )
                connection.execute(
                    """
                    UPDATE wiki_ingest_runs
                    SET status = 'ingest_failed', error_code = 'WIKI_INGEST_INTERRUPTED',
                        finished_at = ?
                    WHERE transaction_id = ? AND status = 'ingesting'
                    """,
                    (now, row["transaction_id"]),
                )

    def _require_recovery(
        self,
        store: WikiChangeSetStore,
        journal: dict[str, object],
        transaction_id: str,
        idempotency_key: str,
    ) -> WikiTransactionResult:
        store.set_state(
            RECOVERY_REQUIRED,
            self.clock(),
            error_code="WIKI_RECOVERY_REQUIRED",
        )
        store.write_recovery_result(journal, RECOVERY_REQUIRED, self.clock())
        return self._result(transaction_id, idempotency_key, RECOVERY_REQUIRED)

    @staticmethod
    def _result(transaction_id: str, idempotency_key: str, status: str) -> WikiTransactionResult:
        return WikiTransactionResult(
            transaction_id=transaction_id,
            idempotency_key=idempotency_key,
            status=status,
        )

    def _commit_database(self, change_set: WikiChangeSet) -> None:
        now = self.clock()
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE source_records
                SET ingest_status = 'ingested', ingest_schema_version = ?, ingested_at = ?,
                    source_page_path = ?, topic_page_paths_json = ?, ingest_result_digest = ?,
                    ingest_error_code = NULL, generation_mode = ?
                WHERE id = ? AND project_id = ?
                """,
                (
                    change_set.schema_version,
                    now.isoformat(),
                    change_set.source_page_path,
                    json.dumps(change_set.topic_page_paths, ensure_ascii=False),
                    change_set.result_digest,
                    change_set.generation_mode.value,
                    change_set.source_id,
                    change_set.project_id,
                ),
            )
            if updated.rowcount != 1:
                raise DomainError(ErrorCode.WIKI_TRANSACTION_FAILED, "SOURCE_NOT_FOUND")
            connection.execute(
                """
                INSERT INTO wiki_ingest_runs (
                    id, project_id, source_id, transaction_id, idempotency_key, schema_version,
                    generation_mode, status, source_page_path, topic_page_paths_json,
                    result_digest, error_code, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ingested', ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    status = 'ingested', source_page_path = excluded.source_page_path,
                    topic_page_paths_json = excluded.topic_page_paths_json,
                    result_digest = excluded.result_digest, error_code = NULL,
                    finished_at = excluded.finished_at
                """,
                (
                    f"WIKI-{change_set.transaction_id}",
                    change_set.project_id,
                    change_set.source_id,
                    change_set.transaction_id,
                    change_set.idempotency_key,
                    change_set.schema_version,
                    change_set.generation_mode.value,
                    change_set.source_page_path,
                    json.dumps(change_set.topic_page_paths, ensure_ascii=False),
                    change_set.result_digest,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

    def _record_failure(self, change_set: WikiChangeSet, error_code: str) -> None:
        now = self.clock()
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE source_records
                SET ingest_status = 'ingest_failed', ingest_error_code = ?
                WHERE id = ? AND project_id = ?
                """,
                (error_code, change_set.source_id, change_set.project_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("WIKI_RECOVERY_REQUIRED: SOURCE_NOT_FOUND")
            connection.execute(
                """
                INSERT INTO wiki_ingest_runs (
                    id, project_id, source_id, transaction_id, idempotency_key, schema_version,
                    generation_mode, status, source_page_path, topic_page_paths_json,
                    result_digest, error_code, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ingest_failed', NULL, '[]', NULL, ?, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    status = 'ingest_failed', error_code = excluded.error_code,
                    finished_at = excluded.finished_at
                """,
                (
                    f"WIKI-{change_set.transaction_id}",
                    change_set.project_id,
                    change_set.source_id,
                    change_set.transaction_id,
                    change_set.idempotency_key,
                    change_set.schema_version,
                    change_set.generation_mode.value,
                    error_code,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
