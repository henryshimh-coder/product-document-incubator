"""T12 冻结缓存离线主流程集成测试（评审 T12-R10～R14）。

所有环境都从仓库已提交的 frozen 快照独立恢复到 ``tmp_path`` 空目录（不依赖
bootstrap 前置），HTTP factory 禁止一切网络请求。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest

from scripts.bootstrap_demo import BASELINE_VERSION, RULE_CARD_ID
from scripts.demo_materials import DEMO_QUESTION
from scripts.reset_demo import reset_demo
from scripts.snapshot_common import (
    CACHE_DIR_REL,
    DATABASE_REL,
    MANIFEST_REL,
    SNAPSHOT_TARGETS,
)
from src.application.container import AppContainer, build_container
from src.application.dto.decision import CreateChangeRequestInput, RecordDecisionInput
from src.application.dto.ingest import ImportSourceInput
from src.application.dto.lint import RunLintInput
from src.application.dto.query import RunQueryInput
from src.application.dto.release import PublishBaselineInput, ReviewChangeRequestInput
from src.domain.enums import (
    AuthorityLevel,
    CallResultMode,
    ChangeReviewAction,
    DecisionAction,
    SecurityLevel,
)
from src.domain.errors import DomainError, OutputValidationError
from src.infrastructure.cache.ai_cache import AiCache, CacheIdentity
from src.infrastructure.db.connection import connect
from src.infrastructure.files.manifest_store import ManifestStore

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "sources"
CONFIG_DIR = REPO_ROOT / "config"

OFFLINE_ENVIRON = {
    "DIFY_BASE_URL": "https://dify.offline.local",
    "DIFY_INGEST_API_KEY": "ingest-key",
    "DIFY_QUERY_API_KEY": "query-key",
    "DIFY_LINT_API_KEY": "lint-key",
}


def _forbidden_factory() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"NETWORK_FORBIDDEN:{request.url}")

    return httpx.Client(transport=httpx.MockTransport(handler))


def _restored_frozen_root(tmp_path: Path) -> Path:
    """从 frozen 快照独立恢复到空目录，不调用 bootstrap。"""
    root = tmp_path / "demo"
    root.mkdir()
    report = reset_demo("frozen", root)
    assert report.ok
    return root


def _offline_container(root: Path, monkeypatch: pytest.MonkeyPatch) -> AppContainer:
    shutil.copytree(CONFIG_DIR, root / "config", dirs_exist_ok=True)
    monkeypatch.chdir(root)
    container = build_container(
        root / "config" / "app.yaml",
        environ=OFFLINE_ENVIRON,
        http_factory=_forbidden_factory,
    )
    assert container.import_source is not None
    assert container.query is not None
    assert container.lint is not None
    return container


def _import_risk_cache(container: AppContainer) -> str:
    report = container.import_source.execute(
        ImportSourceInput(
            project_id="LLD",
            uploaded_name="风险意见.md",
            uploaded_bytes=(FIXTURES_DIR / "risk_opinion.md").read_bytes(),
            source_type="risk_opinion",
            authority_level=AuthorityLevel.FORMAL_DECISION,
            source_department="风险",
            provider=None,
            document_date=date(2026, 8, 4),
            document_version="v1.0",
            applicable_baseline_version=BASELINE_VERSION,
            security_level=SecurityLevel.L2_INTERNAL,
            is_redacted_confirmed=True,
            allow_external_model=True,
            is_sandbox=False,
            preferred_mode="cache",
        )
    )
    assert report.result_mode == CallResultMode.CACHE
    return report.source_id


def _query_cache(container: AppContainer, question: str = DEMO_QUESTION):
    return container.query.execute(
        RunQueryInput(
            project_id="LLD",
            question=question,
            scope="effective",
            preferred_mode="cache",
        )
    )


def _lint_cache(container: AppContainer, source_id: str):
    return container.lint.execute(
        RunLintInput(
            project_id="LLD",
            scope="current_plus_source",
            source_id=source_id,
            preferred_mode="cache",
        )
    )


def _model_call_log_count(root: Path) -> int:
    with connect(root / DATABASE_REL) as connection:
        return connection.execute("SELECT COUNT(*) FROM model_call_logs").fetchone()[0]


def _table_count(root: Path, table: str) -> int:
    with connect(root / DATABASE_REL) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _run_offline_main_flow(container: AppContainer) -> str:
    """断网缓存主流程：cache ingest → cache query → cache lint → 决定 → 批准 → 发布。"""
    risk_source_id = _import_risk_cache(container)
    query = _query_cache(container)
    assert query.answer == "当前目标客群是符合准入要求的存量客户。"
    lint = _lint_cache(container, risk_source_id)
    issue = next(item for item in lint.issues if item.target_rule_id == RULE_CARD_ID)
    challenging = next(e for e in issue.evidence if e.side.value == "challenging_source")
    decision = container.record_decision.execute(
        RecordDecisionInput(
            issue_id=issue.id,
            action=DecisionAction.ACCEPT_CHANGE,
            conclusion="采纳风险意见，收紧目标客群。",
            confirmed_by="产品经理",
            responsible_party="产品负责人",
            verification_condition="回归校验通过且审批完成。",
            idempotency_key="DECISION-OFFLINE-001",
            change_request=CreateChangeRequestInput(
                target_card_id=RULE_CARD_ID,
                before_content="当前目标客群是符合准入要求的存量客户。",
                after_content="目标客群收紧为符合准入要求且通过风险评估的存量客户。",
                rationale="依据正式风险意见和会议结论调整。",
                evidence_refs=[challenging.citation_id],
                impacted_objects=[RULE_CARD_ID],
                responsible_domain="产品",
                required_approver_role="产品经理",
                demo_confirmer="产品经理",
                target_version="LLD-724_2",
                effective_condition="审批通过且验证完成后发布。",
            ),
        )
    )
    change = decision.change_request
    assert change is not None
    reviewed = container.review_change_request.execute(
        ReviewChangeRequestInput(
            change_request_id=change.id,
            action=ChangeReviewAction.APPROVE,
            reviewed_by="产品经理",
            comment="已检查修改前后、依据、影响对象和目标版本。",
            idempotency_key="REVIEW-OFFLINE-001",
        )
    )
    assert reviewed.status.value == "approved"
    baseline = container.publish_baseline.execute(
        PublishBaselineInput(
            project_id="LLD",
            change_request_id=change.id,
            approved_by="产品经理",
            impact_reviewed=True,
            release_note="断网缓存主流程发布验证，保留版本差异与追溯依据。",
        )
    )
    return baseline.version


def _forge_cache_entry(root: Path, task_type: str, mutate) -> None:
    """一致性地篡改缓存文件与索引行（绕过 AiCache 自身完整性校验的伪造面）。"""
    with connect(root / DATABASE_REL) as connection:
        row = connection.execute(
            "SELECT cache_key, response_json FROM cache_entries WHERE task_type = ?",
            (task_type,),
        ).fetchone()
    payload = json.loads(row["response_json"])
    mutate(payload)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    (root / CACHE_DIR_REL / f"{row['cache_key']}.json").write_text(canonical, encoding="utf-8")
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with sqlite3.connect(root / DATABASE_REL) as connection:
        connection.execute(
            "UPDATE cache_entries SET response_json = ?, response_sha256 = ? WHERE cache_key = ?",
            (canonical, digest, row["cache_key"]),
        )


def _assert_schema_version_separates_cache_key(
    root: Path,
    task_type: str,
) -> None:
    """schema_version 参与缓存键：版本不同则键不同且必然 miss。"""
    with connect(root / DATABASE_REL) as connection:
        row = connection.execute(
            """
            SELECT task_type, source_sha256, baseline_version,
                   prompt_version, model_label, schema_version, project_id
            FROM cache_entries WHERE task_type = ?
            """,
            (task_type,),
        ).fetchone()
    base = CacheIdentity(**dict(row))
    forged = replace(base, schema_version="9.9")
    assert forged.cache_key != base.cache_key
    assert AiCache(root / DATABASE_REL).get(forged) is None


def test_query_and_lint_serve_from_exact_frozen_cache_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T12-R10：Query/Lint 命中完全匹配缓存返回 CACHE，零网络、零模型调用记录。"""
    root = _restored_frozen_root(tmp_path)
    container = _offline_container(root, monkeypatch)
    risk_source_id = _import_risk_cache(container)

    query = _query_cache(container)
    assert query.result_mode == CallResultMode.CACHE
    assert query.model_call_id is None
    assert query.cache_generated_at is not None
    assert query.baseline_version == BASELINE_VERSION

    lint = _lint_cache(container, risk_source_id)
    assert lint.result_mode == CallResultMode.CACHE
    assert lint.model_call_id is None
    assert lint.cache_generated_at is not None
    assert lint.semantic_count == 1
    assert any(issue.target_rule_id == RULE_CARD_ID for issue in lint.issues)

    assert _model_call_log_count(root) == 0


def test_offline_cache_full_story_flow_reaches_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T12-R13：断网主流程一路发布到 LLD-724_2，Manifest 落盘为新版本。"""
    root = _restored_frozen_root(tmp_path)
    container = _offline_container(root, monkeypatch)

    version = _run_offline_main_flow(container)

    assert version == "LLD-724_2"
    manifest = ManifestStore(root / MANIFEST_REL).read_and_validate()
    assert manifest.current_version == "LLD-724_2"
    assert _model_call_log_count(root) == 0


def test_cache_identity_fields_are_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T12-R11：question、source SHA、baseline、prompt、model、schema 任一不同即 miss。"""
    root = _restored_frozen_root(tmp_path)
    container = _offline_container(root, monkeypatch)
    risk_source_id = _import_risk_cache(container)
    assert container.query is not None and container.lint is not None

    with pytest.raises(DomainError, match="CACHE_NOT_FOUND"):
        _query_cache(container, question="另一个完全不同的问题？")

    for attribute, forged in (
        ("prompt_version", "query-v2"),
        ("model_label", "dify-query-v2"),
    ):
        original = getattr(container.query, attribute)
        setattr(container.query, attribute, forged)
        try:
            with pytest.raises(DomainError, match="CACHE_NOT_FOUND"):
                _query_cache(container)
        finally:
            setattr(container.query, attribute, original)

    # schema_version 受工作流输入契约的 Literal 保护，伪造值在缓存查找前就会
    # 被拒绝；在身份层验证键分离，证明不同 schema 版本绝不会命中旧缓存。
    _assert_schema_version_separates_cache_key(root, "query")

    for attribute, forged in (
        ("prompt_version", "lint-v2"),
        ("model_label", "dify-lint-v2"),
    ):
        original = getattr(container.lint, attribute)
        setattr(container.lint, attribute, forged)
        try:
            with pytest.raises(DomainError, match="CACHE_NOT_FOUND"):
                _lint_cache(container, risk_source_id)
        finally:
            setattr(container.lint, attribute, original)

    _assert_schema_version_separates_cache_key(root, "lint")

    # source SHA 不同：改动材料一字节，ingest 缓存身份立即 miss。
    tampered_bytes = (FIXTURES_DIR / "risk_opinion.md").read_bytes() + b"x"
    with pytest.raises(DomainError, match="CACHE_NOT_FOUND"):
        container.import_source.execute(
            ImportSourceInput(
                project_id="LLD",
                uploaded_name="风险意见.md",
                uploaded_bytes=tampered_bytes,
                source_type="risk_opinion",
                authority_level=AuthorityLevel.FORMAL_DECISION,
                source_department="风险",
                provider=None,
                document_date=date(2026, 8, 4),
                document_version="v1.0",
                applicable_baseline_version=BASELINE_VERSION,
                security_level=SecurityLevel.L2_INTERNAL,
                is_redacted_confirmed=True,
                allow_external_model=True,
                is_sandbox=False,
                preferred_mode="cache",
            )
        )

    # baseline 不同：发布 LLD-724_2 后原缓存身份不再匹配。
    assert _run_offline_main_flow(container) == "LLD-724_2"
    with pytest.raises(DomainError, match="CACHE_NOT_FOUND"):
        _query_cache(container)


def test_forged_query_cache_payload_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T12-R12：伪造缓存 citation 或版本，走与实时相同验证并 fail closed。"""
    root = _restored_frozen_root(tmp_path)
    container = _offline_container(root, monkeypatch)
    issues_before = _table_count(root, "issue_cards")
    relations_before = _table_count(root, "relations")
    decisions_before = _table_count(root, "decisions")

    _forge_cache_entry(
        root, "query", lambda payload: payload["citations"][0].update(id="CIT-FORGED")
    )
    # 与实时路径一致：citation 校验抛出 OutputValidationError。
    with pytest.raises(OutputValidationError, match="UNKNOWN_CITATION"):
        _query_cache(container)

    _forge_cache_entry(root, "query", lambda payload: payload.update(baseline_version="LLD-999_9"))
    with pytest.raises(OutputValidationError, match="BASELINE_VERSION_MISMATCH"):
        _query_cache(container)

    assert _table_count(root, "issue_cards") == issues_before
    assert _table_count(root, "relations") == relations_before
    assert _table_count(root, "decisions") == decisions_before
    assert _model_call_log_count(root) == 0


def test_forged_lint_cache_payload_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T12-R12：伪造缓存证据 citation/locator/版本，在任何领域写入前 fail closed。"""
    root = _restored_frozen_root(tmp_path)
    container = _offline_container(root, monkeypatch)
    risk_source_id = _import_risk_cache(container)
    issues_before = _table_count(root, "issue_cards")
    relations_before = _table_count(root, "relations")

    _forge_cache_entry(
        root,
        "lint",
        lambda payload: payload["issues"][0]["evidence"][1].update(citation_id="SRC-FAKE-0001"),
    )
    with pytest.raises(OutputValidationError, match="LINT_CACHE_EVIDENCE_UNKNOWN_CITATION"):
        _lint_cache(container, risk_source_id)

    _forge_cache_entry(
        root,
        "lint",
        lambda payload: payload["issues"][0]["evidence"][0].update(document_version="v9.9"),
    )
    with pytest.raises(OutputValidationError, match="LINT_CACHE_EVIDENCE_MISMATCH"):
        _lint_cache(container, risk_source_id)

    _forge_cache_entry(
        root,
        "lint",
        lambda payload: payload["issues"][0]["evidence"][1].update(page_or_section="page:999"),
    )
    with pytest.raises(OutputValidationError, match="LINT_CACHE_EVIDENCE_MISMATCH"):
        _lint_cache(container, risk_source_id)

    assert _table_count(root, "issue_cards") == issues_before
    assert _table_count(root, "relations") == relations_before
    assert _model_call_log_count(root) == 0


def test_three_consecutive_resets_after_full_demo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T12-R14：完整演示到 LLD-724_2 后连续三次 reset initial，结果确定且无残留。"""
    root = _restored_frozen_root(tmp_path)
    container = _offline_container(root, monkeypatch)
    assert _run_offline_main_flow(container) == "LLD-724_2"
    # 应用运行期持共享状态锁：先释放再重置（持锁应用会阻断重置，另行覆盖）。
    container.close()

    fingerprints: list[dict[str, str]] = []
    for _ in range(3):
        report = reset_demo("initial", root)
        assert report.ok
        assert report.baseline_version == BASELINE_VERSION
        snapshot: dict[str, str] = {}
        for relative in SNAPSHOT_TARGETS:
            path = root / relative
            if path.is_dir():
                snapshot[str(relative)] = hashlib.sha256(
                    "\n".join(
                        f"{item.relative_to(path)}:{hashlib.sha256(item.read_bytes()).hexdigest()}"
                        for item in sorted(path.rglob("*"))
                        if item.is_file()
                    ).encode("utf-8")
                ).hexdigest()
            else:
                snapshot[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
        fingerprints.append(snapshot)
        residue = [
            str(item)
            for item in root.rglob("*")
            if item.name.endswith(("-wal", "-shm", ".tmp"))
            or item.name.startswith(".reset-")
            or ".staging-" in item.name
        ]
        assert residue == []
    assert fingerprints[0] == fingerprints[1] == fingerprints[2]
