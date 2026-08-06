"""T10/T11 联合验收：全新临时工程根目录上的真实 14 步流程。

使用真实文件存储、真实 SQLite 仓储和真实 Use Case（build_container 组装）；
外部模型侧通过 httpx.MockTransport 模拟 Dify 工作流响应（回声式，应答全部
来自请求输入中的真实卡片/片段，不伪造中间状态）。

运行方式（仓库根目录）：
    .venv/bin/python .superpowers/sdd/2026-07-29-product-intelligence-lightweight/evidence/t10-t11/joint_acceptance.py
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402

from scripts.bootstrap_demo import (  # noqa: E402
    BASE_SOURCE_ID,
    MARKET_CARD_CONTENT,
    MARKET_CARD_ID,
    RULE_CARD_ID,
    bootstrap,
)
from src.application.container import build_container  # noqa: E402
from src.application.dto.decision import (  # noqa: E402
    CreateChangeRequestInput,
    RecordDecisionInput,
)
from src.application.dto.ingest import ImportSourceInput  # noqa: E402
from src.application.dto.lint import RunLintInput  # noqa: E402
from src.application.dto.query import RunQueryInput  # noqa: E402
from src.application.dto.release import (  # noqa: E402
    PublishBaselineInput,
    ReviewChangeRequestInput,
)
from src.application.dto.trace import BuildTraceInput  # noqa: E402
from src.application.dto.dashboard import GetDashboardInput  # noqa: E402
from src.domain.enums import (  # noqa: E402
    AuthorityLevel,
    ChangeReviewAction,
    DecisionAction,
    EvidenceSide,
    SecurityLevel,
)
from src.domain.errors import DomainError  # noqa: E402
from src.domain.models import CostImpactInput  # noqa: E402

ROOT = Path("/tmp/t10t11_joint")
LOG_PATH = (
    REPO
    / ".superpowers/sdd/2026-07-29-product-intelligence-lightweight/evidence/t10-t11"
    / "joint-acceptance.log"
)

RISK_SENTENCE = "风险意见要求收紧目标客群。"
RISK_CONTENT = (
    "# 风险意见书\n\n"
    f"{RISK_SENTENCE}\n\n"
    "## 风险背景\n\n"
    + "\n\n".join(f"第{i}段说明文字，用于记录风险排查过程与数据口径。" for i in range(1, 301))
    + "\n"
)
# 验收侧独立、固定的业务预期：不得从被测模块（scripts/bootstrap_demo.py）导入业务
# 正文常量，避免实现与验收共享同一个常量来源（ACC-P1-01 整改）。
EXPECTED_INITIAL_RULE = "当前目标客群是符合准入要求的存量客户。"
EXPECTED_PUBLISHED_RULE = "目标客群收紧为符合准入要求且通过风险评估的存量客户。"

_RESULTS: list[str] = []


def check(step: str, condition: bool, detail: str) -> None:
    status = "PASS" if condition else "FAIL"
    line = f"[{status}] {step}: {detail}"
    _RESULTS.append(line)
    print(line)
    if not condition:
        raise SystemExit(f"验收中断于：{step}")


def _ingest_result(inputs: dict) -> dict:
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


def _handler(request: httpx.Request) -> httpx.Response:
    auth = request.headers.get("authorization", "")
    inputs = json.loads(request.content.decode("utf-8"))["inputs"]
    if "ingest" in auth:
        result = _ingest_result(inputs)
    elif "query" in auth:
        result = _query_result(inputs)
    elif "lint" in auth:
        result = _lint_result(inputs)
    else:  # pragma: no cover - 防御未知任务
        return httpx.Response(400, json={"message": "unknown task key"})
    return httpx.Response(
        200,
        json={"workflow_run_id": "WF-ACCEPT-001", "data": {"outputs": {"result": result}}},
    )


def _factory() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_handler))


def _relation_count() -> int:
    with sqlite3.connect(ROOT / "data/local_state/product_intelligence.db") as connection:
        return connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0]


def _import_command(
    *,
    name: str,
    content: str,
    source_type: str,
    authority: AuthorityLevel,
    security: SecurityLevel,
    sandbox: bool,
    mode: str,
) -> ImportSourceInput:
    return ImportSourceInput(
        project_id="LLD",
        uploaded_name=name,
        uploaded_bytes=content.encode("utf-8"),
        source_type=source_type,
        authority_level=authority,
        source_department="风险" if source_type == "risk_opinion" else "产品",
        provider=None,
        document_date=date(2026, 8, 4),
        document_version="v1.0",
        applicable_baseline_version="LLD-724_1",
        security_level=security,
        is_redacted_confirmed=True,
        allow_external_model=not sandbox,
        is_sandbox=sandbox,
        preferred_mode=mode,
    )


def main() -> int:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    (ROOT / "config").mkdir(parents=True)
    shutil.copytree(REPO / "config", ROOT / "config", dirs_exist_ok=True)
    # 来源归档根目录按进程工作目录解析，验收全程以临时工程根为 CWD。
    os.chdir(ROOT)

    environ = {
        "DIFY_BASE_URL": "https://dify.acceptance.local",
        "DIFY_INGEST_API_KEY": "ingest-key",
        "DIFY_QUERY_API_KEY": "query-key",
        "DIFY_LINT_API_KEY": "lint-key",
    }

    # 步骤 1：初始化 LLD-724_1（真实 bootstrap 路径）
    manifest = bootstrap(ROOT)
    check(
        "01 初始化",
        manifest.current_version == "LLD-724_1" and _relation_count() == 2,
        f"baseline={manifest.current_version}, relations={_relation_count()}",
    )

    container = build_container(
        ROOT / "config/app.yaml",
        environ=environ,
        http_factory=_factory,
    )
    check(
        "01b 容器组装",
        container.import_source is not None
        and container.query is not None
        and container.lint is not None,
        "import/query/lint 服务齐备，启动对账通过",
    )

    # 步骤 2：导入两份正式材料和一份明确沙箱成本材料
    risk_report = container.import_source.execute(
        _import_command(
            name="风险意见.md",
            content=RISK_CONTENT,
            source_type="risk_opinion",
            authority=AuthorityLevel.FORMAL_DECISION,
            security=SecurityLevel.L2_INTERNAL,
            sandbox=False,
            mode="realtime",
        )
    )
    formal_report = container.import_source.execute(
        _import_command(
            name="市场宣传方案.md",
            content="# 市场宣传方案\n\n仅作留档参考的宣传口径草稿。\n",
            source_type="formal_document",
            authority=AuthorityLevel.FORMAL_EFFECTIVE,
            security=SecurityLevel.L2_INTERNAL,
            sandbox=False,
            mode="local",
        )
    )
    sandbox_report = container.import_source.execute(
        _import_command(
            name="演示测算参数.md",
            content="# 演示测算参数\n\n单笔有效推荐奖励 50 元，仅用于演示测算。\n",
            source_type="demo_cost_parameter",
            authority=AuthorityLevel.DISCUSSION_REFERENCE,
            security=SecurityLevel.L1_PUBLIC_SIMULATED,
            sandbox=True,
            mode="local",
        )
    )
    check(
        "02 导入三份材料",
        len(risk_report.created_card_ids) == 1
        and not formal_report.created_card_ids
        and not sandbox_report.created_card_ids
        and _relation_count() == 4,
        f"risk={risk_report.source_id} 生成 1 张候选卡；relations={_relation_count()}",
    )

    # 步骤 3：当前查询
    current = container.query.execute(
        RunQueryInput(project_id="LLD", question="当前目标客群是什么？", scope="effective")
    )
    check(
        "03 当前查询 (V3-A02)",
        current.answer == EXPECTED_INITIAL_RULE
        and current.baseline_version == "LLD-724_1"
        and any(citation.excerpt == EXPECTED_INITIAL_RULE for citation in current.citations),
        f"answer={current.answer!r}, version={current.baseline_version}, "
        f"citation_excerpts={[c.excerpt for c in current.citations]!r}",
    )

    # 步骤 4：Lint
    lint_report = container.lint.execute(
        RunLintInput(
            project_id="LLD",
            scope="current_plus_source",
            source_id=risk_report.source_id,
        )
    )
    issue = next((item for item in lint_report.issues if item.target_rule_id == RULE_CARD_ID), None)
    check(
        "04 Lint",
        issue is not None
        and lint_report.semantic_count == 1
        and _relation_count() == 5,
        f"issue={issue.id if issue else None}, relations={_relation_count()}",
    )
    challenging = next(e for e in issue.evidence if e.side == EvidenceSide.CHALLENGING_SOURCE)

    # 步骤 5：人工决定并生成变更单
    decision_command = RecordDecisionInput(
        issue_id=issue.id,
        action=DecisionAction.ACCEPT_CHANGE,
        conclusion="采纳风险意见，收紧目标客群。",
        confirmed_by="产品经理",
        responsible_party="产品负责人",
        verification_condition="回归校验通过且审批完成。",
        idempotency_key="DECISION-ACCEPT-001",
        change_request=CreateChangeRequestInput(
            target_card_id=RULE_CARD_ID,
            before_content=EXPECTED_INITIAL_RULE,
            after_content=EXPECTED_PUBLISHED_RULE,
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
    decision_result = container.record_decision.execute(decision_command)
    change = decision_result.change_request
    check(
        "05 人工决定",
        change is not None and _relation_count() == 7,
        f"decision={decision_result.decision.id}, change={change.id}, relations=7",
    )

    # 步骤 5b：决定幂等重放，Relation 数量稳定
    replay = container.record_decision.execute(decision_command)
    check(
        "05b 决定幂等重放",
        replay.decision.id == decision_result.decision.id and _relation_count() == 7,
        f"重放返回原决定 {replay.decision.id}，relations 仍为 7",
    )

    # 步骤 6：人工批准
    reviewed = container.review_change_request.execute(
        ReviewChangeRequestInput(
            change_request_id=change.id,
            action=ChangeReviewAction.APPROVE,
            reviewed_by="产品经理",
            comment="已检查修改前后、依据、影响对象和目标版本。",
            idempotency_key="REVIEW-ACCEPT-001",
        )
    )
    check("06 人工批准", reviewed.status.value == "approved", f"change={reviewed.id} approved")

    # 步骤 7：发布 LLD-724_2
    baseline = container.publish_baseline.execute(
        PublishBaselineInput(
            project_id="LLD",
            change_request_id=change.id,
            approved_by="产品经理",
            impact_reviewed=True,
            release_note="完成客群规则调整，保留版本差异与追溯依据。",
        )
    )
    check(
        "07 原子发布",
        baseline.version == "LLD-724_2" and _relation_count() == 9,
        f"baseline={baseline.id} effective，relations=9（+approved_as/supersedes）",
    )

    # 步骤 8：当前查询验证新规则
    current_after = container.query.execute(
        RunQueryInput(project_id="LLD", question="当前目标客群是什么？", scope="effective")
    )
    check(
        "08 发布后当前查询 (V3-A03)",
        current_after.answer == EXPECTED_PUBLISHED_RULE
        and current_after.baseline_version == "LLD-724_2"
        and any(
            citation.excerpt == EXPECTED_PUBLISHED_RULE for citation in current_after.citations
        ),
        f"answer={current_after.answer!r}, version={current_after.baseline_version}, "
        f"citation_excerpts={[c.excerpt for c in current_after.citations]!r}",
    )

    # 步骤 9：历史查询验证旧规则
    historical = container.query.execute(
        RunQueryInput(
            project_id="LLD",
            question="当前目标客群是什么？",
            scope="historical",
            historical_version="LLD-724_1",
        )
    )
    check(
        "09 历史查询 (V3-A04)",
        historical.answer == EXPECTED_INITIAL_RULE
        and historical.baseline_version == "LLD-724_1"
        and any(citation.excerpt == EXPECTED_INITIAL_RULE for citation in historical.citations),
        f"answer={historical.answer!r}, version={historical.baseline_version}, "
        f"citation_excerpts={[c.excerpt for c in historical.citations]!r}",
    )

    # 步骤 10：追溯页验证六节点 Relation 链
    trace = container.trace.execute(BuildTraceInput(entity_id=RULE_CARD_ID))
    kinds = [node.kind for node in trace.main_chain]
    edge_types = [edge.relation_type for edge in trace.edges]
    check(
        "10 六节点 Relation 链",
        kinds == ["source", "knowledge", "issue", "decision", "change", "baseline"]
        and edge_types
        == [
            "derived_from",
            "conflicts_with",
            "resolved_by",
            "proposes_change_to",
            "approved_as",
        ]
        and trace.missing_links == [],
        f"kinds={kinds}, edges={edge_types}, missing={trace.missing_links}",
    )

    # 步骤 11：展开来源验证 locator 和 excerpt
    source_node = trace.main_chain[0]
    excerpt = source_node.excerpt or ""
    check(
        "11 来源原文验证 (V3-A05)",
        source_node.entity_id == BASE_SOURCE_ID
        and source_node.verification == "verified"
        and "｜" in excerpt
        and EXPECTED_INITIAL_RULE in excerpt
        and str(ROOT) not in excerpt
        and "/Users" not in excerpt,
        f"verification={source_node.verification}, excerpt={excerpt[:60]!r}…",
    )

    # 步骤 12：市场证据不足提示
    gaps = container.trace.market_evidence_gaps("LLD")
    market_gap = next((gap for gap in gaps if gap.claim == MARKET_CARD_CONTENT), None)
    check(
        "12 市场证据不足",
        market_gap is not None and market_gap.classification == "unvalidated_assumption",
        f"卡片={MARKET_CARD_ID}, classification="
        f"{market_gap.classification if market_gap else None}",
    )

    # 步骤 13：沙箱轻量成本联动
    cost_sources = container.trace.list_cost_sources("LLD")
    cost = container.trace.calculate_cost_impact(
        "LLD",
        CostImpactInput(
            parameter_name="单笔有效推荐奖励",
            old_value=50,
            new_value=60,
            projected_valid_referrals=100,
            source_refs=[source.id for source in cost_sources],
        ),
    )
    blocked = None
    try:
        container.trace.calculate_cost_impact(
            "LLD",
            CostImpactInput(
                parameter_name="单笔有效推荐奖励",
                old_value=50,
                new_value=60,
                projected_valid_referrals=100,
                source_refs=[BASE_SOURCE_ID],
            ),
        )
    except DomainError as error:
        blocked = error.code
    check(
        "13 沙箱成本联动",
        [source.id for source in cost_sources] == [sandbox_report.source_id]
        and cost.is_simulation is True
        and str(cost.delta) == "1000.00"
        and blocked == "COST_SOURCE_REQUIRED",
        f"sources={[s.id for s in cost_sources]}, is_simulation={cost.is_simulation}, "
        f"delta={cost.delta}, 正式来源被阻断={blocked}",
    )

    # 步骤 14：重启应用验证 Manifest/SQLite 对账
    restarted = build_container(
        ROOT / "config/app.yaml",
        environ=environ,
        http_factory=_factory,
    )
    reconciliation = restarted.reconciliation.validate_manifest_mirror()
    dashboard = restarted.dashboard.execute(GetDashboardInput(project_id="LLD"))
    check(
        "14 重启对账",
        reconciliation.success
        and dashboard.integrity_ok
        and dashboard.current_baseline is not None
        and dashboard.current_baseline.version == "LLD-724_2"
        and _relation_count() == 9,
        f"reconcile={reconciliation.success}, integrity={dashboard.integrity_ok}, "
        f"version={dashboard.current_baseline.version if dashboard.current_baseline else None}, "
        f"relations={_relation_count()}",
    )

    LOG_PATH.write_text("\n".join(_RESULTS) + "\n", encoding="utf-8")
    print(f"\n全部 14 步通过，证据已写入 {LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
