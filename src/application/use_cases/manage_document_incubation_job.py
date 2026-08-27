from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, Thread, current_thread
from uuid import uuid4

from src.application.dto.documents import IncubateDocumentInput, IncubationView
from src.application.ports.incubator import (
    DocumentIncubation,
    DocumentIncubationJobRepository,
    DocumentWorkflowRunReader,
)
from src.domain.enums import DocumentIncubationJobStatus
from src.domain.incubator import DocumentIncubationJob

_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]*(?::[A-Z][A-Z0-9_]*)?$")
_REGISTRY_LOCK = Lock()
_THREADS: dict[str, tuple[str, Thread]] = {}
_RESULTS: dict[str, IncubationView] = {}


class DocumentIncubationCoordinator:
    """Own the lifecycle of one background incubation job per project."""

    def __init__(
        self,
        *,
        db_path: Path,
        jobs: DocumentIncubationJobRepository,
        incubation: DocumentIncubation,
        workflow_runs: DocumentWorkflowRunReader | None,
        now: Callable[[], datetime] | None = None,
        job_id_factory: Callable[[], str] | None = None,
        startup_grace_seconds: int = 30,
    ) -> None:
        self.db_path = db_path
        self.jobs = jobs
        self.incubation = incubation
        self.workflow_runs = workflow_runs
        self.now = now or (lambda: datetime.now(UTC))
        self.job_id_factory = job_id_factory or (lambda: f"JOB-{uuid4().hex.upper()}")
        self.startup_grace_seconds = startup_grace_seconds

    def start(self, command: IncubateDocumentInput) -> DocumentIncubationJob:
        key = self._registry_key(command.project_id)
        with _REGISTRY_LOCK:
            active = self.jobs.get_active(command.project_id)
            if active is not None:
                return active
            created_at = self.now()
            job = DocumentIncubationJob(
                id=self.job_id_factory(),
                project_id=command.project_id,
                source_ids=list(command.source_ids),
                requested_by=command.requested_by,
                status=DocumentIncubationJobStatus.PENDING,
                created_at=created_at,
                updated_at=created_at,
            )
            try:
                self.jobs.create(job)
            except sqlite3.IntegrityError:
                concurrent = self.jobs.get_active(command.project_id)
                if concurrent is None:
                    raise
                return concurrent
            worker = Thread(
                target=self._run_job,
                args=(job.id, command, key),
                name=f"document-incubation-{job.id}",
                daemon=True,
            )
            _THREADS[key] = (job.id, worker)
            worker.start()
            return self.jobs.get(job.id)

    def get_current(self, project_id: str) -> DocumentIncubationJob | None:
        active = self.jobs.get_active(project_id)
        if active is None:
            return self.jobs.get_latest(project_id)
        if self._has_live_worker(project_id, active.id):
            return active
        if active.status is DocumentIncubationJobStatus.PENDING:
            elapsed = (self.now() - active.created_at).total_seconds()
            if elapsed <= self.startup_grace_seconds:
                return active
            return self.jobs.mark_failed(
                active.id,
                error_code="DOCUMENT_INCUBATION_START_TIMEOUT",
                finished_at=self.now(),
            )
        return self._recover_running(active)

    def get_result(self, job_id: str) -> IncubationView | None:
        job = self.jobs.get(job_id)
        if job.status in {
            DocumentIncubationJobStatus.PENDING,
            DocumentIncubationJobStatus.RUNNING,
        }:
            self.get_current(job.project_id)
        with _REGISTRY_LOCK:
            return _RESULTS.get(job_id)

    def _run_job(
        self,
        job_id: str,
        command: IncubateDocumentInput,
        registry_key: str,
    ) -> None:
        def on_started(dify_task_id: str, workflow_run_id: str) -> None:
            self.jobs.mark_started(
                job_id,
                dify_task_id=dify_task_id,
                workflow_run_id=workflow_run_id,
                started_at=self.now(),
            )

        try:
            result = self.incubation.execute(command, on_started=on_started)
            with _REGISTRY_LOCK:
                _RESULTS[job_id] = result
            self.jobs.mark_succeeded(
                job_id,
                draft_id=result.draft.id,
                finished_at=self.now(),
            )
        except BaseException as error:
            current = self.jobs.get(job_id)
            if (
                current.status is DocumentIncubationJobStatus.RUNNING
                and current.workflow_run_id is not None
            ):
                return
            self.jobs.mark_failed(
                job_id,
                error_code=self._safe_error(error),
                finished_at=self.now(),
            )
        finally:
            with _REGISTRY_LOCK:
                registered = _THREADS.get(registry_key)
                if registered is not None and registered == (job_id, current_thread()):
                    _THREADS.pop(registry_key, None)

    def _recover_running(self, job: DocumentIncubationJob) -> DocumentIncubationJob:
        if self.workflow_runs is None or job.workflow_run_id is None:
            return job
        try:
            response = self.workflow_runs.get_run(
                workflow_run_id=job.workflow_run_id,
                user=job.requested_by,
            )
        except BaseException:
            return job
        status = response.get("status")
        if status == "succeeded":
            command = IncubateDocumentInput(
                project_id=job.project_id,
                source_ids=list(job.source_ids),
                requested_by=job.requested_by,
            )
            try:
                result = self.incubation.complete_from_workflow(command, response)
            except BaseException as error:
                return self.jobs.mark_failed(
                    job.id,
                    error_code=self._safe_error(error),
                    finished_at=self.now(),
                )
            with _REGISTRY_LOCK:
                _RESULTS[job.id] = result
            return self.jobs.mark_succeeded(
                job.id,
                draft_id=result.draft.id,
                finished_at=self.now(),
            )
        if status in {"failed", "stopped"}:
            return self.jobs.mark_failed(
                job.id,
                error_code="DOCUMENT_INCUBATION_WORKFLOW_FAILED",
                finished_at=self.now(),
            )
        return job

    def _has_live_worker(self, project_id: str, job_id: str) -> bool:
        with _REGISTRY_LOCK:
            registered = _THREADS.get(self._registry_key(project_id))
            return registered is not None and registered[0] == job_id and registered[1].is_alive()

    def _registry_key(self, project_id: str) -> str:
        return f"{self.db_path.resolve()}::{project_id}"

    @staticmethod
    def _safe_error(error: BaseException) -> str:
        code = str(getattr(error, "code", "DOCUMENT_INCUBATION_FAILED"))
        detail = getattr(error, "detail", None)
        candidates = [
            f"{code}:{detail}" if detail else None,
            code,
            "DOCUMENT_INCUBATION_FAILED",
        ]
        return next(
            candidate
            for candidate in candidates
            if candidate is not None and _SAFE_ERROR_CODE.fullmatch(candidate)
        )
