from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

import src.domain.enums as enums
import src.domain.incubator as incubator
import src.infrastructure.db.repositories as repositories
from src.infrastructure.db.migrations import migrate

NOW = datetime(2026, 8, 26, 1, 0, tzinfo=UTC)
STARTED = datetime(2026, 8, 26, 1, 1, tzinfo=UTC)
FINISHED = datetime(2026, 8, 26, 1, 2, tzinfo=UTC)


def _job(**overrides: object):
    values = {
        "id": "INCUBATION-001",
        "project_id": "PROJECT_A",
        "source_ids": ["SRC-001"],
        "requested_by": "Henry",
        "status": enums.DocumentIncubationJobStatus.PENDING,
        "dify_task_id": None,
        "workflow_run_id": None,
        "draft_id": None,
        "error_code": None,
        "created_at": NOW,
        "started_at": None,
        "updated_at": NOW,
        "finished_at": None,
    }
    values.update(overrides)
    return incubator.DocumentIncubationJob.model_validate(values)


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


def _insert_draft(db_path: Path, draft_id: str) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO document_drafts (
                id, project_id, version_id, display_version, parent_version_id, status,
                markdown_path, markdown_sha256, source_ids_json, section_citations_json,
                summary, missing_sections_json, evidence_gaps_json, created_at, updated_at,
                generation_mode
            ) VALUES (?, 'PROJECT_A', ?, 'V1.0', NULL, 'candidate_draft', ?, ?, '[]',
                      '[]', '候选产品文档', '[]', '[]', ?, ?, 'external_ai')
            """,
            (
                draft_id,
                f"VERSION-{draft_id}",
                f"wiki/candidates/{draft_id}.md",
                "0" * 64,
                FINISHED.isoformat(),
                FINISHED.isoformat(),
            ),
        )


def test_document_incubation_job_requires_at_least_one_source() -> None:
    """Catches background jobs that cannot prove which Wiki pages were selected."""
    with pytest.raises(ValidationError):
        _job(source_ids=[])


@pytest.mark.parametrize(
    ("status", "overrides"),
    [
        ("running", {}),
        ("succeeded", {"started_at": NOW, "finished_at": NOW}),
        ("failed", {"finished_at": NOW}),
    ],
)
def test_document_incubation_job_rejects_incomplete_lifecycle(
    status: str,
    overrides: dict[str, object],
) -> None:
    """Catches persisted states that cannot be resumed or explained safely."""
    with pytest.raises(ValidationError):
        _job(status=status, **overrides)


def test_document_incubation_job_rejects_unsafe_error_detail() -> None:
    """Catches raw Dify exception text leaking into the durable task record."""
    with pytest.raises(ValidationError):
        _job(
            status="failed",
            error_code="request failed: api-key=secret",
            finished_at=NOW,
        )


def test_repository_persists_and_lists_active_and_latest_job(tmp_path: Path) -> None:
    """Catches restarts losing the selected Wiki sources or active task identity."""
    db_path = _database(tmp_path)
    repository = repositories.SqliteDocumentIncubationJobRepository(db_path)
    job = _job()

    repository.create(job)

    assert repository.get(job.id) == job
    assert repository.get_active(job.project_id) == job
    assert repository.get_latest(job.project_id) == job


def test_repository_transitions_to_success_and_replays_same_draft_idempotently(
    tmp_path: Path,
) -> None:
    """Catches duplicate callbacks producing a second terminal outcome."""
    db_path = _database(tmp_path)
    _insert_draft(db_path, "DRAFT-001")
    _insert_draft(db_path, "DRAFT-002")
    repository = repositories.SqliteDocumentIncubationJobRepository(db_path)
    repository.create(_job())

    running = repository.mark_started(
        "INCUBATION-001",
        dify_task_id="DIFY-TASK-001",
        workflow_run_id="WORKFLOW-001",
        started_at=STARTED,
    )
    assert (
        repository.mark_started(
            "INCUBATION-001",
            dify_task_id="DIFY-TASK-001",
            workflow_run_id="WORKFLOW-001",
            started_at=STARTED,
        )
        == running
    )
    with pytest.raises(ValueError, match="DOCUMENT_INCUBATION_STATE_CONFLICT"):
        repository.mark_started(
            "INCUBATION-001",
            dify_task_id="DIFY-TASK-OTHER",
            workflow_run_id="WORKFLOW-001",
            started_at=STARTED,
        )
    succeeded = repository.mark_succeeded(
        "INCUBATION-001", draft_id="DRAFT-001", finished_at=FINISHED
    )

    assert running.status is enums.DocumentIncubationJobStatus.RUNNING
    assert running.dify_task_id == "DIFY-TASK-001"
    assert succeeded.status is enums.DocumentIncubationJobStatus.SUCCEEDED
    assert succeeded.draft_id == "DRAFT-001"
    assert repository.get_active("PROJECT_A") is None
    assert (
        repository.mark_succeeded("INCUBATION-001", draft_id="DRAFT-001", finished_at=FINISHED)
        == succeeded
    )
    with pytest.raises(ValueError, match="DOCUMENT_INCUBATION_STATE_CONFLICT"):
        repository.mark_succeeded("INCUBATION-001", draft_id="DRAFT-002", finished_at=FINISHED)


def test_repository_can_fail_pending_job_with_only_a_safe_error_code(tmp_path: Path) -> None:
    """Catches pre-start transport failures leaking raw exception detail into SQLite."""
    repository = repositories.SqliteDocumentIncubationJobRepository(_database(tmp_path))
    repository.create(_job())

    failed = repository.mark_failed(
        "INCUBATION-001",
        error_code="MODEL_TIMEOUT:DIFY_TIMEOUT",
        finished_at=FINISHED,
    )

    assert failed.status is enums.DocumentIncubationJobStatus.FAILED
    assert failed.error_code == "MODEL_TIMEOUT:DIFY_TIMEOUT"
    assert failed.started_at is None
    assert repository.get_active("PROJECT_A") is None

    repository.create(_job(id="INCUBATION-UNSAFE"))
    with pytest.raises(ValidationError):
        repository.mark_failed(
            "INCUBATION-UNSAFE",
            error_code="request failed: api-key=secret",
            finished_at=FINISHED,
        )


def test_two_connections_cannot_create_two_active_jobs_for_one_project(
    tmp_path: Path,
) -> None:
    """Catches two browser clicks racing past an application-only active-job check."""
    db_path = _database(tmp_path)
    barrier = Barrier(2)

    def create(job_id: str) -> bool:
        barrier.wait()
        try:
            repositories.SqliteDocumentIncubationJobRepository(db_path).create(_job(id=job_id))
        except sqlite3.IntegrityError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, ("INCUBATION-A", "INCUBATION-B")))

    assert sorted(results) == [False, True]
    active = repositories.SqliteDocumentIncubationJobRepository(db_path).get_active("PROJECT_A")
    assert active is not None
    assert active.id in {"INCUBATION-A", "INCUBATION-B"}
