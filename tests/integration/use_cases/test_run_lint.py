from __future__ import annotations

import importlib
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any

import pytest

from src.application.ports.dashboard import ManifestSnapshot
from src.domain.enums import (
    AuthorityLevel,
    CallResultMode,
    IssueSeverity,
    IssueStatus,
    KnowledgeStatus,
    SecurityLevel,
)
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import (
    BaselineManifest,
    IssueCard,
    IssueEvidence,
    KnowledgeCard,
    Project,
    SourceRecord,
)
from src.infrastructure.files.query_material_reader import (
    VerifiedFragment,
    VerifiedQueryMaterial,
)

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def _baseline_card() -> KnowledgeCard:
    return KnowledgeCard(
        id="RULE-001",
        project_id="LLD",
        card_type="rule",
        title="目标客群",
        content="当前目标客群规则。",
        status=KnowledgeStatus.EFFECTIVE,
        product_version="LLD-724_1",
        applicable_scope="演示",
        source_refs=["SRC-BASE:CIT-BASE-001"],
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        owner="产品",
        created_at=NOW,
        updated_at=NOW,
    )


def _manifest() -> BaselineManifest:
    return BaselineManifest(
        schema_version="1.0",
        project_id="LLD",
        current_baseline_id="BASE-LLD-724_1",
        current_version="LLD-724_1",
        parent_baseline_id=None,
        full_document_path=("data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md"),
        card_snapshot_path=("data/obsidian_vault/02_Current_Baseline/LLD-724_1/cards.json"),
        full_document_sha256="a" * 64,
        card_snapshot_sha256="b" * 64,
        change_request_id=None,
        approved_by="产品经理",
        published_at=NOW,
    )


def _source(*, allow_external_model: bool = True) -> SourceRecord:
    return SourceRecord(
        id="SRC-RISK",
        project_id="LLD",
        original_filename="risk.md",
        archive_path="data/source_archive/LLD/SRC-RISK/risk.md",
        sha256="c" * 64,
        mime_type="text/markdown",
        size_bytes=20,
        source_type="formal_document",
        authority_level=AuthorityLevel.PROFESSIONAL_OPINION,
        source_department="风险",
        provider=None,
        document_date=date(2026, 7, 29),
        document_version="v1.0",
        applicable_baseline_version="LLD-724_1",
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted=True,
        allow_external_model=allow_external_model,
        is_sandbox=False,
        ingest_status="completed",
        created_at=NOW,
    )


class _Manifest:
    def read_snapshot(self) -> ManifestSnapshot:
        return ManifestSnapshot(_manifest(), "d" * 64)


class _Projects:
    def get(self, project_id: str) -> Project:
        return Project(
            id=project_id,
            name="产品智策",
            product_line="轻量交付",
            stage="demo",
            current_baseline_id="BASE-LLD-724_1",
            allow_external_model=True,
            created_at=NOW,
            updated_at=NOW,
        )


class _CardStore:
    def sha256_for(self, path: str) -> str:
        return "b" * 64

    def read_cards(self, path: str) -> list[KnowledgeCard]:
        return [_baseline_card()]


class _Materials:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def read_baseline(self, **context: Any) -> VerifiedQueryMaterial:
        self.events.append("baseline_archive")
        return VerifiedQueryMaterial(
            source_id="BASE-LLD-724_1",
            filename="full.md",
            document_version="LLD-724_1",
            sha256="a" * 64,
            text="当前目标客群规则。",
            fragments=(VerifiedFragment(locator="目标客群", text="当前目标客群规则。"),),
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            security_level=SecurityLevel.L2_INTERNAL,
            is_baseline_asset=True,
        )

    def read_source(self, source: SourceRecord) -> VerifiedQueryMaterial:
        self.events.append("source_archive")
        return VerifiedQueryMaterial(
            source_id=source.id,
            filename=source.original_filename,
            document_version=source.document_version,
            sha256=source.sha256,
            text="风险意见要求收紧客群。",
            fragments=(
                VerifiedFragment(
                    locator="客群限制",
                    text="风险意见要求收紧客群。",
                    fragment_id=f"{source.id}-0002",
                ),
            ),
            authority_level=source.authority_level,
            security_level=source.security_level,
            is_baseline_asset=False,
        )

    def total_chars(self, materials: list[VerifiedQueryMaterial]) -> int:
        return sum(len(item.text) for item in materials)


def _inputs() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "input_contract_version": "2.0",
        "project_id": "LLD",
        "baseline_version": "LLD-724_1",
        "task_id": "TASK-LINT-001",
        "language": "zh-CN",
        "baseline_rules": [
            {
                "id": "RULE-001",
                "source_id": "SRC-BASE",
                "citation_id": "CIT-BASE-001",
                "document_version": "LLD-724_1",
                "page_or_section": "目标客群",
                "excerpt": "当前目标客群规则。",
            }
        ],
        "comparison_items": [
            {
                "id": "ITEM-001",
                "source_id": "SRC-RISK",
                "citation_id": "CIT-RISK-001",
                "document_version": "v1.0",
                "page_or_section": "客群限制",
                "excerpt": "风险意见要求收紧客群。",
            }
        ],
        "deterministic_findings": [],
        "allowed_issue_types": [
            "conflict",
            "omission",
            "stale",
            "not_synchronized",
            "insufficient_evidence",
        ],
    }


def _single_sided_output() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "issues": [
            {
                "issue_type": "conflict",
                "severity": "blocking",
                "title": "客群边界不一致",
                "description": "需要会议确认当前执行口径",
                "evidence": [
                    {
                        "source_id": "SRC-BASE",
                        "citation_id": "CIT-BASE-001",
                        "excerpt": "当前目标客群规则。",
                        "document_version": "LLD-724_1",
                        "page_or_section": "目标客群",
                        "side": "current_baseline",
                    }
                ],
                "impacted_domains": ["产品", "风险"],
                "options": [{"code": "A", "label": "收紧", "impact": "调整产品规则"}],
                "ai_recommendation": "A",
                "ai_confidence": 0.78,
                "uncertainty": None,
            }
        ],
    }


def test_run_lint_orders_governed_pipeline_and_downgrades_one_sided_major_issue() -> None:
    """Catches external Lint running first or a one-sided major result surviving as blocking."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    dto = importlib.import_module("src.application.dto.lint")
    events: list[str] = []

    class Local:
        def run(self, command):
            events.append("local")
            return []

    class Builder:
        def build_minimum(self, command, deterministic):
            events.append("comparison")
            return dto.LintComparisonPackage(
                inputs=_inputs(),
                source_total_chars=100_000,
                security_level="L2",
            )

    class Gateway:
        def run(self, inputs, *, safety_proof, user=None, timeout_seconds=30):
            common = importlib.import_module("src.infrastructure.gateways._common")
            assert isinstance(safety_proof, common.OutboundSafetyProof)
            events.append("semantic")
            return {"workflow_run_id": "WF-LINT-001", "result": _single_sided_output()}

    class Issues:
        def upsert_all(self, issues):
            events.append("upsert")
            self.saved = deepcopy(issues)

    class LintUoW:
        def apply(self, *, issues, relations):
            events.append("upsert")
            self.saved = deepcopy(issues)
            self.saved_relations = relations

    issues = Issues()
    lint_uow = LintUoW()
    use_case = module.RunLint(
        local_lint=Local(),
        comparison_builder=Builder(),
        gateway=Gateway(),
        issues=issues,
        unit_of_work=lint_uow,
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        now=lambda: NOW,
    )

    report = use_case.execute(
        dto.RunLintInput(project_id="LLD", scope="current_plus_source", source_id="SRC-RISK")
    )

    assert events == ["local", "comparison", "semantic", "upsert"]
    assert report.result_mode == CallResultMode.REALTIME
    assert report.model_call_id == "WF-LINT-001"
    assert len(report.issues) == 1
    assert report.issues[0].severity == IssueSeverity.PENDING_INFO
    assert report.issues[0].raw_severity is None
    assert report.issues[0].deterministic_rule_id is None
    assert report.issues[0].validation_note == (
        "严重度由 blocking 降级为 pending_info：缺少对方依据"
    )
    assert report.issues[0].uncertainty == "缺少对方依据"
    assert lint_uow.saved == report.issues


def test_run_lint_deduplicates_same_fingerprint_before_upsert() -> None:
    """Catches duplicate model rows creating duplicate persisted issue cards."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    dto = importlib.import_module("src.application.dto.lint")
    output = _single_sided_output()
    output["issues"].append(deepcopy(output["issues"][0]))

    class Local:
        def run(self, command):
            return []

    class Builder:
        def build_minimum(self, command, deterministic):
            return dto.LintComparisonPackage(
                inputs=_inputs(), source_total_chars=100_000, security_level="L2"
            )

    class Gateway:
        def run(self, inputs, *, safety_proof, user=None, timeout_seconds=30):
            return {"workflow_run_id": "WF-LINT-001", "result": output}

    class Issues:
        def upsert_all(self, issues):
            self.saved = issues

    class LintUoW:
        def apply(self, *, issues, relations):
            self.saved = issues

    issues = Issues()
    lint_uow = LintUoW()
    report = module.RunLint(
        local_lint=Local(),
        comparison_builder=Builder(),
        gateway=Gateway(),
        issues=issues,
        unit_of_work=lint_uow,
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        now=lambda: NOW,
    ).execute(dto.RunLintInput(project_id="LLD", scope="current", source_id=None))

    assert len(report.issues) == 1
    assert report.issues[0].fingerprint
    assert len(lint_uow.saved) == 1


@pytest.mark.parametrize(
    ("view", "expected_ids"),
    [
        ("all_open", ["ISSUE-BLOCKING", "ISSUE-DECISION", "ISSUE-INFO"]),
        ("blocking", ["ISSUE-BLOCKING"]),
        ("pending_decision", ["ISSUE-DECISION"]),
        ("pending_info", ["ISSUE-INFO"]),
        ("processed", ["ISSUE-DECIDED", "ISSUE-DEFERRED", "ISSUE-CLOSED"]),
        ("false_positive", ["ISSUE-FALSE-POSITIVE"]),
    ],
)
def test_list_lint_issues_exposes_six_semantic_views(view: str, expected_ids: list[str]) -> None:
    """Catches UI-specific filters leaking past the application view contract."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    dto = importlib.import_module("src.application.dto.lint")
    base = module.LintIssueValidator(now=lambda: NOW).validate_issue(
        _single_sided_output()["issues"][0], project_id="LLD"
    )
    seeded = [
        base.model_copy(
            update={
                "id": "ISSUE-BLOCKING",
                "severity": IssueSeverity.BLOCKING,
                "status": IssueStatus.OPEN,
            }
        ),
        base.model_copy(
            update={
                "id": "ISSUE-DECISION",
                "severity": IssueSeverity.PENDING_DECISION,
                "status": IssueStatus.OPEN,
            }
        ),
        base.model_copy(
            update={
                "id": "ISSUE-INFO",
                "severity": IssueSeverity.PENDING_INFO,
                "status": IssueStatus.OPEN,
            }
        ),
        base.model_copy(
            update={
                "id": "ISSUE-DECIDED",
                "severity": IssueSeverity.BLOCKING,
                "status": IssueStatus.DECIDED,
            }
        ),
        base.model_copy(
            update={
                "id": "ISSUE-DEFERRED",
                "severity": IssueSeverity.PENDING_DECISION,
                "status": IssueStatus.DEFERRED,
            }
        ),
        base.model_copy(
            update={
                "id": "ISSUE-CLOSED",
                "severity": IssueSeverity.PENDING_INFO,
                "status": IssueStatus.CLOSED,
            }
        ),
        base.model_copy(
            update={
                "id": "ISSUE-FALSE-POSITIVE",
                "severity": IssueSeverity.BLOCKING,
                "status": IssueStatus.FALSE_POSITIVE,
            }
        ),
    ]

    class Issues:
        def list_all(self, project_id):
            assert project_id == "LLD"
            return list(seeded)

    use_case = module.RunLint(
        local_lint=object(),
        comparison_builder=object(),
        gateway=object(),
        issues=Issues(),
        unit_of_work=type("LintUoW", (), {"apply": lambda self, *, issues, relations: None})(),
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        now=lambda: NOW,
    )

    result = use_case.list_issues(
        dto.ListLintIssuesInput(project_id="LLD", view=view, sort_by="severity")
    )

    assert [issue.id for issue in result] == expected_ids


def test_list_lint_issues_can_sort_a_semantic_view_by_most_recent_update() -> None:
    """Catches updated sorting being reimplemented inconsistently in the UI."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    dto = importlib.import_module("src.application.dto.lint")
    base = module.LintIssueValidator(now=lambda: NOW).validate_issue(
        _single_sided_output()["issues"][0], project_id="LLD"
    )
    seeded = [
        base.model_copy(
            update={
                "id": "ISSUE-OLDER",
                "severity": IssueSeverity.BLOCKING,
                "updated_at": NOW,
            }
        ),
        base.model_copy(
            update={
                "id": "ISSUE-NEWER",
                "severity": IssueSeverity.PENDING_INFO,
                "updated_at": NOW.replace(hour=8),
            }
        ),
    ]

    class Issues:
        def list_all(self, project_id):
            return list(seeded)

    use_case = module.RunLint(
        local_lint=object(),
        comparison_builder=object(),
        gateway=object(),
        issues=Issues(),
        unit_of_work=type("LintUoW", (), {"apply": lambda self, *, issues, relations: None})(),
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        now=lambda: NOW,
    )

    result = use_case.list_issues(
        dto.ListLintIssuesInput(project_id="LLD", view="all_open", sort_by="updated")
    )

    assert [issue.id for issue in result] == ["ISSUE-NEWER", "ISSUE-OLDER"]


def test_issue_fingerprint_is_project_scoped_and_ignores_evidence_enrichment() -> None:
    """Catches citation growth changing the identity of the same logical issue."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    first_evidence = IssueEvidence.model_validate(
        _single_sided_output()["issues"][0]["evidence"][0]
    )
    second_evidence = first_evidence.model_copy(
        update={"citation_id": "CIT-BASE-002", "excerpt": "补充后的基线摘录。"}
    )
    common = {
        "issue_type": "conflict",
        "target_identity": "RULE-001",
    }

    first = module.issue_fingerprint(
        project_id="LLD",
        evidence=[first_evidence],
        impacted_domains=["产品"],
        **common,
    )
    enriched = module.issue_fingerprint(
        project_id="LLD",
        evidence=[first_evidence, second_evidence],
        impacted_domains=["产品", "风险"],
        **common,
    )
    another_project = module.issue_fingerprint(
        project_id="OTHER",
        evidence=[first_evidence],
        impacted_domains=["产品"],
        **common,
    )

    assert first == enriched
    assert first != another_project


def test_deduplicate_keeps_highest_severity_for_same_logical_issue() -> None:
    """Catches a later lower-severity duplicate overwriting a blocking finding."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    baseline_evidence = IssueEvidence.model_validate(
        _single_sided_output()["issues"][0]["evidence"][0]
    )
    challenge_evidence = baseline_evidence.model_copy(
        update={
            "source_id": "SRC-RISK",
            "citation_id": "CIT-RISK-001",
            "excerpt": "风险意见要求收紧客群。",
            "side": "challenging_source",
        }
    )
    blocking = IssueCard(
        id="ISSUE-BLOCKING",
        project_id="LLD",
        issue_type="stale",
        severity=IssueSeverity.BLOCKING,
        status=IssueStatus.OPEN,
        title="当前基线引用历史产品规则",
        description="版本不一致。",
        evidence=[baseline_evidence, challenge_evidence],
        impacted_domains=["产品"],
        options=[],
        ai_recommendation=None,
        ai_confidence=None,
        uncertainty=None,
        fingerprint="b" * 64,
        target_rule_id="RULE-001",
        owner=None,
        due_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    pending = blocking.model_copy(
        update={
            "id": "ISSUE-PENDING",
            "severity": IssueSeverity.PENDING_INFO,
            "title": "低严重度后来结果",
            "uncertainty": "待补充",
        }
    )

    assert module._deduplicate([blocking, pending]) == [blocking]


def test_deterministic_rule_identity_prevents_ver_rules_from_colliding() -> None:
    """Catches VER-002 replacing VER-001 merely because they target the same card."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    service = importlib.import_module("src.domain.services.deterministic_lint")
    validator = module.LintIssueValidator(now=lambda: NOW)
    common = {
        "issue_type": "stale",
        "severity": IssueSeverity.PENDING_INFO,
        "description": "版本不一致。",
        "evidence": [],
        "impacted_domains": ["产品"],
        "uncertainty": "需要核对",
        "target_rule_id": "RULE-001",
    }
    ver_001 = service.DeterministicFinding(rule_id="VER-001", title="产品版本落后", **common)
    ver_002 = service.DeterministicFinding(rule_id="VER-002", title="技术版本落后", **common)

    issues = module._deduplicate(
        [
            validator.validate_finding(ver_001, project_id="LLD"),
            validator.validate_finding(ver_002, project_id="LLD"),
        ]
    )

    assert len(issues) == 2
    assert {item.title for item in issues} == {"产品版本落后", "技术版本落后"}


def test_deterministic_runner_invokes_all_nine_configured_rules(monkeypatch) -> None:
    """Catches handlers being implemented but omitted from the production runner."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    dto = importlib.import_module("src.application.dto.lint")
    called: list[str] = []

    def capture(rule_id, **context):
        called.append(rule_id)
        return None

    monkeypatch.setattr(module, "run_rule", capture)
    runner = module.DeterministicLintRunner(
        manifest=_Manifest(),
        card_store=_CardStore(),
    )

    assert runner.run(dto.RunLintInput(project_id="LLD", scope="current")) == []
    assert called == [
        "STR-001",
        "STR-002",
        "GOV-001",
        "GOV-002",
        "GOV-003",
        "VER-001",
        "VER-002",
        "MKT-001",
        "COST-001",
    ]


def test_current_plus_source_validates_selected_source_before_notice_lookup() -> None:
    """Catches an invalid source escaping validation merely because it has no notice card."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    dto = importlib.import_module("src.application.dto.lint")
    events: list[str] = []

    class Sources:
        def get(self, source_id: str):
            events.append("source_record")
            raise KeyError(source_id)

    class Knowledge:
        def list_notices(self, project_id: str, version: str):
            events.append("notices")
            return []

    builder = module.SafeLintComparisonBuilder(
        manifest=_Manifest(),
        projects=_Projects(),
        knowledge=Knowledge(),
        sources=Sources(),
        card_store=_CardStore(),
        material_reader=_Materials(events),
        input_contract_version="2.0",
    )

    with pytest.raises(DomainError) as raised:
        builder.build_minimum(
            dto.RunLintInput(
                project_id="LLD", scope="current_plus_source", source_id="SRC-MISSING"
            ),
            [],
        )

    assert raised.value.code == ErrorCode.CITATION_INVALID.value
    assert raised.value.detail == "LINT_SOURCE_NOT_FOUND"
    assert events == ["baseline_archive", "source_record"]


def test_current_plus_source_rejects_valid_source_without_comparable_cards() -> None:
    """Catches a source-scoped run silently degrading to a baseline-only comparison."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    dto = importlib.import_module("src.application.dto.lint")
    events: list[str] = []

    class Sources:
        def get(self, source_id: str):
            events.append("source_record")
            return _source()

    class Knowledge:
        def list_notices(self, project_id: str, version: str):
            events.append("notices")
            return []

    builder = module.SafeLintComparisonBuilder(
        manifest=_Manifest(),
        projects=_Projects(),
        knowledge=Knowledge(),
        sources=Sources(),
        card_store=_CardStore(),
        material_reader=_Materials(events),
    )

    with pytest.raises(DomainError) as raised:
        builder.build_minimum(
            dto.RunLintInput(project_id="LLD", scope="current_plus_source", source_id="SRC-RISK"),
            [],
        )

    assert raised.value.code == ErrorCode.LINT_SOURCE_NOT_COMPARABLE.value
    assert events == ["baseline_archive", "source_record", "source_archive", "notices"]


def test_comparison_builder_sends_local_fact_without_citation_authority() -> None:
    """Catches deterministic facts being omitted or serialized as trusted citations."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    dto = importlib.import_module("src.application.dto.lint")
    service = importlib.import_module("src.domain.services.deterministic_lint")
    forged = IssueEvidence(
        source_id="CARD-RULE-001",
        citation_id="CARD-RULE-001",
        excerpt="未经验证的本地摘录。",
        document_version="LLD-700_1",
        page_or_section="目标客群",
        side="challenging_source",
    )
    finding = service.DeterministicFinding(
        rule_id="VER-001",
        issue_type="stale",
        severity=IssueSeverity.PENDING_INFO,
        title="版本不一致",
        description="本地规则事实。",
        evidence=[forged],
        impacted_domains=["产品"],
        uncertainty="缺少独立依据",
        target_rule_id="RULE-001",
    )
    events: list[str] = []
    builder = module.SafeLintComparisonBuilder(
        manifest=_Manifest(),
        projects=_Projects(),
        knowledge=type("Knowledge", (), {"list_notices": lambda *args: []})(),
        sources=type("Sources", (), {})(),
        card_store=_CardStore(),
        material_reader=_Materials(events),
    )

    package = builder.build_minimum(dto.RunLintInput(project_id="LLD", scope="current"), [finding])

    assert package.inputs["deterministic_findings"] == [
        {
            "id": "FACT-VER-001-RULE-001",
            "rule_id": "VER-001",
            "issue_type": "stale",
            "severity": "pending_info",
            "title": "版本不一致",
            "description": "本地规则事实。",
            "target_identity": "RULE-001",
            "locally_validated": True,
        }
    ]
    assert package.inputs["schema_version"] == "1.0"
    assert package.inputs["input_contract_version"] == "2.0"
    assert "citation_id" not in package.inputs["deterministic_findings"][0]


@pytest.mark.parametrize(
    ("finding_count", "expect_error", "expected_gateway_calls"),
    [(50, False, 1), (51, True, 0)],
)
def test_run_lint_fails_closed_instead_of_truncating_deterministic_facts(
    finding_count: int,
    expect_error: bool,
    expected_gateway_calls: int,
) -> None:
    """Catches the 51st local fact being silently dropped before the workflow call."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    dto = importlib.import_module("src.application.dto.lint")
    service = importlib.import_module("src.domain.services.deterministic_lint")
    findings = [
        service.DeterministicFinding(
            rule_id="STR-001",
            issue_type="insufficient_evidence",
            severity=IssueSeverity.PENDING_INFO,
            title=f"缺少来源 {index}",
            description=f"卡片 RULE-{index:03d} 缺少来源。",
            evidence=[],
            impacted_domains=["产品"],
            uncertainty="需要补充来源",
            target_rule_id=f"RULE-{index:03d}",
        )
        for index in range(finding_count)
    ]
    events: list[str] = []

    class LargeMaterials(_Materials):
        def total_chars(self, materials):
            return 100_000

    class Local:
        def run(self, command):
            return findings

    class Gateway:
        def run(self, inputs, *, safety_proof, user=None, timeout_seconds=30):
            events.append("gateway")
            return {
                "workflow_run_id": "WF-LIMIT",
                "result": {"schema_version": "1.0", "issues": []},
            }

    builder = module.SafeLintComparisonBuilder(
        manifest=_Manifest(),
        projects=_Projects(),
        knowledge=type("Knowledge", (), {"list_notices": lambda *args: []})(),
        sources=object(),
        card_store=_CardStore(),
        material_reader=LargeMaterials([]),
        task_id_factory=lambda: "TASK-LIMIT",
    )
    use_case = module.RunLint(
        local_lint=Local(),
        comparison_builder=builder,
        gateway=Gateway(),
        issues=type("Issues", (), {"upsert_all": lambda *args: None})(),
        unit_of_work=type("LintUoW", (), {"apply": lambda self, *, issues, relations: None})(),
        customer_names=(),
        strategy_terms=(),
        financial_terms=(),
        leader_names=(),
        unpublished_decisions=(),
        now=lambda: NOW,
    )

    if expect_error:
        with pytest.raises(DomainError) as raised:
            use_case.execute(dto.RunLintInput(project_id="LLD", scope="current"))
        assert raised.value.code == "LINT_DETERMINISTIC_LIMIT_EXCEEDED"
    else:
        report = use_case.execute(dto.RunLintInput(project_id="LLD", scope="current"))
        assert report.deterministic_count == 50

    assert events.count("gateway") == expected_gateway_calls


def test_current_plus_source_uses_selected_ref_even_when_it_is_not_first() -> None:
    """Catches a multi-source notice loading an unrelated first ref instead of the request."""
    module = importlib.import_module("src.application.use_cases.run_lint")
    dto = importlib.import_module("src.application.dto.lint")
    events: list[str] = []
    notice = _baseline_card().model_copy(
        update={
            "id": "NOTICE-001",
            "status": KnowledgeStatus.CONFLICT,
            "content": "风险意见要求收紧客群。",
            "source_refs": ["SRC-OTHER:CIT-OTHER", "SRC-RISK:CIT-RISK-001"],
        }
    )

    class Sources:
        def get(self, source_id: str):
            events.append(f"record:{source_id}")
            if source_id != "SRC-RISK":
                raise KeyError(source_id)
            return _source()

    class Knowledge:
        def list_notices(self, project_id: str, version: str):
            return [notice]

    package = module.SafeLintComparisonBuilder(
        manifest=_Manifest(),
        projects=_Projects(),
        knowledge=Knowledge(),
        sources=Sources(),
        card_store=_CardStore(),
        material_reader=_Materials(events),
    ).build_minimum(
        dto.RunLintInput(project_id="LLD", scope="current_plus_source", source_id="SRC-RISK"),
        [],
    )

    assert package.inputs["comparison_items"][0]["source_id"] == "SRC-RISK"
    assert "record:SRC-OTHER" not in events
