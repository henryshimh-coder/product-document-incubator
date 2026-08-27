from __future__ import annotations

import importlib
import sqlite3
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock

import pytest

from src.application.dto.documents import IncubateDocumentInput, IncubationView
from src.domain.enums import DocumentIncubationJobStatus
from src.domain.incubator import DocumentIncubationJob
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteDocumentDraftRepository,
    SqliteDocumentIncubationJobRepository,
)

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)


def _coordinator_type():
    try:
        module = importlib.import_module("src.application.use_cases.manage_document_incubation_job")
    except ModuleNotFoundError:
        pytest.fail("DocumentIncubationCoordinator has not been implemented")
    return module.DocumentIncubationCoordinator


def _database(tmp_path: Path) -> Path:
    db_path = tmp_path / "product_incubator.db"
    migrate(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO projects (
                id, name, product_line, stage, current_baseline_id,
                allow_external_model, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "PROJECT_A",
                "产品文档孵化器",
                "产品线",
                "孵化中",
                None,
                1,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
    return db_path


def _command() -> IncubateDocumentInput:
    return IncubateDocumentInput(
        project_id="PROJECT_A",
        source_ids=["SRC-001"],
        requested_by="Henry",
    )


def _job(
    job_id: str,
    *,
    created_at: datetime = NOW,
) -> DocumentIncubationJob:
    return DocumentIncubationJob(
        id=job_id,
        project_id="PROJECT_A",
        source_ids=["SRC-001"],
        requested_by="Henry",
        status=DocumentIncubationJobStatus.PENDING,
        created_at=created_at,
        updated_at=created_at,
    )


def _insert_draft(db_path: Path, draft_id: str, created_at: datetime) -> IncubationView:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO document_drafts (
                id, project_id, version_id, display_version, parent_version_id, status,
                markdown_path, markdown_sha256, source_ids_json, section_citations_json,
                summary, missing_sections_json, evidence_gaps_json, created_at, updated_at,
                generation_mode
            ) VALUES (?, 'PROJECT_A', ?, NULL, NULL, 'candidate_draft', ?, ?, '["SRC-001"]',
                      '[]', '候选产品文档', '[]', '[]', ?, ?, 'external_ai')
            """,
            (
                draft_id,
                f"VERSION-{draft_id}",
                f"wiki/candidates/{draft_id}.md",
                "0" * 64,
                created_at.isoformat(),
                created_at.isoformat(),
            ),
        )
    draft = SqliteDocumentDraftRepository(db_path).get(draft_id)
    return IncubationView(draft=draft, markdown="# 候选产品文档\n")


class BlockingIncubation:
    """Keeps only the external model wait fake; SQLite effects remain real."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.started = Event()
        self.release = Event()
        self.finished = Event()
        self._lock = Lock()
        self.execute_count = 0
        self.recovery_count = 0

    def execute(
        self,
        command: IncubateDocumentInput,
        *,
        on_started: Callable[[str, str], None] | None = None,
    ) -> IncubationView:
        assert command == _command()
        with self._lock:
            self.execute_count += 1
        if on_started is not None:
            on_started("DIFY-TASK-LIVE", "WORKFLOW-LIVE")
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release the fake workflow")
        view = _insert_draft(self.db_path, "DRAFT-LIVE", NOW)
        self.finished.set()
        return view

    def complete_from_workflow(
        self,
        command: IncubateDocumentInput,
        workflow_response: Mapping[str, object],
    ) -> IncubationView:
        assert command == _command()
        assert workflow_response["workflow_run_id"] == "WORKFLOW-RECOVERY"
        assert workflow_response["status"] == "succeeded"
        self.recovery_count += 1
        return _insert_draft(self.db_path, "DRAFT-RECOVERY", NOW)


class RunDetails:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = dict(response)
        self.calls: list[tuple[str, str]] = []

    def get_run(self, *, workflow_run_id: str, user: str) -> Mapping[str, object]:
        self.calls.append((workflow_run_id, user))
        return self.response


class TimeoutAfterStartedIncubation(BlockingIncubation):
    """Simulates the local HTTP wait ending after Dify accepted the run."""

    def execute(
        self,
        command: IncubateDocumentInput,
        *,
        on_started: Callable[[str, str], None] | None = None,
    ) -> IncubationView:
        assert command == _command()
        self.execute_count += 1
        assert on_started is not None
        on_started("DIFY-TASK-RECOVERY", "WORKFLOW-RECOVERY")
        self.finished.set()
        raise TimeoutError("local HTTP wait expired")


def _wait_for(
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("background incubation did not reach the expected state")


def test_start_returns_before_model_completion_and_duplicate_start_reuses_job(
    tmp_path: Path,
) -> None:
    """Catches synchronous execution and duplicate threads from repeated clicks."""
    coordinator_type = _coordinator_type()
    db_path = _database(tmp_path)
    incubation = BlockingIncubation(db_path)
    coordinator = coordinator_type(
        db_path=db_path,
        jobs=SqliteDocumentIncubationJobRepository(db_path),
        incubation=incubation,
        workflow_runs=RunDetails({"status": "running"}),
        now=lambda: NOW,
        job_id_factory=lambda: "JOB-LIVE",
    )

    first = coordinator.start(_command())

    assert first.status in {
        DocumentIncubationJobStatus.PENDING,
        DocumentIncubationJobStatus.RUNNING,
    }
    assert incubation.release.is_set() is False
    assert incubation.started.wait(timeout=1)
    second = coordinator.start(_command())
    assert second.id == first.id
    assert incubation.execute_count == 1

    incubation.release.set()
    _wait_for(
        lambda: coordinator.get_current("PROJECT_A").status is DocumentIncubationJobStatus.SUCCEEDED
    )
    result = coordinator.get_result(first.id)
    assert result is not None
    assert result.draft.id == "DRAFT-LIVE"


def test_restart_recovers_succeeded_dify_run_without_second_model_execution(
    tmp_path: Path,
) -> None:
    """Catches refresh recovery accidentally launching a second Dify workflow."""
    coordinator_type = _coordinator_type()
    db_path = _database(tmp_path)
    jobs = SqliteDocumentIncubationJobRepository(db_path)
    jobs.create(_job("JOB-RECOVERY"))
    jobs.mark_started(
        "JOB-RECOVERY",
        dify_task_id="DIFY-TASK-RECOVERY",
        workflow_run_id="WORKFLOW-RECOVERY",
        started_at=NOW,
    )
    incubation = BlockingIncubation(db_path)
    workflow_runs = RunDetails(
        {
            "workflow_run_id": "WORKFLOW-RECOVERY",
            "status": "succeeded",
            "result": {"document_markdown": "# 候选产品文档"},
        }
    )
    coordinator = coordinator_type(
        db_path=db_path,
        jobs=jobs,
        incubation=incubation,
        workflow_runs=workflow_runs,
        now=lambda: NOW,
        job_id_factory=lambda: "JOB-UNUSED",
    )

    recovered = coordinator.get_current("PROJECT_A")

    assert recovered is not None
    assert recovered.status is DocumentIncubationJobStatus.SUCCEEDED
    assert recovered.draft_id == "DRAFT-RECOVERY"
    assert incubation.execute_count == 0
    assert incubation.recovery_count == 1
    assert workflow_runs.calls == [("WORKFLOW-RECOVERY", "Henry")]
    assert coordinator.get_current("PROJECT_A") == recovered
    assert incubation.recovery_count == 1
    assert coordinator.get_result("JOB-RECOVERY").draft.id == "DRAFT-RECOVERY"


def test_local_timeout_after_dify_started_keeps_job_recoverable(
    tmp_path: Path,
) -> None:
    """Catches a local timeout incorrectly closing a still-running Dify workflow."""
    coordinator_type = _coordinator_type()
    db_path = _database(tmp_path)
    incubation = TimeoutAfterStartedIncubation(db_path)
    workflow_runs = RunDetails(
        {
            "workflow_run_id": "WORKFLOW-RECOVERY",
            "status": "succeeded",
            "result": {"document_markdown": "# 候选产品文档"},
        }
    )
    coordinator = coordinator_type(
        db_path=db_path,
        jobs=SqliteDocumentIncubationJobRepository(db_path),
        incubation=incubation,
        workflow_runs=workflow_runs,
        now=lambda: NOW,
        job_id_factory=lambda: "JOB-TIMEOUT-RECOVERY",
    )

    started = coordinator.start(_command())
    assert incubation.finished.wait(timeout=1)
    time.sleep(0.05)
    recovered = coordinator.get_current("PROJECT_A")

    assert recovered is not None
    assert recovered.id == started.id
    assert recovered.status is DocumentIncubationJobStatus.SUCCEEDED
    assert recovered.draft_id == "DRAFT-RECOVERY"
    assert incubation.execute_count == 1
    assert incubation.recovery_count == 1
    assert workflow_runs.calls == [("WORKFLOW-RECOVERY", "Henry")]


def test_failed_recovered_workflow_closes_job_and_allows_a_new_start(
    tmp_path: Path,
) -> None:
    """Catches failed remote runs leaving the project permanently locked."""
    coordinator_type = _coordinator_type()
    db_path = _database(tmp_path)
    jobs = SqliteDocumentIncubationJobRepository(db_path)
    jobs.create(_job("JOB-FAILED"))
    jobs.mark_started(
        "JOB-FAILED",
        dify_task_id="DIFY-TASK-FAILED",
        workflow_run_id="WORKFLOW-FAILED",
        started_at=NOW,
    )
    incubation = BlockingIncubation(db_path)
    ids = iter(("JOB-RETRY", "JOB-UNUSED"))
    coordinator = coordinator_type(
        db_path=db_path,
        jobs=jobs,
        incubation=incubation,
        workflow_runs=RunDetails({"workflow_run_id": "WORKFLOW-FAILED", "status": "failed"}),
        now=lambda: NOW,
        job_id_factory=lambda: next(ids),
    )

    failed = coordinator.get_current("PROJECT_A")

    assert failed is not None
    assert failed.status is DocumentIncubationJobStatus.FAILED
    assert failed.error_code == "DOCUMENT_INCUBATION_WORKFLOW_FAILED"
    retry = coordinator.start(_command())
    assert retry.id == "JOB-RETRY"
    assert incubation.started.wait(timeout=1)
    incubation.release.set()
    _wait_for(
        lambda: coordinator.get_current("PROJECT_A").status is DocumentIncubationJobStatus.SUCCEEDED
    )


def test_pending_job_without_dify_identifiers_expires_after_startup_grace(
    tmp_path: Path,
) -> None:
    """Catches a crash-before-callback leaving a pending task active forever."""
    coordinator_type = _coordinator_type()
    db_path = _database(tmp_path)
    jobs = SqliteDocumentIncubationJobRepository(db_path)
    jobs.create(_job("JOB-ORPHAN", created_at=NOW - timedelta(seconds=31)))
    coordinator = coordinator_type(
        db_path=db_path,
        jobs=jobs,
        incubation=BlockingIncubation(db_path),
        workflow_runs=RunDetails({"status": "running"}),
        now=lambda: NOW,
        job_id_factory=lambda: "JOB-UNUSED",
        startup_grace_seconds=30,
    )

    expired = coordinator.get_current("PROJECT_A")

    assert expired is not None
    assert expired.status is DocumentIncubationJobStatus.FAILED
    assert expired.error_code == "DOCUMENT_INCUBATION_START_TIMEOUT"


def test_restart_keeps_remote_running_job_active_without_restarting_it(
    tmp_path: Path,
) -> None:
    """Catches polling a live Dify run being mistaken for terminal failure."""
    coordinator_type = _coordinator_type()
    db_path = _database(tmp_path)
    jobs = SqliteDocumentIncubationJobRepository(db_path)
    jobs.create(_job("JOB-RUNNING"))
    running = jobs.mark_started(
        "JOB-RUNNING",
        dify_task_id="DIFY-TASK-RUNNING",
        workflow_run_id="WORKFLOW-RUNNING",
        started_at=NOW,
    )
    incubation = BlockingIncubation(db_path)
    coordinator = coordinator_type(
        db_path=db_path,
        jobs=jobs,
        incubation=incubation,
        workflow_runs=RunDetails({"workflow_run_id": "WORKFLOW-RUNNING", "status": "running"}),
        now=lambda: NOW,
        job_id_factory=lambda: "JOB-UNUSED",
    )

    assert coordinator.get_current("PROJECT_A") == running
    assert incubation.execute_count == 0
    assert incubation.recovery_count == 0
