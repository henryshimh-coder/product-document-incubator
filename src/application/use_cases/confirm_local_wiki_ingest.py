from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import yaml

from src.application.dto.wiki_ingest import (
    ConfirmLocalWikiIngestInput,
    WikiIngestResultView,
)
from src.application.ports.repositories import SourceRepository
from src.application.ports.wiki_ingest import WikiIngestRunRepository
from src.application.use_cases.prepare_local_wiki_ingest import (
    WIKI_SCHEMA_VERSION,
    _draft_root,
    _owned_source,
    _relative_raw_path,
    _require_existing_draft,
    _require_sensitive_source,
    _resolve_project,
    _validate_source_id,
    _verified_raw,
)
from src.domain.enums import DocumentGenerationMode
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import SourceRecord
from src.domain.wiki import WikiChangeSet, WikiIngestRun, WikiIngestStatus, WikiPageChange
from src.infrastructure.files.project_audit_log import ProjectAuditLog
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.files.source_index_store import SourceIndexStore
from src.infrastructure.files.wiki_change_set_store import WikiTransactionCoordinator
from src.infrastructure.files.wiki_validator import WikiValidator

_SOURCE_REQUIRED_HEADINGS = ("# 来源：", "## 来源摘要", "## 来源定位")
_TOPIC_REQUIRED_HEADINGS = (
    "# 主题：",
    "## 当前综合结论",
    "## 支持来源",
    "## 冲突来源",
    "## 待确认项",
)
CoordinatorFactory = Callable[[WikiValidator, datetime], WikiTransactionCoordinator]


class ConfirmLocalWikiIngest:
    """Validate and atomically commit an Owner-authored L3/L4 local Wiki draft."""

    def __init__(
        self,
        *,
        paths: ProjectPaths,
        db_path: Path,
        sources: SourceRepository,
        runs: WikiIngestRunRepository,
        now: Callable[[], datetime] | None = None,
        coordinator_factory: CoordinatorFactory | None = None,
    ) -> None:
        self.paths = paths
        self.db_path = db_path
        self.sources = sources
        self.runs = runs
        self.now = now or (lambda: datetime.now(UTC))
        self.coordinator_factory = coordinator_factory
        self.index = SourceIndexStore(paths)

    def execute(self, command: ConfirmLocalWikiIngestInput) -> WikiIngestResultView:
        _resolve_project(self.paths, command.project_id)
        _validate_source_id(command.source_id)
        source = _owned_source(self.sources, command.project_id, command.source_id)
        _require_sensitive_source(source)
        idempotency_key = self._idempotency_key(source)
        duplicate = self.runs.get_succeeded_by_idempotency(idempotency_key)
        if duplicate is not None:
            return WikiIngestResultView(
                source_id=source.id,
                status=WikiIngestStatus.INGESTED,
                source_page_path=duplicate.source_page_path,
                topic_page_paths=duplicate.topic_page_paths,
                conflict_count=0,
                evidence_gap_count=0,
                duplicate=True,
            )
        if source.ingest_status not in {
            WikiIngestStatus.LOCAL_REVIEW_REQUIRED,
            WikiIngestStatus.FAILED,
        }:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "SOURCE_STATUS_INVALID")
        _verified_raw(self.paths, source)
        draft_root = _draft_root(self.paths, source.id)
        _require_existing_draft(draft_root)
        source = self._restore_retryable_review_status(source)
        committed_at = self.now()
        validator, change_set = self._compile_change_set(
            source=source,
            draft_root=draft_root,
            committed_at=committed_at,
            idempotency_key=idempotency_key,
        )
        validator.validate_change_set(change_set)
        retry_run = self._reuse_failed_run(change_set, committed_at)
        try:
            transaction = self._transaction(validator, committed_at)
            committed = transaction.commit(change_set)
        except Exception as error:
            error_code = self._retryable_error_code(error)
            self._restore_retryable_failure(source.id, retry_run, error_code)
            if "WIKI_TRANSACTION_FAILED" in str(error):
                raise DomainError(ErrorCode.WIKI_TRANSACTION_FAILED) from None
            raise
        if committed.status != "committed":
            raise DomainError(ErrorCode.WIKI_TRANSACTION_FAILED)
        # Only a completed shared transaction can remove the Owner's draft.
        self._remove_draft(draft_root)
        return WikiIngestResultView(
            source_id=source.id,
            status=WikiIngestStatus.INGESTED,
            source_page_path=change_set.source_page_path,
            topic_page_paths=change_set.topic_page_paths,
            conflict_count=0,
            evidence_gap_count=0,
        )

    def _restore_retryable_review_status(self, source: SourceRecord) -> SourceRecord:
        if source.ingest_status != WikiIngestStatus.FAILED:
            return source
        retrying = source.model_copy(
            update={
                "ingest_status": WikiIngestStatus.LOCAL_REVIEW_REQUIRED,
                "ingest_error_code": None,
                "generation_mode": DocumentGenerationMode.LOCAL_MANUAL,
            }
        )
        self.sources.update(retrying)
        self.index.upsert(retrying)
        return retrying

    def _reuse_failed_run(
        self, change_set: WikiChangeSet, started_at: datetime
    ) -> WikiIngestRun | None:
        existing = self._run_by_idempotency(change_set.idempotency_key)
        if existing is None:
            return None
        if existing.status != WikiIngestStatus.FAILED:
            raise DomainError(ErrorCode.WIKI_INGEST_ALREADY_RUNNING)
        retry = existing.model_copy(
            update={
                "transaction_id": change_set.transaction_id,
                "status": WikiIngestStatus.PROCESSING,
                "source_page_path": None,
                "topic_page_paths": [],
                "result_digest": None,
                "error_code": None,
                "started_at": started_at,
                "finished_at": None,
            }
        )
        self.runs.update(retry)
        return retry

    def _run_by_idempotency(self, idempotency_key: str) -> WikiIngestRun | None:
        from src.infrastructure.db.connection import connect

        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT transaction_id FROM wiki_ingest_runs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return None if row is None else self.runs.get_by_transaction(str(row["transaction_id"]))

    def _transaction(
        self, validator: WikiValidator, committed_at: datetime
    ) -> WikiTransactionCoordinator:
        if self.coordinator_factory is not None:
            return self.coordinator_factory(validator, committed_at)
        return WikiTransactionCoordinator(
            paths=self.paths,
            db_path=self.db_path,
            validator=validator,
            clock=lambda: committed_at,
        )

    def _restore_retryable_failure(
        self,
        source_id: str,
        retry_run: WikiIngestRun | None,
        error_code: str,
    ) -> None:
        current = self.sources.get(source_id)
        if current.ingest_status != WikiIngestStatus.INGESTED:
            failed = current.model_copy(
                update={
                    "ingest_status": WikiIngestStatus.FAILED,
                    "ingest_error_code": error_code,
                    "generation_mode": DocumentGenerationMode.LOCAL_MANUAL,
                }
            )
            self.sources.update(failed)
            self.index.upsert(failed)
        if retry_run is None:
            return
        persisted_run = self.runs.get_by_transaction(retry_run.transaction_id)
        if persisted_run is not None and persisted_run.status != WikiIngestStatus.INGESTED:
            self.runs.update(
                persisted_run.model_copy(
                    update={
                        "status": WikiIngestStatus.FAILED,
                        "error_code": error_code,
                        "finished_at": self.now(),
                    }
                )
            )

    @staticmethod
    def _retryable_error_code(error: Exception) -> str:
        if isinstance(error, DomainError):
            return error.code
        if "WIKI_TRANSACTION_FAILED" in str(error):
            return ErrorCode.WIKI_TRANSACTION_FAILED.value
        return ErrorCode.WIKI_CHANGESET_INVALID.value

    @staticmethod
    def _idempotency_key(source: SourceRecord) -> str:
        material = (
            f"{source.project_id}{source.id}{source.sha256}{WIKI_SCHEMA_VERSION}"
            f"{DocumentGenerationMode.LOCAL_MANUAL.value}"
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _compile_change_set(
        self,
        *,
        source: SourceRecord,
        draft_root: Path,
        committed_at: datetime,
        idempotency_key: str,
    ) -> tuple[WikiValidator, WikiChangeSet]:
        source_markdown = self._source_markdown(source, draft_root / "source.md", committed_at)
        existing_paths, new_drafts = self._topic_targets(draft_root, source)
        validator = WikiValidator(
            self.paths,
            source,
            existing_topic_paths=existing_paths,
            new_topic_count=len(new_drafts),
        )
        source_page_path = validator._source_page_path()
        contents: dict[str, str] = {source_page_path: source_markdown}
        for target, draft_path in zip(
            validator.topic_page_paths,
            [*self._existing_draft_paths(draft_root, existing_paths), *new_drafts],
            strict=True,
        ):
            contents[target] = self._topic_markdown(source, draft_path)
        topic_paths = list(validator.topic_page_paths)
        contents["wiki/index.md"] = self._index_markdown(source, source_page_path, topic_paths)
        transaction_id = self._transaction_id(source, committed_at)
        contents["wiki/log.md"] = ProjectAuditLog.render_ingest(
            self._read_required("wiki/log.md"),
            transaction_id=transaction_id,
            idempotency_key=idempotency_key,
            source_id=source.id,
            committed_at=committed_at,
        )
        result_digest = self._result_digest(contents)
        contents[".incubator/source-index.json"] = self._source_index_json(
            source,
            source_page_path,
            topic_paths,
            result_digest,
            committed_at,
        )
        changes = [self._page_change(path, content) for path, content in contents.items()]
        return validator, WikiChangeSet(
            transaction_id=transaction_id,
            project_id=source.project_id,
            source_id=source.id,
            raw_path=_relative_raw_path(self.paths, source),
            raw_sha256=source.sha256,
            raw_size_bytes=source.size_bytes,
            idempotency_key=idempotency_key,
            schema_version=WIKI_SCHEMA_VERSION,
            generation_mode=DocumentGenerationMode.LOCAL_MANUAL,
            page_changes=changes,
            source_page_path=source_page_path,
            topic_page_paths=topic_paths,
            conflict_count=0,
            evidence_gap_count=0,
            result_digest=result_digest,
        )

    def _source_markdown(self, source: SourceRecord, path: Path, committed_at: datetime) -> str:
        markdown = self._read_draft_markdown(path)
        _require_headings(markdown, _SOURCE_REQUIRED_HEADINGS, "LOCAL_SOURCE_SECTIONS_REQUIRED")
        _require_section_content(markdown, "## 来源摘要", "LOCAL_SOURCE_SUMMARY_REQUIRED")
        _require_section_content(markdown, "## 来源定位", "LOCAL_SOURCE_LOCATOR_REQUIRED")
        frontmatter, body = _split_frontmatter(markdown)
        expected = {
            "project_id": source.project_id,
            "source_id": source.id,
            "material_series_id": source.material_series_id or source.id,
            "material_version": source.document_version,
            "raw_path": _relative_raw_path(self.paths, source),
            "raw_sha256": source.sha256,
            "source_type": source.source_type,
            "authority_level": source.authority_level.value,
            "security_level": source.security_level.value,
            "schema_version": WIKI_SCHEMA_VERSION,
            "generation_mode": DocumentGenerationMode.LOCAL_MANUAL.value,
        }
        for key, value in expected.items():
            if frontmatter.get(key) != value:
                raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, f"LOCAL_SOURCE_{key.upper()}")
        if not isinstance(frontmatter.get("ingested_at"), str):
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_SOURCE_INGESTED_AT")
        frontmatter["ingested_at"] = committed_at.isoformat()
        return self._render_frontmatter(frontmatter, body)

    def _topic_targets(
        self, draft_root: Path, source: SourceRecord
    ) -> tuple[list[str], list[Path]]:
        topics_root = draft_root / "topics"
        existing_paths: list[str] = []
        new_drafts: list[Path] = []
        for path in sorted(topics_root.iterdir(), key=lambda item: item.name):
            if path.is_symlink() or not path.is_file() or path.suffix != ".md":
                raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_TOPIC_PATH_INVALID")
            self._topic_markdown(source, path)
            existing = self.paths.wiki_root / "topics" / path.name
            if existing.exists():
                if existing.is_symlink() or not existing.is_file():
                    raise DomainError(
                        ErrorCode.WIKI_CHANGESET_INVALID,
                        "LOCAL_TOPIC_TARGET_INVALID",
                    )
                existing_paths.append(existing.relative_to(self.paths.project_root).as_posix())
            else:
                new_drafts.append(path)
        return existing_paths, new_drafts

    @staticmethod
    def _existing_draft_paths(draft_root: Path, existing_paths: list[str]) -> list[Path]:
        return [draft_root / "topics" / Path(path).name for path in existing_paths]

    def _topic_markdown(self, source: SourceRecord, path: Path) -> str:
        markdown = self._read_draft_markdown(path)
        _require_headings(markdown, _TOPIC_REQUIRED_HEADINGS, "LOCAL_TOPIC_SECTIONS_REQUIRED")
        _require_section_content(markdown, "## 当前综合结论", "LOCAL_TOPIC_CONCLUSION_REQUIRED")
        _require_section_content(markdown, "## 支持来源", "LOCAL_TOPIC_SUPPORT_REQUIRED")
        frontmatter, _ = _split_frontmatter(markdown)
        if frontmatter.get("page_type") != "topic":
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_TOPIC_PAGE_TYPE")
        if frontmatter.get("project_id") != source.project_id:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_TOPIC_PROJECT_ID")
        topic_id = frontmatter.get("topic_id")
        if not isinstance(topic_id, str) or not topic_id.strip():
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_TOPIC_ID")
        if f"【{source.id}：" not in markdown:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_TOPIC_SOURCE_REFERENCE")
        return markdown.strip()

    def _read_draft_markdown(self, path: Path) -> str:
        draft_root = _draft_root(
            self.paths,
            path.parent.parent.name if path.parent.name == "topics" else path.parent.name,
        )
        resolved = path.resolve()
        if (
            path.is_symlink()
            or resolved != path
            or not resolved.is_relative_to(draft_root.resolve())
            or not resolved.is_file()
        ):
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_DRAFT_PATH_INVALID")
        try:
            return resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_DRAFT_READ_FAILED") from None

    def _index_markdown(
        self, source: SourceRecord, source_page_path: str, topic_paths: list[str]
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
                        "generation_mode": DocumentGenerationMode.LOCAL_MANUAL.value,
                    }
                )
                return json.dumps(payload, ensure_ascii=False, indent=2)
        raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "SOURCE_INDEX_ENTRY_MISSING")

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

    @staticmethod
    def _transaction_id(source: SourceRecord, committed_at: datetime) -> str:
        stamp = committed_at.strftime("%Y%m%dT%H%M%S%f")
        return f"LOCAL-{source.id}-{stamp}"

    @staticmethod
    def _result_digest(contents: dict[str, str]) -> str:
        serialized = json.dumps(contents, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _render_frontmatter(frontmatter: dict, body: str) -> str:
        return (
            f"---\n{yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()}\n"
            f"---\n{body.strip()}"
        )

    @staticmethod
    def _remove_draft(draft_root: Path) -> None:
        import shutil

        if draft_root.is_symlink():
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_DRAFT_PATH_INVALID")
        shutil.rmtree(draft_root)


def _split_frontmatter(markdown: str) -> tuple[dict, str]:
    if not markdown.startswith("---\n"):
        raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_FRONTMATTER_MISSING")
    closing = markdown.find("\n---\n", len("---\n"))
    if closing < 0:
        raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_FRONTMATTER_INVALID")
    try:
        frontmatter = yaml.safe_load(markdown[len("---\n") : closing])
    except yaml.YAMLError:
        raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_FRONTMATTER_INVALID") from None
    if not isinstance(frontmatter, dict):
        raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_FRONTMATTER_INVALID")
    return frontmatter, markdown[closing + len("\n---\n") :]


def _require_headings(markdown: str, headings: tuple[str, ...], detail: str) -> None:
    if any(heading not in markdown for heading in headings):
        raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, detail)


def _require_section_content(markdown: str, heading: str, detail: str) -> None:
    start = markdown.find(heading)
    following = markdown.find("\n## ", start + len(heading))
    section = markdown[start + len(heading) : following if following >= 0 else None]
    if not section.strip():
        raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, detail)
