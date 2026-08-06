from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from filelock import FileLock, Timeout

from src.application.dto.release import PublishBaselineInput
from src.application.ports.dashboard import ManifestIntegrity
from src.application.ports.repositories import (
    BaselineRepository,
    ChangeRepository,
    IssueRepository,
    ReleaseUnitOfWork,
    SourceRepository,
)
from src.application.use_cases.baseline_citations import build_baseline_citations
from src.domain.enums import BaselineStatus, KnowledgeStatus
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import (
    Baseline,
    BaselineManifest,
    ChangeRequest,
    EventLog,
    IssueEvidence,
    KnowledgeCard,
    Relation,
    RepairResult,
    SourceRecord,
)
from src.domain.policies.authority_policy import ensure_formal_baseline_source
from src.domain.policies.release_policy import ReleasePolicy
from src.infrastructure.files.manifest_store import (
    ManifestDurabilityUncertainError,
    ManifestStore,
)
from src.infrastructure.files.markdown_store import RELEASE_ROOT, MarkdownStore
from src.infrastructure.files.query_material_reader import VerifiedQueryMaterial


class SourceMaterialReader(Protocol):
    """受控材料读取端口：复用查询侧的路径、SHA-256、大小与片段校验。"""

    def read_source(self, source: SourceRecord) -> VerifiedQueryMaterial: ...

    def read_baseline(
        self,
        *,
        project_id: str,
        asset_id: str,
        version: str,
        relative_path: str,
        expected_sha256: str,
    ) -> VerifiedQueryMaterial: ...


class Reconciliation(Protocol):
    def rebuild_current_from_manifest(self) -> RepairResult: ...


class ReleaseGuardState(Protocol):
    @property
    def is_blocked(self) -> bool: ...

    @property
    def reason(self) -> str | None: ...

    def block(self, reason: str) -> None: ...


class PublishBaseline:
    """Atomically publish an approved change into a new effective baseline."""

    def __init__(
        self,
        *,
        manifest_store: ManifestStore,
        markdown_store: MarkdownStore,
        changes: ChangeRepository,
        baselines: BaselineRepository,
        sources: SourceRepository,
        issues: IssueRepository,
        integrity: ManifestIntegrity,
        material_reader: SourceMaterialReader,
        release_uow: ReleaseUnitOfWork,
        reconciliation: Reconciliation,
        guard: ReleaseGuardState,
        lock_path: Path,
        now: Callable[[], datetime],
        policy: ReleasePolicy | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.manifest_store = manifest_store
        self.markdown_store = markdown_store
        self.changes = changes
        self.baselines = baselines
        self.sources = sources
        self.issues = issues
        self.integrity = integrity
        self.material_reader = material_reader
        self.release_uow = release_uow
        self.reconciliation = reconciliation
        self.guard = guard
        self.lock_path = lock_path
        self.now = now
        self.policy = policy or ReleasePolicy()
        self.event_id_factory = event_id_factory or (lambda: f"EVENT-{uuid4().hex.upper()}")

    def execute(self, command: PublishBaselineInput) -> Baseline:
        if self.guard.is_blocked:
            raise DomainError(
                ErrorCode.RELEASE_BLOCKED,
                self.guard.reason or "manifest_sqlite_mismatch",
            )
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(self.lock_path))
        try:
            with lock.acquire(timeout=0):
                return self._publish_locked(command)
        except Timeout as error:
            raise DomainError(ErrorCode.RELEASE_LOCKED) from error

    def _publish_locked(self, command: PublishBaselineInput) -> Baseline:
        try:
            snapshot = self.manifest_store.read_snapshot()
        except ValueError as error:
            raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED, "MANIFEST_INVALID") from error
        current = snapshot.manifest
        manifest_integrity_ok = self.integrity.validate(current)
        try:
            change = self.changes.get(command.change_request_id)
        except KeyError as error:
            raise DomainError(ErrorCode.RELEASE_CHANGE_MISMATCH, "CHANGE_NOT_FOUND") from error
        self.policy.validate(
            command,
            current,
            change,
            target_version_exists=self.markdown_store.release_dir_exists(change.target_version),
            manifest_integrity_ok=manifest_integrity_ok,
        )
        approved_by = (change.reviewed_by or "").strip()
        if not approved_by or command.approved_by.strip() != approved_by:
            raise DomainError(ErrorCode.RELEASE_CHANGE_MISMATCH, "APPROVER_MISMATCH")
        parent_cards = self.markdown_store.read_cards(current.card_snapshot_path)
        self._validate_formal_sources(command, change, parent_cards, current)
        published_at = self.now()
        temp_dir: Path | None = None
        final_dir: Path | None = None
        manifest_replaced = False
        try:
            temp_dir = self.markdown_store.create_release_temp_dir()
            self.markdown_store.build_release_full_document(
                current.full_document_path,
                change,
                temp_dir,
                parent_version=current.current_version,
            )
            new_cards = self.markdown_store.build_release_cards(
                current.card_snapshot_path,
                change,
                temp_dir,
                parent_version=current.current_version,
                updated_at=published_at,
            )
            self.markdown_store.write_release_metadata(
                temp_dir,
                change=change,
                parent_version=current.current_version,
                approved_by=approved_by,
                published_at=published_at,
                release_note=command.release_note.strip(),
            )
            expected_dir = RELEASE_ROOT / change.target_version
            candidate = self.manifest_store.build_candidate(
                current=current,
                change=change,
                approved_by=approved_by,
                published_at=published_at,
                full_document_path=str(expected_dir / "full.md"),
                card_snapshot_path=str(expected_dir / "cards.json"),
                full_document_sha256=_sha256(temp_dir / "full.md"),
                card_snapshot_sha256=_sha256(temp_dir / "cards.json"),
            )
            self.manifest_store.validate_candidate(
                candidate,
                current=current,
                staging_dir=temp_dir,
            )
            final_dir = self.markdown_store.commit_release_dir(temp_dir, change.target_version)
            temp_dir = None
            try:
                self.manifest_store.atomic_replace(candidate)
                manifest_replaced = True
            except ManifestDurabilityUncertainError as error:
                replaced = self._confirm_replaced(candidate)
                if replaced is None:
                    final_dir = None
                    raise DomainError(
                        ErrorCode.BASELINE_INTEGRITY_FAILED,
                        "MANIFEST_STATE_UNKNOWN",
                    ) from error
                if not replaced:
                    self._quarantine_quietly(final_dir)
                    final_dir = None
                    raise DomainError(
                        ErrorCode.RELEASE_FAILED,
                        "MANIFEST_REPLACE_UNCERTAIN",
                    ) from error
                manifest_replaced = True
            new_snapshot = self.manifest_store.read_snapshot()
            new_baseline = Baseline(
                id=candidate.current_baseline_id,
                project_id=candidate.project_id,
                version=candidate.current_version,
                parent_baseline_id=candidate.parent_baseline_id,
                status=BaselineStatus.EFFECTIVE,
                full_document_path=candidate.full_document_path,
                card_snapshot_path=candidate.card_snapshot_path,
                manifest_sha256=new_snapshot.sha256,
                full_document_sha256=candidate.full_document_sha256,
                card_snapshot_sha256=candidate.card_snapshot_sha256,
                change_request_id=change.id,
                approved_by=approved_by,
                effective_at=published_at,
                created_at=published_at,
            )
            publish_relations = [
                Relation(
                    id=f"REL-{change.id}-APPROVED-AS-{new_baseline.id}",
                    project_id=command.project_id,
                    source_id=change.id,
                    relation_type="approved_as",
                    target_id=new_baseline.id,
                    source_ref=None,
                    created_at=published_at,
                ),
                Relation(
                    id=f"REL-{new_baseline.id}-SUPERSEDES-{current.current_baseline_id}",
                    project_id=command.project_id,
                    source_id=new_baseline.id,
                    relation_type="supersedes",
                    target_id=current.current_baseline_id,
                    source_ref=None,
                    created_at=published_at,
                ),
            ]
            event = EventLog(
                id=self.event_id_factory(),
                project_id=command.project_id,
                event_type="baseline_published",
                entity_type="baseline",
                entity_id=new_baseline.id,
                actor=approved_by,
                correlation_id=f"RELEASE-{change.id}",
                payload={
                    "project_id": command.project_id,
                    "version": candidate.current_version,
                    "parent_version": current.current_version,
                    "change_request_id": change.id,
                    "target_card_id": change.target_card_id,
                    "approved_by": approved_by,
                    "release_note": command.release_note.strip(),
                },
                created_at=published_at,
            )
            try:
                self.release_uow.publish(
                    superseded_baseline_id=current.current_baseline_id,
                    new_baseline=new_baseline,
                    change_id=change.id,
                    change_updated_at=published_at,
                    project_id=command.project_id,
                    event=event,
                    new_cards=new_cards,
                    relations=publish_relations,
                    parent_full_document_sha256=current.full_document_sha256,
                    parent_card_snapshot_sha256=current.card_snapshot_sha256,
                )
            except Exception as mirror_error:
                repair = self.reconciliation.rebuild_current_from_manifest()
                if not repair.success:
                    self.guard.block(repair.error_code or "manifest_sqlite_mismatch")
                    raise DomainError(ErrorCode.RELEASE_MIRROR_REPAIR_REQUIRED) from mirror_error
            return new_baseline
        except Exception:
            if final_dir is not None and not manifest_replaced:
                self._quarantine_quietly(final_dir)
            else:
                self.markdown_store.discard_temp_dir_if_exists(temp_dir)
            raise

    def _validate_formal_sources(
        self,
        command: PublishBaselineInput,
        change: ChangeRequest,
        parent_cards: list[KnowledgeCard],
        current: BaselineManifest,
    ) -> None:
        """Fail closed unless formal references resolve and citations stay locatable.

        Runs before any release directory is created: archive path/SHA-256/size and
        citation/chunk location are re-verified through the controlled material reader.
        """
        for card in parent_cards:
            if card.status != KnowledgeStatus.EFFECTIVE:
                continue
            if not card.source_refs:
                raise DomainError(
                    ErrorCode.CITATION_INVALID,
                    f"PUBLISH_CARD_SOURCE_REQUIRED:{card.id}",
                )
            locatable_citation = False
            for reference in card.source_refs:
                source_id, citation_id = _parse_source_ref(reference, card.id)
                source = self._require_formal_source(command.project_id, source_id)
                if citation_id is None:
                    # 裸 SOURCE_ID 只保留为补充来源关联，不计入正式证据门槛。
                    continue
                material = self._read_source_material(source)
                if not any(fragment.fragment_id == citation_id for fragment in material.fragments):
                    raise DomainError(
                        ErrorCode.PUBLISH_CITATION_UNVERIFIABLE,
                        f"PUBLISH_CITATION_UNVERIFIABLE:{card.id}",
                    )
                locatable_citation = True
            if not locatable_citation:
                raise DomainError(
                    ErrorCode.PUBLISH_CITATION_UNVERIFIABLE,
                    f"PUBLISH_CARD_CITATION_REQUIRED:{card.id}",
                )
        try:
            issue = self.issues.get(change.issue_id)
        except KeyError as error:
            raise DomainError(ErrorCode.RELEASE_CHANGE_MISMATCH, "ISSUE_NOT_FOUND") from error
        if issue.project_id != command.project_id:
            raise DomainError(ErrorCode.RELEASE_PROJECT_MISMATCH, "ISSUE_PROJECT_MISMATCH")
        for citation_id in dict.fromkeys(change.evidence_refs):
            matches = [item for item in issue.evidence if item.citation_id == citation_id]
            if not matches:
                raise DomainError(ErrorCode.CITATION_INVALID, "PUBLISH_EVIDENCE_NOT_IN_ISSUE")
            if len(matches) > 1:
                raise DomainError(
                    ErrorCode.CITATION_INVALID,
                    f"PUBLISH_EVIDENCE_AMBIGUOUS:{citation_id}",
                )
            evidence = matches[0]
            if evidence.source_id == current.current_baseline_id:
                self._verify_baseline_evidence(command, change, current, parent_cards, evidence)
                continue
            source = self._require_formal_source(command.project_id, evidence.source_id)
            material = self._read_source_material(source)
            fragment = next(
                (item for item in material.fragments if item.fragment_id == citation_id),
                None,
            )
            if (
                fragment is None
                or evidence.document_version != material.document_version
                or evidence.page_or_section != fragment.locator
                or evidence.excerpt not in fragment.text
            ):
                raise DomainError(
                    ErrorCode.PUBLISH_CITATION_UNVERIFIABLE,
                    f"PUBLISH_EVIDENCE_CITATION_UNVERIFIABLE:{citation_id}",
                )

    def _verify_baseline_evidence(
        self,
        command: PublishBaselineInput,
        change: ChangeRequest,
        current: BaselineManifest,
        parent_cards: list[KnowledgeCard],
        evidence: IssueEvidence,
    ) -> None:
        """Verify an evidence item that cites the current baseline full document.

        The citation ID must be a real identity generated from the Manifest-pointed
        card snapshot (shared rule with run_lint), and version/locator/excerpt must
        equal that mapping entry field-by-field — a partial excerpt that is merely a
        substring of the fragment fails closed. Arbitrary citation IDs fail closed.

        受控关系规则（唯一允许的基线证据绑定）：基线侧证据只允许绑定本次变更的
        目标卡片，即 entry.card_id == change.target_card_id。受影响对象
        （impacted_objects）中的其他卡片不允许作为基线证据引用；其内容如确需
        进入证据链，必须以正式来源证据（source_id 指向受控来源归档）形式提交。
        当前实现不允许任何例外，fail closed。
        """
        material = self.material_reader.read_baseline(
            project_id=command.project_id,
            asset_id=current.current_baseline_id,
            version=current.current_version,
            relative_path=current.full_document_path,
            expected_sha256=current.full_document_sha256,
        )
        citations = build_baseline_citations(
            baseline_version=current.current_version,
            cards=parent_cards,
            fragments=material.fragments,
        )
        entry = next(
            (item for item in citations if item.citation_id == evidence.citation_id),
            None,
        )
        if entry is None:
            raise DomainError(
                ErrorCode.PUBLISH_CITATION_UNVERIFIABLE,
                f"PUBLISH_EVIDENCE_CITATION_UNVERIFIABLE:{evidence.citation_id}",
            )
        if entry.card_id != change.target_card_id:
            raise DomainError(
                ErrorCode.PUBLISH_CITATION_UNVERIFIABLE,
                f"PUBLISH_EVIDENCE_CARD_MISMATCH:{evidence.citation_id}",
            )
        if (
            evidence.document_version != entry.baseline_version
            or evidence.page_or_section != entry.locator
            or evidence.excerpt != entry.excerpt
        ):
            raise DomainError(
                ErrorCode.PUBLISH_CITATION_UNVERIFIABLE,
                f"PUBLISH_EVIDENCE_CITATION_UNVERIFIABLE:{evidence.citation_id}",
            )

    def _read_source_material(self, source: SourceRecord) -> VerifiedQueryMaterial:
        """Re-verify archive path/SHA-256/size through the controlled reader."""
        try:
            return self.material_reader.read_source(source)
        except DomainError as error:
            raise DomainError(
                ErrorCode.PUBLISH_SOURCE_INTEGRITY_FAILED,
                f"PUBLISH_SOURCE_INTEGRITY_FAILED:{source.id}",
            ) from error

    def _require_formal_source(self, project_id: str, source_id: str) -> SourceRecord:
        try:
            source = self.sources.get(source_id)
        except KeyError as error:
            raise DomainError(
                ErrorCode.CITATION_INVALID,
                f"PUBLISH_SOURCE_MISSING:{source_id}",
            ) from error
        if source.project_id != project_id:
            raise DomainError(
                ErrorCode.CITATION_INVALID,
                f"PUBLISH_SOURCE_PROJECT_MISMATCH:{source_id}",
            )
        if source.ingest_status != "completed":
            raise DomainError(
                ErrorCode.CITATION_INVALID,
                f"PUBLISH_SOURCE_NOT_IMPORTED:{source_id}",
            )
        ensure_formal_baseline_source(source)
        return source

    def _confirm_replaced(self, candidate: BaselineManifest) -> bool | None:
        try:
            post = self.manifest_store.read_snapshot().manifest
        except ValueError:
            return None
        return (
            post.current_baseline_id == candidate.current_baseline_id
            and post.current_version == candidate.current_version
        )

    def _quarantine_quietly(self, final_dir: Path | None) -> None:
        if final_dir is None:
            return
        try:
            self.markdown_store.quarantine_unreferenced_release(final_dir)
        except Exception:
            pass


def _parse_source_ref(reference: str, card_id: str) -> tuple[str, str | None]:
    """Parse SOURCE_ID or SOURCE_ID:CITATION_ID; every other shape fails closed."""
    head, separator, tail = reference.partition(":")
    source_id = head.strip()
    citation_id = tail.strip()
    if not source_id or (separator and not citation_id) or ":" in tail:
        raise DomainError(
            ErrorCode.CITATION_INVALID,
            f"PUBLISH_SOURCE_REF_INVALID:{card_id}",
        )
    return source_id, citation_id if separator else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
