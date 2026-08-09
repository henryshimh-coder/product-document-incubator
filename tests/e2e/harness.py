"""T13 E2E Harness：真实 AppContainer + 确定性 mock 网关的演示流程驱动器。

计划 Step 1 的实现。Harness 只做编排，不绕过任何应用层校验：模型侧由
httpx.MockTransport 提供与联合验收同构的确定性响应，服务端校验、发布闸、
回滚全部为真实应用行为。网关可按任务注入超时（Step 3 实时超时回退用例）。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

import httpx

from scripts.bootstrap_demo import BASELINE_VERSION, RULE_CARD_CONTENT, RULE_CARD_ID
from scripts.demo_materials import RISK_SENTENCE
from src.application.container import AppContainer
from src.application.dto.decision import CreateChangeRequestInput, RecordDecisionInput
from src.application.dto.ingest import ImportSourceInput
from src.application.dto.lint import RunLintInput
from src.application.dto.query import RunQueryInput
from src.application.dto.release import PublishBaselineInput, ReviewChangeRequestInput
from src.domain.enums import (
    AuthorityLevel,
    ChangeReviewAction,
    DecisionAction,
    SecurityLevel,
)
from src.domain.models import (
    Baseline,
    ChangeRequest,
    DecisionResult,
    IngestReport,
    LintReport,
    QueryResponse,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "sources"

PUBLISHED_RULE_CONTENT = "目标客群收紧为符合准入要求且通过风险评估的存量客户。"
TARGET_VERSION = "LLD-724_2"

MOCK_ENVIRON = {
    "DIFY_BASE_URL": "https://dify.e2e.local",
    "DIFY_INGEST_API_KEY": "ingest-key",
    "DIFY_QUERY_API_KEY": "query-key",
    "DIFY_LINT_API_KEY": "lint-key",
}


def ingest_command_from_fixture(
    fixture_path: Path,
    *,
    security: SecurityLevel = SecurityLevel.L2_INTERNAL,
    sandbox: bool = False,
) -> ImportSourceInput:
    """从演示夹具构造导入命令（风险材料为正式决定，其余为普通产品材料）。"""
    is_risk = "risk" in fixture_path.name
    return ImportSourceInput(
        project_id="LLD",
        uploaded_name=fixture_path.name,
        uploaded_bytes=fixture_path.read_bytes(),
        source_type="risk_opinion" if is_risk else "meeting_minutes",
        authority_level=(
            AuthorityLevel.FORMAL_DECISION if is_risk else AuthorityLevel.DISCUSSION_REFERENCE
        ),
        source_department="风险" if is_risk else "产品",
        provider=None,
        document_date=date(2026, 8, 4),
        document_version="v1.0",
        applicable_baseline_version=BASELINE_VERSION,
        security_level=security,
        is_redacted_confirmed=True,
        allow_external_model=not sandbox,
        is_sandbox=sandbox,
        preferred_mode="realtime",
    )


def _ingest_result(inputs: dict) -> dict:
    """与联合验收同构：风险材料产出一条冲突候选 + 冲突关系，其余留档。"""
    if inputs["source"]["type"] != "risk_opinion":
        return {
            "schema_version": "1.0",
            "task_id": inputs["task_id"],
            "summary": "留档材料，无需提取候选知识。",
            "items": [],
            "relations": [],
        }
    chunk = next(
        (c for c in inputs["source_chunks"] if RISK_SENTENCE in c["text"]),
        inputs["source_chunks"][0],
    )
    return {
        "schema_version": "1.0",
        "task_id": inputs["task_id"],
        "summary": "识别到一条需会议裁决的风险意见。",
        "items": [
            {
                "item_id": "ITEM-RISK-001",
                "item_type": "professional_opinion",
                "title": "客群限制意见",
                "content": RISK_SENTENCE,
                "target_card_id": RULE_CARD_ID,
                "result_type": "conflict_discussion",
                "status": "conflict",
                "source_citations": [
                    {
                        "source_id": inputs["source"]["id"],
                        "chunk_id": chunk["chunk_id"],
                        "locator": chunk["locator"],
                        "excerpt": chunk["text"][:40],
                    }
                ],
                "confidence": 0.86,
                "uncertainty": "尚未形成正式决定",
            }
        ],
        "relations": [
            {
                "source_id": "ITEM-RISK-001",
                "relation_type": "conflicts_with",
                "target_id": RULE_CARD_ID,
            }
        ],
    }


def _query_result(inputs: dict) -> dict:
    card = next(c for c in inputs["effective_cards"] if c["id"] == RULE_CARD_ID)
    citation = next(
        (
            c
            for c in inputs.get("citations", [])
            if c["id"] in set(card.get("source_citations", []))
        ),
        (inputs.get("citations") or [None])[0],
    )
    return {
        "answer": card["content"],
        "effective_rules": [card["id"]],
        "citations": [citation] if citation else [],
        "candidate_notice": None,
        "conflict_notice": None,
        "baseline_version": inputs["baseline_version"],
        "evidence_sufficiency": "sufficient",
        "result_mode": "realtime",
        "model_call_id": None,
    }


def _lint_result(inputs: dict) -> dict:
    base = next(r for r in inputs["baseline_rules"] if r["id"] == RULE_CARD_ID)
    compare = inputs["comparison_items"][0]
    return {
        "schema_version": "1.0",
        "issues": [
            {
                "issue_type": "conflict",
                "severity": "pending_decision",
                "title": "客群边界不一致",
                "description": "正式风险意见要求收紧目标客群，需要会议确认执行口径。",
                "evidence": [
                    {
                        "source_id": base["source_id"],
                        "citation_id": base["citation_id"],
                        "excerpt": base["excerpt"],
                        "document_version": base["document_version"],
                        "page_or_section": base["page_or_section"],
                        "side": "current_baseline",
                    },
                    {
                        "source_id": compare["source_id"],
                        "citation_id": compare["citation_id"],
                        "excerpt": compare["excerpt"],
                        "document_version": compare["document_version"],
                        "page_or_section": compare["page_or_section"],
                        "side": "challenging_source",
                    },
                ],
                "impacted_domains": ["产品", "风险"],
                "options": [{"code": "A", "label": "收紧", "impact": "调整产品规则"}],
                "ai_recommendation": "A",
                "ai_confidence": 0.78,
                "uncertainty": "专业意见尚未形成正式决定",
            }
        ],
    }


def mock_http_factory(
    timeout_tasks: frozenset[str] = frozenset(),
    record: list[dict] | None = None,
) -> httpx.Client:
    """确定性 mock 网关；`timeout_tasks` 中的任务一律超时，`record` 收集出站载荷。"""

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        inputs = json.loads(request.content.decode("utf-8"))["inputs"]
        if "ingest" in auth:
            task = "ingest"
            result_factory = _ingest_result
        elif "query" in auth:
            task = "query"
            result_factory = _query_result
        elif "lint" in auth:
            task = "lint"
            result_factory = _lint_result
        else:  # pragma: no cover - 防御未知任务
            return httpx.Response(400, json={"message": "unknown task key"})
        if record is not None:
            record.append(
                {
                    "task": task,
                    "inputs": inputs,
                    "raw_body": request.content.decode("utf-8"),
                }
            )
        if task in timeout_tasks:
            raise httpx.ConnectTimeout(f"E2E injected timeout for {task}", request=request)
        return httpx.Response(
            200,
            json={
                "workflow_run_id": "WF-E2E-001",
                "data": {"outputs": {"result": result_factory(inputs)}},
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


class DemoHarness:
    """计划 Step 1 的演示流程驱动器：真实容器 + 夹具编排。"""

    def __init__(self, container: AppContainer, fixture_dir: Path = FIXTURES_DIR) -> None:
        self.container = container
        self.fixture_dir = fixture_dir

    def import_source(
        self,
        fixture_name: str,
        preferred_mode: Literal["realtime", "cache", "local"] = "realtime",
        *,
        security: SecurityLevel = SecurityLevel.L2_INTERNAL,
        sandbox: bool = False,
    ) -> IngestReport:
        command = ingest_command_from_fixture(
            self.fixture_dir / fixture_name,
            security=security,
            sandbox=sandbox,
        )
        return self.container.import_source.execute(
            command.model_copy(update={"preferred_mode": preferred_mode})
        )

    def query(self, question: str) -> QueryResponse:
        return self.container.query.execute(
            RunQueryInput(
                project_id="LLD",
                question=question,
                scope="effective",
                historical_version=None,
            )
        )

    def run_lint(self, source_id: str | None = None) -> LintReport:
        return self.container.lint.execute(
            RunLintInput(
                project_id="LLD",
                scope="all_current_sources" if source_id is None else "current_plus_source",
                source_id=source_id,
            )
        )

    def record_accept_change(self, issue_id: str, *, evidence_ref: str) -> DecisionResult:
        return self.container.record_decision.execute(
            RecordDecisionInput(
                issue_id=issue_id,
                action=DecisionAction.ACCEPT_CHANGE,
                conclusion="采纳专业意见并形成产品规则调整。",
                confirmed_by="产品经理",
                responsible_party="产品",
                due_at=None,
                verification_condition="发布前完成规则、风险和技术实现一致性复核。",
                idempotency_key=f"E2E-{issue_id}-ACCEPT",
                change_request=CreateChangeRequestInput(
                    target_card_id=RULE_CARD_ID,
                    before_content=RULE_CARD_CONTENT,
                    after_content=PUBLISHED_RULE_CONTENT,
                    rationale="依据正式风险意见和会议结论调整。",
                    evidence_refs=[evidence_ref],
                    impacted_objects=[RULE_CARD_ID],
                    responsible_domain="产品",
                    required_approver_role="产品经理",
                    demo_confirmer="产品经理",
                    target_version=TARGET_VERSION,
                    effective_condition="审批通过且验证完成后发布。",
                ),
            )
        )

    def approve_change(self, change_id: str) -> ChangeRequest:
        return self.container.review_change_request.execute(
            ReviewChangeRequestInput(
                change_request_id=change_id,
                action=ChangeReviewAction.APPROVE,
                reviewed_by="产品经理",
                comment="已检查修改前后、依据、影响对象和目标版本。",
                idempotency_key=f"E2E-{change_id}-APPROVE",
            )
        )

    def publish(self, change_id: str) -> Baseline:
        return self.container.publish_baseline.execute(
            PublishBaselineInput(
                project_id="LLD",
                change_request_id=change_id,
                approved_by="产品经理",
                impact_reviewed=True,
                release_note="完成目标客群边界调整并保留来源与决策记录。",
            )
        )
