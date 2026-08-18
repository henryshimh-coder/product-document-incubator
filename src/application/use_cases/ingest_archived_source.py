from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import yaml

from src.application.dto.wiki_ingest import (
    IngestArchivedSourceInput,
    WikiIngestResultView,
)
from src.application.ports.repositories import SourceRepository
from src.application.ports.wiki_ingest import (
    WikiIngestGenerating,
    WikiIngestRunRepository,
)
from src.domain.enums import DocumentGenerationMode, SecurityLevel
from src.domain.errors import AppError, DomainError, ErrorCode
from src.domain.models import SourceRecord
from src.domain.wiki import (
    WikiChangeSet,
    WikiIngestRun,
    WikiIngestStatus,
    WikiPageChange,
)
from src.infrastructure.db.connection import connect
from src.infrastructure.files.extractor import extract_document_bytes
from src.infrastructure.files.project_audit_log import ProjectAuditLog
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.files.redactor import redact_text
from src.infrastructure.files.wiki_change_set_store import WikiTransactionCoordinator
from src.infrastructure.files.wiki_outbound_context import WikiOutboundContextBuilder
from src.infrastructure.files.wiki_validator import WikiValidator
from src.infrastructure.gateways._common import (
    create_outbound_safety_proof,
    new_workflow_task_id,
)
from src.infrastructure.gateways.schemas import (
    WikiIngestWorkflowInput,
    WikiIngestWorkflowOutput,
)

WIKI_SCHEMA_VERSION = "2.2"
MAX_OUTBOUND_SOURCE_CHUNKS = 3
_SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class IngestArchivedSource:
    """Turn one verified L1/L2 archive into a validated, atomic Wiki change set."""

    def __init__(
        self,
        *,
        paths: ProjectPaths,
        db_path: Path,
        sources: SourceRepository,
        runs: WikiIngestRunRepository,
        gateway: WikiIngestGenerating,
        customer_names: Iterable[str],
        strategy_terms: Iterable[str],
        financial_terms: Iterable[str],
        leader_names: Iterable[str],
        unpublished_decisions: Iterable[str],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.paths = paths
        self.db_path = db_path
        self.sources = sources
        self.runs = runs
        self.gateway = gateway
        self.customer_names = tuple(customer_names)
        self.strategy_terms = tuple(strategy_terms)
        self.financial_terms = tuple(financial_terms)
        self.leader_names = tuple(leader_names)
        self.unpublished_decisions = tuple(unpublished_decisions)
        self.now = now or (lambda: datetime.now(UTC))
        self.context = WikiOutboundContextBuilder(paths, sources)

    def execute(self, command: IngestArchivedSourceInput) -> WikiIngestResultView:
        source: SourceRecord | None = None
        run: WikiIngestRun | None = None
        try:
            # Fixed boundary order: resolve -> IDs -> Raw path/SHA -> status/idempotency.
            self._resolve_project(command.project_id)
            self._validate_ids(command)
            source = self._owned_source(command)
            raw_path, raw_payload = self._verified_raw(source)
            idempotency_key = self._idempotency_key(source)
            duplicate = self.runs.get_succeeded_by_idempotency(idempotency_key)
            if duplicate is not None:
                return self._duplicate_view(duplicate)
            self._validate_starting_status(source)
            run = self._begin_run(source, idempotency_key)

            # Authorization precedes extraction. No L3/L4 or unapproved source can
            # reach extraction, projection, proof creation, or the Gateway.
            self._authorize_external(source)
            extracted = extract_document_bytes(
                raw_payload,
                filename=source.original_filename,
                source_id=source.id,
            )
            chunks = self._redacted_chunks(source, extracted.chunks)
            related_topic_paths = self._related_topic_paths()
            projection = self.context.build(command.project_id, related_topic_paths)
            workflow_inputs = self._workflow_inputs(
                command,
                source,
                chunks,
                projection.safe_index_projection,
                [item.model_dump(mode="json") for item in projection.safe_related_topics],
            )
            safety_proof = create_outbound_safety_proof(
                WikiIngestWorkflowInput,
                workflow_inputs,
                security_level=source.security_level,
                customer_names=self.customer_names,
                strategy_terms=self.strategy_terms,
                financial_terms=self.financial_terms,
                leader_names=self.leader_names,
                unpublished_decisions=self.unpublished_decisions,
                source_total_chars=len(extracted.text),
            )
            wiki_authorization = self.context.authorize(
                workflow_inputs,
                related_topic_paths=related_topic_paths,
            )
            output = self.gateway.generate(
                workflow_inputs,
                safety_proof=safety_proof,
                wiki_authorization=wiki_authorization,
                user=command.project_id,
            )

            # Re-read immutable evidence after the external boundary and before any
            # Wiki compilation or transaction write.
            self._verify_same_raw(raw_path, source, raw_payload)
            committed_at = self.now()
            validator, change_set = self._compile_change_set(
                source=source,
                run=run,
                output=output,
                related_topic_paths=related_topic_paths,
                committed_at=committed_at,
            )
            validator.validate_change_set(change_set)
            self._verify_same_raw(raw_path, source, raw_payload)
            transaction = WikiTransactionCoordinator(
                paths=self.paths,
                db_path=self.db_path,
                validator=validator,
                clock=lambda: committed_at,
            )
            committed = transaction.commit(change_set)
            if committed.status != "committed":
                raise DomainError(ErrorCode.WIKI_TRANSACTION_FAILED)
            return WikiIngestResultView(
                source_id=source.id,
                status=WikiIngestStatus.INGESTED,
                source_page_path=change_set.source_page_path,
                topic_page_paths=change_set.topic_page_paths,
                conflict_count=change_set.conflict_count,
                evidence_gap_count=change_set.evidence_gap_count,
            )
        except Exception as error:
            error_code = self._safe_error_code(error)
            if source is not None and source.project_id == command.project_id:
                self._record_failure(source, run, error_code)
            if isinstance(error, AppError):
                raise
            raise DomainError(error_code) from None

    def _resolve_project(self, project_id: str) -> None:
        if project_id != self.paths.project_id:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "PROJECT_ID_MISMATCH")
        root = self.paths.project_root
        if root.is_symlink() or root.resolve() != root or not root.is_dir():
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "PROJECT_ROOT_INVALID")

    @staticmethod
    def _validate_ids(command: IngestArchivedSourceInput) -> None:
        if _SOURCE_ID.fullmatch(command.source_id) is None:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "SOURCE_ID_INVALID")

    def _owned_source(self, command: IngestArchivedSourceInput) -> SourceRecord:
        try:
            source = self.sources.get(command.source_id)
        except KeyError:
            raise DomainError(ErrorCode.NOT_FOUND, "SOURCE_NOT_FOUND") from None
        if source.project_id != command.project_id:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "SOURCE_PROJECT_MISMATCH")
        return source

    def _verified_raw(self, source: SourceRecord) -> tuple[Path, bytes]:
        archive = Path(source.archive_path)
        if (
            "\\" in source.archive_path
            or archive.as_posix() != source.archive_path
            or any(part in {"", ".", ".."} for part in archive.parts)
        ):
            raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "RAW_PATH_INVALID")
        lexical = archive if archive.is_absolute() else self.paths.project_root / archive
        raw_root = self.paths.raw_root
        resolved = lexical.resolve()
        if (
            self.paths.project_root.is_symlink()
            or raw_root.is_symlink()
            or raw_root.resolve() != raw_root
            or lexical.is_symlink()
            or resolved != lexical
            or not resolved.is_relative_to(raw_root)
            or not resolved.is_file()
        ):
            raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "RAW_PATH_INVALID")
        try:
            payload = resolved.read_bytes()
        except OSError:
            raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "RAW_READ_FAILED") from None
        if (
            len(payload) != source.size_bytes
            or hashlib.sha256(payload).hexdigest() != source.sha256
        ):
            raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "RAW_SHA256_MISMATCH")
        return resolved, payload

    @staticmethod
    def _idempotency_key(source: SourceRecord) -> str:
        material = f"{source.project_id}{source.id}{source.sha256}{WIKI_SCHEMA_VERSION}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_starting_status(source: SourceRecord) -> None:
        if source.ingest_status == WikiIngestStatus.PROCESSING:
            raise DomainError(ErrorCode.WIKI_INGEST_ALREADY_RUNNING)
        if source.ingest_status not in {
            WikiIngestStatus.PENDING,
            WikiIngestStatus.FAILED,
            WikiIngestStatus.REINGEST_RECOMMENDED,
        }:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "SOURCE_STATUS_INVALID")

    def _begin_run(self, source: SourceRecord, idempotency_key: str) -> WikiIngestRun:
        started_at = self.now()
        run = WikiIngestRun(
            id=new_workflow_task_id("RUN"),
            project_id=source.project_id,
            source_id=source.id,
            transaction_id=new_workflow_task_id("TXN"),
            idempotency_key=idempotency_key,
            schema_version=WIKI_SCHEMA_VERSION,
            generation_mode=DocumentGenerationMode.EXTERNAL_AI,
            status=WikiIngestStatus.PROCESSING,
            started_at=started_at,
        )
        processing = source.model_copy(
            update={
                "ingest_status": WikiIngestStatus.PROCESSING,
                "ingest_error_code": None,
                "generation_mode": DocumentGenerationMode.EXTERNAL_AI,
            }
        )
        existing = self._run_by_idempotency(idempotency_key)
        if existing is not None:
            if existing.status == WikiIngestStatus.PROCESSING:
                raise DomainError(ErrorCode.WIKI_INGEST_ALREADY_RUNNING)
            run = existing.model_copy(
                update={
                    "transaction_id": run.transaction_id,
                    "status": WikiIngestStatus.PROCESSING,
                    "source_page_path": None,
                    "topic_page_paths": [],
                    "result_digest": None,
                    "error_code": None,
                    "started_at": started_at,
                    "finished_at": None,
                }
            )
        self.sources.update(processing)
        try:
            if existing is None:
                self.runs.add(run)
            else:
                self.runs.update(run)
        except Exception:
            self.sources.update(source)
            raise
        return run

    def _run_by_idempotency(self, idempotency_key: str) -> WikiIngestRun | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT transaction_id FROM wiki_ingest_runs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return self.runs.get_by_transaction(str(row["transaction_id"]))

    def _authorize_external(self, source: SourceRecord) -> None:
        try:
            project = json.loads(
                (self.paths.system_root / "project.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            project = None
        allowed = all(
            (
                isinstance(project, dict),
                isinstance(project, dict) and project.get("project_id") == source.project_id,
                isinstance(project, dict) and project.get("allow_external_model") is True,
                source.security_level
                in {SecurityLevel.L1_PUBLIC_SIMULATED, SecurityLevel.L2_INTERNAL},
                source.is_redacted,
                source.allow_external_model,
                not source.is_sandbox
                or source.security_level == SecurityLevel.L1_PUBLIC_SIMULATED,
            )
        )
        if not allowed:
            raise DomainError(ErrorCode.WIKI_EXTERNAL_CALL_DENIED)

    def _redacted_chunks(self, source: SourceRecord, chunks: Sequence) -> list[dict[str, str]]:
        safe_chunks: list[dict[str, str]] = []
        for chunk in chunks[:MAX_OUTBOUND_SOURCE_CHUNKS]:
            redaction = redact_text(
                chunk.text,
                security_level=source.security_level,
                customer_names=self.customer_names,
                strategy_terms=self.strategy_terms,
                financial_terms=self.financial_terms,
                leader_names=self.leader_names,
                unpublished_decisions=self.unpublished_decisions,
            )
            if not redaction.safe_for_external_model or redaction.redacted_text != chunk.text:
                raise DomainError(ErrorCode.WIKI_EXTERNAL_CALL_DENIED, "REDACTION_REQUIRED")
            safe_chunks.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "locator": chunk.locator,
                    "text": redaction.redacted_text,
                }
            )
        if not safe_chunks:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "SOURCE_CHUNKS_REQUIRED")
        return safe_chunks

    def _related_topic_paths(self) -> list[str]:
        topic_root = self.paths.wiki_root / "topics"
        if not topic_root.is_dir():
            return []
        return [
            path.relative_to(self.paths.project_root).as_posix()
            for path in sorted(topic_root.glob("*.md"))
            if path.is_file() and not path.is_symlink()
        ]

    def _workflow_inputs(
        self,
        command: IngestArchivedSourceInput,
        source: SourceRecord,
        chunks: list[dict[str, str]],
        safe_index_projection: str,
        safe_related_topics: list[dict],
    ) -> dict:
        try:
            contract = (self.paths.schema_root / "ingest-contract.md").read_text(
                encoding="utf-8"
            ).strip()
        except (OSError, UnicodeError):
            raise DomainError(ErrorCode.WIKI_SCHEMA_MISSING) from None
        return {
            "schema_version": WIKI_SCHEMA_VERSION,
            "task_id": new_workflow_task_id("WIKI"),
            "project_id": command.project_id,
            "source": {
                "id": source.id,
                "source_type": source.source_type,
                "material_name": source.material_name
                or Path(source.original_filename).stem,
                "document_version": source.document_version,
                "document_date": source.document_date.isoformat(),
                "applicable_scope": source.applicable_baseline_version,
                "authority_level": source.authority_level.value,
                "security_level": source.security_level.value,
            },
            "source_chunks": chunks,
            "safe_index_projection": safe_index_projection,
            "safe_related_topics": safe_related_topics,
            "ingest_contract": contract,
        }

    def _compile_change_set(
        self,
        *,
        source: SourceRecord,
        run: WikiIngestRun,
        output: WikiIngestWorkflowOutput,
        related_topic_paths: list[str],
        committed_at: datetime,
    ) -> tuple[WikiValidator, WikiChangeSet]:
        update_paths, new_outputs = self._resolve_topic_targets(
            source,
            output,
            related_topic_paths,
        )
        validator = WikiValidator(
            self.paths,
            source,
            existing_topic_paths=update_paths,
            new_topic_count=len(new_outputs),
        )
        source_page_path = validator._source_page_path()
        topic_outputs = [
            topic
            for topic in output.topic_changes
            if topic.change_type == "update"
        ] + new_outputs
        topic_paths = list(validator.topic_page_paths)
        first_locator = self._first_source_locator(source)
        source_markdown = self._source_markdown(
            source,
            output.source_page_markdown,
            first_locator,
            committed_at,
        )
        result_digest = hashlib.sha256(
            json.dumps(
                output.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        contents: dict[str, str] = {source_page_path: source_markdown}
        for relative_path, topic in zip(topic_paths, topic_outputs, strict=True):
            contents[relative_path] = self._topic_markdown(
                source,
                topic,
                first_locator,
                committed_at,
            )
        contents["wiki/index.md"] = self._index_markdown(
            source,
            source_page_path,
            topic_paths,
        )
        contents["wiki/log.md"] = ProjectAuditLog.render_ingest(
            self._read_required("wiki/log.md"),
            transaction_id=run.transaction_id,
            idempotency_key=run.idempotency_key,
            source_id=source.id,
            committed_at=committed_at,
        )
        contents[".incubator/source-index.json"] = self._source_index_json(
            source,
            source_page_path,
            topic_paths,
            result_digest,
            committed_at,
        )
        changes = [self._page_change(path, markdown) for path, markdown in contents.items()]
        return validator, WikiChangeSet(
            transaction_id=run.transaction_id,
            project_id=source.project_id,
            source_id=source.id,
            idempotency_key=run.idempotency_key,
            schema_version=WIKI_SCHEMA_VERSION,
            generation_mode=DocumentGenerationMode.EXTERNAL_AI,
            page_changes=changes,
            source_page_path=source_page_path,
            topic_page_paths=topic_paths,
            conflict_count=len(output.conflicts),
            evidence_gap_count=len(output.evidence_gaps),
            result_digest=result_digest,
        )

    def _resolve_topic_targets(
        self,
        source: SourceRecord,
        output: WikiIngestWorkflowOutput,
        related_topic_paths: list[str],
    ) -> tuple[list[str], list]:
        topic_ids = [topic.topic_id for topic in output.topic_changes]
        if len(topic_ids) != len(set(topic_ids)):
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "TOPIC_ID_DUPLICATE")
        safe_existing: dict[str, str] = {}
        for relative_path in related_topic_paths:
            projection = self.context.build(source.project_id, [relative_path])
            if len(projection.safe_related_topics) == 1:
                safe_existing[projection.safe_related_topics[0].title] = relative_path
        update_paths: list[str] = []
        new_outputs = []
        allowed_source_ids = {source.id}
        for projected in self.context.build(
            source.project_id, related_topic_paths
        ).safe_related_topics:
            allowed_source_ids.update(projected.source_ids)
        for topic in output.topic_changes:
            if source.id not in topic.source_ids or not set(topic.source_ids).issubset(
                allowed_source_ids
            ):
                raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "TOPIC_SOURCE_UNAUTHORIZED")
            if topic.change_type == "update":
                relative_path = safe_existing.get(topic.topic_id)
                if relative_path is None or relative_path in update_paths:
                    raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "TOPIC_UPDATE_UNAUTHORIZED")
                update_paths.append(relative_path)
            else:
                new_outputs.append(topic)
        return update_paths, new_outputs

    def _source_markdown(
        self,
        source: SourceRecord,
        body: str,
        locator: str,
        committed_at: datetime,
    ) -> str:
        frontmatter = {
            "project_id": source.project_id,
            "source_id": source.id,
            "material_series_id": source.material_series_id or source.id,
            "material_version": source.document_version,
            "raw_path": self._relative_raw_path(source),
            "raw_sha256": source.sha256,
            "source_type": source.source_type,
            "authority_level": source.authority_level.value,
            "security_level": source.security_level.value,
            "schema_version": WIKI_SCHEMA_VERSION,
            "generation_mode": DocumentGenerationMode.EXTERNAL_AI.value,
            "ingested_at": committed_at.isoformat(),
        }
        title = source.material_name or Path(source.original_filename).stem
        return (
            f"---\n{yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()}\n"
            f"---\n# 来源：{title}\n\n{body.strip()}\n\n## 来源定位\n\n"
            f"- 归档来源【{source.id}：{locator}】"
        )

    @staticmethod
    def _topic_markdown(source, topic, locator: str, committed_at: datetime) -> str:
        frontmatter = {
            "page_type": "topic",
            "topic_id": topic.topic_id,
            "project_id": source.project_id,
            "updated_at": committed_at.isoformat(),
        }
        citations = " ".join(f"【{source_id}：{locator}】" for source_id in topic.source_ids)
        return (
            f"---\n{yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()}\n"
            f"---\n# 主题：{topic.title}\n\n"
            f"- {topic.markdown.strip()} {citations}"
        )

    def _index_markdown(
        self,
        source: SourceRecord,
        source_page_path: str,
        topic_paths: list[str],
    ) -> str:
        existing = self._read_required("wiki/index.md").rstrip()
        links = [
            f"- [[{source_page_path.removesuffix('.md')}]] · {source.id}",
            *(f"- [[{path.removesuffix('.md')}]]" for path in topic_paths),
        ]
        return f"{existing}\n\n## {source.id}\n\n" + "\n".join(links)

    def _source_index_json(
        self,
        source: SourceRecord,
        source_page_path: str,
        topic_paths: list[str],
        result_digest: str,
        committed_at: datetime,
    ) -> str:
        try:
            payload = json.loads(self._read_required(".incubator/source-index.json"))
        except json.JSONDecodeError:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "SOURCE_INDEX_INVALID") from None
        if (
            not isinstance(payload, dict)
            or payload.get("project_id") != source.project_id
            or not isinstance(payload.get("sources"), list)
        ):
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "SOURCE_INDEX_INVALID")
        updated = False
        for item in payload["sources"]:
            if isinstance(item, dict) and item.get("source_id") == source.id:
                item.update(
                    {
                        "ingest_status": WikiIngestStatus.INGESTED.value,
                        "ingest_schema_version": WIKI_SCHEMA_VERSION,
                        "ingested_at": committed_at.isoformat(),
                        "source_page_path": source_page_path,
                        "topic_page_paths": topic_paths,
                        "ingest_result_digest": result_digest,
                        "ingest_error_code": None,
                        "generation_mode": DocumentGenerationMode.EXTERNAL_AI.value,
                    }
                )
                updated = True
                break
        if not updated:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "SOURCE_INDEX_ENTRY_MISSING")
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _page_change(self, relative_path: str, markdown: str) -> WikiPageChange:
        normalized = markdown.strip()
        target = self.paths.project_root / relative_path
        before = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        return WikiPageChange(
            relative_path=relative_path,
            operation="replace" if before is not None else "create",
            before_sha256=before,
            markdown=normalized,
            after_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        )

    def _read_required(self, relative_path: str) -> str:
        try:
            return (self.paths.project_root / relative_path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "WIKI_FILE_MISSING") from None

    def _relative_raw_path(self, source: SourceRecord) -> str:
        path = Path(source.archive_path)
        if path.is_absolute():
            path = path.relative_to(self.paths.project_root)
        return path.as_posix()

    def _first_source_locator(self, source: SourceRecord) -> str:
        _, payload = self._verified_raw(source)
        extracted = extract_document_bytes(
            payload,
            filename=source.original_filename,
            source_id=source.id,
        )
        return extracted.chunks[0].locator

    @staticmethod
    def _verify_same_raw(path: Path, source: SourceRecord, original: bytes) -> None:
        try:
            current = path.read_bytes()
        except OSError:
            raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "RAW_READ_FAILED") from None
        if current != original or hashlib.sha256(current).hexdigest() != source.sha256:
            raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "RAW_CHANGED")

    def _record_failure(
        self,
        source: SourceRecord,
        run: WikiIngestRun | None,
        error_code: str,
    ) -> None:
        try:
            current = self.sources.get(source.id)
            if current.ingest_status != WikiIngestStatus.INGESTED:
                self.sources.update(
                    current.model_copy(
                        update={
                            "ingest_status": WikiIngestStatus.FAILED,
                            "ingest_error_code": error_code,
                        }
                    )
                )
            if run is not None:
                persisted = self.runs.get_by_transaction(run.transaction_id) or run
                if persisted.status != WikiIngestStatus.INGESTED:
                    self.runs.update(
                        persisted.model_copy(
                            update={
                                "status": WikiIngestStatus.FAILED,
                                "error_code": error_code,
                                "finished_at": self.now(),
                            }
                        )
                    )
        except Exception:
            # The original safe application error remains authoritative. Recovery
            # will reconcile an interrupted run if failure persistence is uncertain.
            pass

    @staticmethod
    def _safe_error_code(error: Exception) -> str:
        if isinstance(error, AppError):
            return error.code
        if "WIKI_TRANSACTION_FAILED" in str(error):
            return ErrorCode.WIKI_TRANSACTION_FAILED.value
        return ErrorCode.WIKI_CHANGESET_INVALID.value

    @staticmethod
    def _duplicate_view(run: WikiIngestRun) -> WikiIngestResultView:
        return WikiIngestResultView(
            source_id=run.source_id,
            status=WikiIngestStatus.INGESTED,
            source_page_path=run.source_page_path,
            topic_page_paths=run.topic_page_paths,
            conflict_count=0,
            evidence_gap_count=0,
            duplicate=True,
        )
