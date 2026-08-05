from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.application.dto.trace import BuildTraceInput
from src.application.use_cases.build_trace import BuildTrace
from src.domain.enums import (
    AuthorityLevel,
    BaselineStatus,
    CallResultMode,
    ChangeStatus,
    DecisionAction,
    EvidenceSide,
    IssueSeverity,
    IssueStatus,
    KnowledgeStatus,
    SecurityLevel,
)
from src.domain.errors import DomainError
from src.domain.models import (
    Baseline,
    CostImpactInput,
    Decision,
    IssueCard,
    IssueEvidence,
    KnowledgeCard,
    ModelCallLog,
    SourceRecord,
)
from src.infrastructure.db.repositories import (
    SqliteBaselineRepository,
    SqliteChangeRepository,
    SqliteDecisionRepository,
    SqliteIssueRepository,
    SqliteKnowledgeRepository,
    SqliteModelCallLogRepository,
    SqliteRelationRepository,
    SqliteSourceRepository,
)
from src.infrastructure.files.baseline_card_reader import LocalBaselineCardReader
from src.infrastructure.files.extractor import extract_document_bytes
from src.infrastructure.files.query_material_reader import LocalQueryMaterialReader
from src.infrastructure.observability.model_call_logger import ModelCallLogger
from tests.integration.release_env import (
    CHANGE_ID,
    CURRENT_BASELINE_ID,
    CURRENT_VERSION,
    DECISION_ID,
    ISSUE_ID,
    NOW,
    PROJECT_ID,
    REVIEWER,
    TARGET_BASELINE_ID,
    TARGET_VERSION,
    _write_source_archive,
    build_release_environment,
    make_change,
)

CARD_ID = "RULE-001"


def _use_case(env) -> BuildTrace:
    return BuildTrace(
        manifest=env.manifest_store,
        baseline_cards=LocalBaselineCardReader(env.project_root),
        relations=SqliteRelationRepository(env.db_path),
        knowledge=SqliteKnowledgeRepository(env.db_path),
        sources=SqliteSourceRepository(env.db_path),
        issues=SqliteIssueRepository(env.db_path),
        decisions=SqliteDecisionRepository(env.db_path),
        changes=SqliteChangeRepository(env.db_path),
        baselines=SqliteBaselineRepository(env.db_path),
        model_calls=SqliteModelCallLogRepository(env.db_path),
        material_reader=LocalQueryMaterialReader(env.project_root),
    )


def _add_relation(
    env,
    rel_id: str,
    source_id: str,
    relation_type: str,
    target_id: str,
    *,
    source_ref: str | None = None,
    project_id: str = PROJECT_ID,
) -> None:
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            """
            INSERT INTO relations (
                id, project_id, source_id, relation_type, target_id, source_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (rel_id, project_id, source_id, relation_type, target_id, source_ref, NOW.isoformat()),
        )


def _seed_chain(env, *, with_baseline: bool = True, with_source: bool = True) -> None:
    if with_source:
        _add_relation(
            env, "REL-DERIVED-1", "SRC-BASE", "derived_from", CARD_ID, source_ref="SRC-BASE"
        )
    _add_relation(env, "REL-CONFLICT-1", CARD_ID, "conflicts_with", ISSUE_ID)
    _add_relation(env, "REL-RESOLVED-1", ISSUE_ID, "resolved_by", DECISION_ID)
    _add_relation(env, "REL-PROPOSES-1", DECISION_ID, "proposes_change_to", CHANGE_ID)
    if with_baseline:
        _add_relation(env, "REL-APPROVED-1", CHANGE_ID, "approved_as", TARGET_BASELINE_ID)


def _publish(env) -> None:
    """Insert the published baseline directly; the publish transaction itself is covered by T10."""
    env.changes.update_status(CHANGE_ID, ChangeStatus.PUBLISHED, NOW)
    env.baselines.add(
        Baseline(
            id=TARGET_BASELINE_ID,
            project_id=PROJECT_ID,
            version=TARGET_VERSION,
            parent_baseline_id=CURRENT_BASELINE_ID,
            status=BaselineStatus.EFFECTIVE,
            full_document_path=f"data/obsidian_vault/02_Current_Baseline/{TARGET_VERSION}/full.md",
            card_snapshot_path=(
                f"data/obsidian_vault/02_Current_Baseline/{TARGET_VERSION}/cards.json"
            ),
            manifest_sha256="c" * 64,
            change_request_id=CHANGE_ID,
            approved_by=REVIEWER,
            effective_at=NOW,
            created_at=NOW,
        )
    )


def _approved_env(tmp_path):
    return build_release_environment(tmp_path, change=make_change(ChangeStatus.APPROVED))


def _add_source(env, source_id: str, content: str, **overrides) -> SourceRecord:
    archive_path, digest, size = _write_source_archive(
        env.project_root, source_id, f"{source_id}.md", content
    )
    payload = {
        "id": source_id,
        "project_id": PROJECT_ID,
        "original_filename": f"{source_id}.md",
        "archive_path": archive_path,
        "sha256": digest,
        "mime_type": "text/markdown",
        "size_bytes": size,
        "source_type": "customer_market_material",
        "authority_level": AuthorityLevel.FORMAL_EFFECTIVE,
        "source_department": "产品",
        "provider": None,
        "document_date": date(2026, 7, 20),
        "document_version": "v1.0",
        "applicable_baseline_version": CURRENT_VERSION,
        "security_level": SecurityLevel.L1_PUBLIC_SIMULATED,
        "is_redacted": True,
        "allow_external_model": True,
        "is_sandbox": False,
        "ingest_status": "completed",
        "created_at": NOW,
    }
    payload.update(overrides)
    source = SourceRecord(**payload)
    SqliteSourceRepository(env.db_path).add(source)
    return source


def _chunk_id_for(source: SourceRecord, needle: str) -> str:
    payload = Path(source.archive_path).read_bytes()
    extracted = extract_document_bytes(
        payload, filename=source.original_filename, source_id=source.id
    )
    for chunk in extracted.chunks:
        if needle in chunk.text:
            return chunk.chunk_id
    raise AssertionError(f"chunk containing {needle!r} not found")


def _rewrite_card_refs(env, card_id: str, refs: list[str]) -> None:
    """Rewrite one snapshot card's refs and re-sign the manifest like a real publish would."""
    import json

    snapshot = env.manifest_store.read_snapshot()
    manifest = snapshot.manifest
    cards_path = env.project_root / manifest.card_snapshot_path
    document = json.loads(cards_path.read_text(encoding="utf-8"))
    for card in document:
        if card["id"] == card_id:
            card["source_refs"] = refs
    payload = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    cards_path.write_bytes(payload)
    env.manifest_store.atomic_replace(
        manifest.model_copy(update={"card_snapshot_sha256": hashlib.sha256(payload).hexdigest()})
    )


def test_trace_contains_source_issue_decision_change_and_release(tmp_path):
    env = _approved_env(tmp_path)
    _publish(env)
    _seed_chain(env)

    trace = _use_case(env).execute(BuildTraceInput(entity_id=CARD_ID))

    assert [node.kind for node in trace.main_chain] == [
        "source",
        "knowledge",
        "issue",
        "decision",
        "change",
        "baseline",
    ]
    assert [edge.relation_type for edge in trace.edges] == [
        "derived_from",
        "conflicts_with",
        "resolved_by",
        "proposes_change_to",
        "approved_as",
    ]
    assert trace.missing_links == []
    assert trace.main_chain[0].entity_id == "SRC-BASE"
    assert trace.main_chain[0].is_sandbox is False
    assert trace.main_chain[0].verification == "verified"
    assert trace.main_chain[-1].entity_id == TARGET_BASELINE_ID
    knowledge_node = trace.main_chain[1]
    assert knowledge_node.entity_id == CARD_ID
    assert knowledge_node.label == "目标客群"


def test_trace_without_any_relations_never_falls_back_to_entity_fields(tmp_path):
    """实体齐全但零 Relation 时，只能出现知识节点，禁止按字段或最新记录补边。"""
    env = _approved_env(tmp_path)
    _publish(env)

    trace = _use_case(env).execute(BuildTraceInput(entity_id=CARD_ID))

    assert [node.kind for node in trace.main_chain] == ["knowledge"]
    assert trace.edges == []
    assert trace.missing_links == ["原始资料", "问题", "人工决定", "变更单", "生效基线"]


def test_deleted_relation_leaves_explicit_gap_without_auto_repair(tmp_path):
    env = _approved_env(tmp_path)
    _publish(env)
    _seed_chain(env)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute("DELETE FROM relations WHERE id = 'REL-RESOLVED-1'")

    trace = _use_case(env).execute(BuildTraceInput(entity_id=CARD_ID))

    assert [node.kind for node in trace.main_chain] == ["source", "knowledge", "issue"]
    assert trace.missing_links == ["人工决定", "变更单", "生效基线"]


def test_trace_before_publish_marks_baseline_missing(tmp_path):
    env = _approved_env(tmp_path)
    _seed_chain(env, with_baseline=False)

    trace = _use_case(env).execute(BuildTraceInput(entity_id=CARD_ID))

    assert "生效基线" in trace.missing_links
    assert [node.kind for node in trace.main_chain] == [
        "source",
        "knowledge",
        "issue",
        "decision",
        "change",
    ]


def test_trace_without_source_relation_marks_source_missing(tmp_path):
    env = _approved_env(tmp_path)
    _publish(env)
    _seed_chain(env, with_source=False)

    trace = _use_case(env).execute(BuildTraceInput(entity_id=CARD_ID))

    assert "原始资料" in trace.missing_links
    assert trace.main_chain[0].kind == "knowledge"


def test_trace_ignores_relations_from_other_projects(tmp_path):
    env = _approved_env(tmp_path)
    _publish(env)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            "INSERT INTO projects (id, name, product_line, stage, current_baseline_id,"
            " allow_external_model, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("OTHER", "其他项目", "线", "demo", "BASE-OTHER", 1, NOW.isoformat(), NOW.isoformat()),
        )
    _add_relation(env, "REL-OTHER-1", CARD_ID, "conflicts_with", ISSUE_ID, project_id="OTHER")

    trace = _use_case(env).execute(BuildTraceInput(entity_id=CARD_ID))

    assert [node.kind for node in trace.main_chain] == ["knowledge"]
    assert "问题" in trace.missing_links


def test_trace_ignores_newer_unconnected_issue_and_decision(tmp_path):
    """存在更新但不相连的 Issue/Decision 时，主链只取关系相连的实体。"""
    env = _approved_env(tmp_path)
    _publish(env)
    _seed_chain(env)
    later = NOW + timedelta(days=1)
    SqliteIssueRepository(env.db_path).add_many(
        [
            IssueCard(
                id="ISSUE-NEWER",
                project_id=PROJECT_ID,
                issue_type="information_gap",
                severity=IssueSeverity.PENDING_INFO,
                status=IssueStatus.OPEN,
                title="更新的不相连问题",
                description="与目标卡没有任何 Relation 连接。",
                evidence=[],
                impacted_domains=["产品"],
                options=[],
                ai_recommendation=None,
                ai_confidence=None,
                uncertainty="需要人工确认",
                owner=None,
                due_at=None,
                created_at=later,
                updated_at=later,
            )
        ]
    )
    SqliteDecisionRepository(env.db_path).add(
        Decision(
            id="DECISION-NEWER",
            project_id=PROJECT_ID,
            issue_id="ISSUE-NEWER",
            action=DecisionAction.KEEP_CURRENT,
            conclusion="不相连的最新决定。",
            confirmed_by="产品经理",
            responsible_party=None,
            due_at=None,
            verification_condition=None,
            created_at=later,
        ),
        idempotency_key="IDEM-NEWER",
    )

    trace = _use_case(env).execute(BuildTraceInput(entity_id=CARD_ID))

    assert [node.entity_id for node in trace.main_chain] == [
        "SRC-BASE",
        CARD_ID,
        ISSUE_ID,
        DECISION_ID,
        CHANGE_ID,
        TARGET_BASELINE_ID,
    ]
    assert trace.missing_links == []


def test_trace_marks_sandbox_source(tmp_path):
    env = _approved_env(tmp_path)
    _add_source(
        env,
        "SRC-SAND",
        "# 模拟材料\n\n演示用模拟参数。\n",
        source_type="demo_cost_parameter",
        is_sandbox=True,
    )
    _add_relation(env, "REL-DERIVED-SAND", "SRC-SAND", "derived_from", CARD_ID)

    trace = _use_case(env).execute(BuildTraceInput(entity_id=CARD_ID))

    assert trace.main_chain[0].kind == "source"
    assert trace.main_chain[0].entity_id == "SRC-SAND"
    assert trace.main_chain[0].is_sandbox is True


def test_source_node_excerpt_is_located_and_redacted(tmp_path):
    env = _approved_env(tmp_path)
    source = _add_source(
        env,
        "SRC-MKT",
        "# 客户访谈\n\n客户普遍接受该奖励机制，联系电话 13800001234。\n",
    )
    chunk_id = _chunk_id_for(source, "客户普遍接受该奖励机制")
    _rewrite_card_refs(env, CARD_ID, [f"SRC-MKT:{chunk_id}"])
    _add_relation(env, "REL-DERIVED-MKT", "SRC-MKT", "derived_from", CARD_ID)

    trace = _use_case(env).execute(BuildTraceInput(entity_id=CARD_ID))

    source_node = trace.main_chain[0]
    assert source_node.entity_id == "SRC-MKT"
    assert source_node.verification == "verified"
    assert source_node.excerpt is not None
    assert "line:" in source_node.excerpt
    assert "客户普遍接受该奖励机制" in source_node.excerpt
    assert "13800001234" not in source_node.excerpt
    assert "[已脱敏:phone]" in source_node.excerpt


def test_source_node_unverifiable_when_archive_tampered(tmp_path):
    env = _approved_env(tmp_path)
    source = _add_source(env, "SRC-MKT", "# 客户访谈\n\n客户普遍接受该奖励机制。\n")
    chunk_id = _chunk_id_for(source, "客户普遍接受该奖励机制")
    _rewrite_card_refs(env, CARD_ID, [f"SRC-MKT:{chunk_id}"])
    _add_relation(env, "REL-DERIVED-MKT", "SRC-MKT", "derived_from", CARD_ID)
    with Path(source.archive_path).open("ab") as handle:
        handle.write("篡改。".encode())

    trace = _use_case(env).execute(BuildTraceInput(entity_id=CARD_ID))

    source_node = trace.main_chain[0]
    assert source_node.verification == "unverifiable"
    assert source_node.excerpt is None


def test_source_node_unverifiable_when_citation_fragment_missing(tmp_path):
    env = _approved_env(tmp_path)
    _add_source(env, "SRC-MKT", "# 客户访谈\n\n客户普遍接受该奖励机制。\n")
    _rewrite_card_refs(env, CARD_ID, ["SRC-MKT:SRC-MKT-9999"])
    _add_relation(env, "REL-DERIVED-MKT", "SRC-MKT", "derived_from", CARD_ID)

    trace = _use_case(env).execute(BuildTraceInput(entity_id=CARD_ID))

    assert trace.main_chain[0].verification == "unverifiable"


def test_trace_unknown_card_raises_not_found(tmp_path):
    env = _approved_env(tmp_path)

    with pytest.raises(DomainError, match="NOT_FOUND"):
        _use_case(env).execute(BuildTraceInput(entity_id="RULE-MISSING"))


def test_entry_cards_come_from_current_manifest_version(tmp_path):
    env = build_release_environment(tmp_path)

    cards = _use_case(env).list_entry_cards(PROJECT_ID)

    assert {card.id for card in cards} == {"RULE-001", "API-CUSTOMER"}
    assert all(card.product_version == CURRENT_VERSION for card in cards)


def test_entry_cards_fail_closed_when_snapshot_tampered(tmp_path):
    env = build_release_environment(tmp_path)
    snapshot = env.manifest_store.read_snapshot()
    cards_path = env.project_root / snapshot.manifest.card_snapshot_path
    cards_path.write_bytes(cards_path.read_bytes() + b"\n")

    with pytest.raises(DomainError, match="BASELINE_INTEGRITY_FAILED"):
        _use_case(env).list_entry_cards(PROJECT_ID)


def _record_call(env, call_id: str, *, task_type: str = "query", elapsed_ms: int = 1000) -> None:
    ModelCallLogger(env.db_path).record(
        ModelCallLog(
            id=call_id,
            project_id=PROJECT_ID,
            task_type=task_type,
            workflow_run_id=None,
            correlation_id=f"CORR-{call_id}",
            source_ids=["SRC-BASE"],
            baseline_version=CURRENT_VERSION,
            model_label="demo-model",
            prompt_version="p1",
            schema_version="1.0",
            authorized=True,
            redacted=True,
            outbound_chars=120,
            outbound_coverage=0.4,
            result_mode=CallResultMode.REALTIME,
            status="succeeded",
            started_at=NOW,
            finished_at=NOW + timedelta(milliseconds=elapsed_ms),
            elapsed_ms=elapsed_ms,
            error_code=None,
        )
    )


def _add_issue(env, issue_id: str, *, severity: IssueSeverity, status: IssueStatus) -> None:
    evidence = []
    if severity in {IssueSeverity.BLOCKING, IssueSeverity.PENDING_DECISION}:
        evidence = [
            IssueEvidence(
                source_id="SRC-BASE",
                citation_id="CIT-BASE-001",
                excerpt="当前规则。",
                document_version="v1.0",
                page_or_section="目标客群",
                side=EvidenceSide.CURRENT_BASELINE,
            ),
            IssueEvidence(
                source_id="SRC-RISK",
                citation_id="CIT-RISK-001",
                excerpt="挑战意见。",
                document_version="v1.0",
                page_or_section="客群限制",
                side=EvidenceSide.CHALLENGING_SOURCE,
            ),
        ]
    SqliteIssueRepository(env.db_path).add_many(
        [
            IssueCard(
                id=issue_id,
                project_id=PROJECT_ID,
                issue_type="conflict",
                severity=severity,
                status=status,
                title=f"问题 {issue_id}",
                description="演示问题。",
                evidence=evidence,
                impacted_domains=["产品"],
                options=[],
                ai_recommendation=None,
                ai_confidence=None,
                uncertainty="需要人工确认" if severity == IssueSeverity.PENDING_INFO else None,
                owner=None,
                due_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        ]
    )


def test_value_metrics_only_show_measured_indicators(tmp_path):
    env = build_release_environment(tmp_path)

    metrics = _use_case(env).value_metrics(PROJECT_ID)

    labels = [metric.label for metric in metrics]
    assert "人工查询耗时" not in labels
    assert "系统查询耗时" not in labels
    assert "引用完整度" not in labels
    assert "有效冲突数量" not in labels
    assert "误报数量" not in labels


def test_value_metrics_come_from_real_local_data(tmp_path):
    env = _approved_env(tmp_path)
    _record_call(env, "CALL-001", elapsed_ms=1000)
    _record_call(env, "CALL-002", elapsed_ms=3000)
    _record_call(env, "CALL-003", task_type="ingest", elapsed_ms=9000)
    _add_issue(env, "ISSUE-BLOCKING", severity=IssueSeverity.BLOCKING, status=IssueStatus.OPEN)
    _add_issue(
        env,
        "ISSUE-FALSE-POSITIVE",
        severity=IssueSeverity.PENDING_INFO,
        status=IssueStatus.FALSE_POSITIVE,
    )

    metrics = {metric.label: metric for metric in _use_case(env).value_metrics(PROJECT_ID)}

    assert metrics["系统查询耗时"].value == "2.0 秒"
    assert metrics["有效冲突数量"].value == "1"
    assert metrics["误报数量"].value == "1"
    assert metrics["变更单形成耗时"].value == "0 分钟"
    assert all("实测" in metric.source_note for metric in metrics.values())


def test_list_model_calls_returns_recent_first_with_limit(tmp_path):
    env = build_release_environment(tmp_path)
    _record_call(env, "CALL-001")
    _record_call(env, "CALL-002", elapsed_ms=2000)

    logs = _use_case(env).list_model_calls(PROJECT_ID, limit=10)

    assert [log.id for log in logs] == ["CALL-002", "CALL-001"]
    assert logs[0].authorized is True
    assert logs[0].redacted is True
    assert logs[0].result_mode == CallResultMode.REALTIME
    limited = _use_case(env).list_model_calls(PROJECT_ID, limit=1)
    assert len(limited) == 1


def _add_market_card(
    env,
    card_id: str,
    *,
    status: KnowledgeStatus,
    source_refs: list[str],
) -> None:
    SqliteKnowledgeRepository(env.db_path).upsert_cards(
        [
            KnowledgeCard(
                id=card_id,
                project_id=PROJECT_ID,
                card_type="market_judgment",
                title="客户接受度",
                content="客户普遍接受该奖励机制",
                status=status,
                product_version=CURRENT_VERSION,
                applicable_scope="演示",
                source_refs=source_refs,
                authority_level=AuthorityLevel.DISCUSSION_REFERENCE,
                owner="产品",
                created_at=NOW,
                updated_at=NOW,
            )
        ]
    )


def test_market_evidence_gaps_flag_unvalidated_market_cards(tmp_path):
    env = build_release_environment(tmp_path)
    _add_market_card(env, "MKT-001", status=KnowledgeStatus.CANDIDATE, source_refs=[])

    gaps = _use_case(env).market_evidence_gaps(PROJECT_ID)

    assert len(gaps) == 1
    assert gaps[0].claim == "客户普遍接受该奖励机制"
    assert gaps[0].classification == "unvalidated_assumption"
    assert gaps[0].evidence_sufficiency == "insufficient"
    assert gaps[0].suggested_validation is not None


def test_formal_document_refs_never_count_as_market_evidence(tmp_path):
    """正式产品材料类型不属于客户/市场验证材料，有引用也不能算有证据。"""
    env = build_release_environment(tmp_path)
    base = env.sources.get("SRC-BASE")
    chunk_id = _chunk_id_for(base, "当前目标客群")
    _add_market_card(
        env,
        "MKT-002",
        status=KnowledgeStatus.CANDIDATE,
        source_refs=[f"SRC-BASE:{chunk_id}"],
    )

    gaps = _use_case(env).market_evidence_gaps(PROJECT_ID)

    assert gaps[0].classification == "unvalidated_assumption"
    assert gaps[0].evidence_refs == []


def test_sandbox_market_material_never_counts_as_evidence(tmp_path):
    env = build_release_environment(tmp_path)
    source = _add_source(
        env,
        "SRC-MKT-SAND",
        "# 模拟访谈\n\n客户普遍接受该奖励机制。\n",
        is_sandbox=True,
    )
    chunk_id = _chunk_id_for(source, "客户普遍接受")
    _add_market_card(
        env,
        "MKT-003",
        status=KnowledgeStatus.CANDIDATE,
        source_refs=[f"SRC-MKT-SAND:{chunk_id}"],
    )

    gaps = _use_case(env).market_evidence_gaps(PROJECT_ID)

    assert gaps[0].classification == "unvalidated_assumption"


def test_verified_market_material_counts_as_evidence(tmp_path):
    env = build_release_environment(tmp_path)
    source = _add_source(
        env,
        "SRC-MKT-REAL",
        "# 客户访谈纪要\n\n客户普遍接受该奖励机制，访谈样本 32 人。\n",
    )
    chunk_id = _chunk_id_for(source, "客户普遍接受")
    _add_market_card(
        env,
        "MKT-004",
        status=KnowledgeStatus.CANDIDATE,
        source_refs=[f"SRC-MKT-REAL:{chunk_id}"],
    )

    gaps = _use_case(env).market_evidence_gaps(PROJECT_ID)

    assert gaps[0].classification == "evidence_supported"
    assert gaps[0].evidence_refs == [f"SRC-MKT-REAL:{chunk_id}"]


def test_tampered_market_archive_drops_evidence(tmp_path):
    env = build_release_environment(tmp_path)
    source = _add_source(
        env,
        "SRC-MKT-TAMPER",
        "# 客户访谈纪要\n\n客户普遍接受该奖励机制。\n",
    )
    chunk_id = _chunk_id_for(source, "客户普遍接受")
    _add_market_card(
        env,
        "MKT-005",
        status=KnowledgeStatus.CANDIDATE,
        source_refs=[f"SRC-MKT-TAMPER:{chunk_id}"],
    )
    with Path(source.archive_path).open("ab") as handle:
        handle.write("篡改。".encode())

    gaps = _use_case(env).market_evidence_gaps(PROJECT_ID)

    assert gaps[0].classification == "unvalidated_assumption"


def test_arbitrary_or_unlocatable_refs_never_count_as_market_evidence(tmp_path):
    """任意字符串、普通卡片 ID 或定位不到的片段都不能充当市场证据。"""
    env = build_release_environment(tmp_path)
    _add_source(
        env,
        "SRC-MKT-ARB",
        "# 客户访谈纪要\n\n客户普遍接受该奖励机制。\n",
    )
    _add_market_card(
        env,
        "MKT-ARB",
        status=KnowledgeStatus.CANDIDATE,
        source_refs=[
            "随便一串字符串",
            "RULE-001",
            "SRC-MKT-ARB:CHUNK-NOT-EXIST",
        ],
    )

    gaps = _use_case(env).market_evidence_gaps(PROJECT_ID)

    assert gaps[0].classification == "unvalidated_assumption"
    assert gaps[0].evidence_refs == []


def _add_validation_issue(
    env,
    issue_id: str,
    *,
    rule_id: str | None,
    target_card_id: str,
    validation_note: str | None,
) -> None:
    SqliteIssueRepository(env.db_path).add_many(
        [
            IssueCard(
                id=issue_id,
                project_id=PROJECT_ID,
                issue_type="information_gap",
                severity=IssueSeverity.PENDING_INFO,
                status=IssueStatus.OPEN,
                title=f"验证 {issue_id}",
                description="市场判断待验证。",
                evidence=[],
                impacted_domains=["产品"],
                options=[],
                ai_recommendation=None,
                ai_confidence=None,
                uncertainty="需要补充市场验证材料",
                validation_note=validation_note,
                deterministic_rule_id=rule_id,
                target_rule_id=target_card_id,
                owner=None,
                due_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        ]
    )


def test_validation_plan_only_comes_from_mkt_rule_of_same_card(tmp_path):
    env = build_release_environment(tmp_path)
    _add_market_card(env, "MKT-006", status=KnowledgeStatus.CANDIDATE, source_refs=[])
    _add_validation_issue(
        env,
        "ISSUE-MKT-PLAN",
        rule_id="MKT-001",
        target_card_id="MKT-006",
        validation_note="2026-09 前完成 20 个目标客户访谈",
    )
    _add_validation_issue(
        env,
        "ISSUE-OTHER-PLAN",
        rule_id="API-002",
        target_card_id="MKT-006",
        validation_note="这条说明不属于市场验证规则，不得复用",
    )

    gaps = _use_case(env).market_evidence_gaps(PROJECT_ID)

    assert gaps[0].classification == "validation_planned"
    assert gaps[0].suggested_validation == "2026-09 前完成 20 个目标客户访谈"


def test_validation_plan_from_other_rule_is_ignored(tmp_path):
    env = build_release_environment(tmp_path)
    _add_market_card(env, "MKT-007", status=KnowledgeStatus.CANDIDATE, source_refs=[])
    _add_validation_issue(
        env,
        "ISSUE-OTHER-ONLY",
        rule_id="API-002",
        target_card_id="MKT-007",
        validation_note="其他规则的说明不能充当市场验证计划",
    )

    gaps = _use_case(env).market_evidence_gaps(PROJECT_ID)

    assert gaps[0].classification == "unvalidated_assumption"


def test_list_cost_sources_only_accepts_sandbox_cost_parameter_materials(tmp_path):
    env = build_release_environment(tmp_path)
    assert _use_case(env).list_cost_sources(PROJECT_ID) == []

    _add_source(
        env,
        "SRC-COST",
        "# 演示测算参数\n\n单笔奖励 50 元。\n",
        source_type="demo_cost_parameter",
        is_sandbox=True,
    )
    _add_source(
        env,
        "SRC-SAND-DOC",
        "# 模拟说明\n\n普通模拟文件。\n",
        source_type="formal_document",
        is_sandbox=True,
    )
    _add_source(
        env,
        "SRC-FORMAL-COST",
        "# 正式成本参数\n\n单笔奖励 50 元。\n",
        source_type="cost_parameter",
        is_sandbox=False,
    )

    sources = _use_case(env).list_cost_sources(PROJECT_ID)

    assert [source.id for source in sources] == ["SRC-COST"]
    assert sources[0].is_sandbox is True


def _cost_command(refs: list[str]) -> CostImpactInput:
    return CostImpactInput(
        parameter_name="单笔有效推荐奖励",
        old_value=50,
        new_value=60,
        projected_valid_referrals=100,
        source_refs=refs,
    )


def test_cost_impact_requires_refs(tmp_path):
    env = build_release_environment(tmp_path)

    with pytest.raises(DomainError, match="COST_SOURCE_REQUIRED"):
        _use_case(env).calculate_cost_impact(PROJECT_ID, _cost_command([]))


def test_cost_impact_rejects_formal_or_unknown_sources(tmp_path):
    env = build_release_environment(tmp_path)
    service = _use_case(env)

    with pytest.raises(DomainError, match="COST_SOURCE_INVALID:SRC-BASE"):
        service.calculate_cost_impact(PROJECT_ID, _cost_command(["SRC-BASE"]))
    with pytest.raises(DomainError, match="COST_SOURCE_INVALID:SRC-MISSING"):
        service.calculate_cost_impact(PROJECT_ID, _cost_command(["SRC-MISSING"]))


def test_cost_impact_rejects_non_cost_sandbox_material(tmp_path):
    env = build_release_environment(tmp_path)
    _add_source(
        env,
        "SRC-SAND-PLAIN",
        "# 模拟说明\n\n普通模拟文件。\n",
        source_type="formal_document",
        is_sandbox=True,
    )

    with pytest.raises(DomainError, match="COST_SOURCE_INVALID:SRC-SAND-PLAIN"):
        _use_case(env).calculate_cost_impact(PROJECT_ID, _cost_command(["SRC-SAND-PLAIN"]))


def test_cost_impact_auto_marks_simulation_from_source_records(tmp_path):
    env = build_release_environment(tmp_path)
    _add_source(
        env,
        "SRC-COST",
        "# 演示测算参数\n\n单笔奖励 50 元。\n",
        source_type="demo_cost_parameter",
        is_sandbox=True,
    )

    result = _use_case(env).calculate_cost_impact(PROJECT_ID, _cost_command(["SRC-COST"]))

    assert result.is_simulation is True
    assert str(result.old_cost) == "5000.00"
    assert str(result.new_cost) == "6000.00"
    assert str(result.delta) == "1000.00"
    assert result.disclaimer == "仅供业务影响提示，正式口径需财务确认。"
