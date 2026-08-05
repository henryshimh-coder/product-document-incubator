from __future__ import annotations

from datetime import UTC, datetime, timedelta

from streamlit.testing.v1 import AppTest

from src.domain.enums import CallResultMode
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import MarketEvidenceGap, ModelCallLog, ValueMetric

NOW = datetime(2026, 8, 3, 7, 0, tzinfo=UTC)


def _render(
    *,
    gaps=(),
    logs=(),
    metrics=(),
    services=True,
    trace_error=None,
    second_card=False,
    bare_source=False,
) -> None:
    from datetime import UTC, datetime

    from src.application.container import AppContainer, AppSettings
    from src.domain.enums import AuthorityLevel, KnowledgeStatus, SecurityLevel
    from src.domain.models import KnowledgeCard, SourceRecord, TraceEdge, TraceNode, TraceView
    from src.domain.services.cost_impact import calculate_cost_impact
    from src.ui.pages import trace as trace_page
    from src.ui.theme.loader import load_theme

    now = datetime(2026, 8, 3, 7, 0, tzinfo=UTC)

    def entry_card() -> KnowledgeCard:
        return KnowledgeCard(
            id="RULE-001",
            project_id="LLD",
            card_type="rule",
            title="目标客群",
            content="当前目标客群是符合准入要求的存量客户。",
            status=KnowledgeStatus.EFFECTIVE,
            product_version="LLD-724_1",
            applicable_scope="演示",
            source_refs=["SRC-BASE:CIT-BASE-001"],
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            owner="产品",
            created_at=now,
            updated_at=now,
        )

    def trace_view() -> TraceView:
        nodes = [
            TraceNode(
                kind="source",
                entity_id="SRC-BASE",
                label="当前产品方案.md",
                status="completed",
                happened_at=now,
                summary="文件版本 v1.0，权威级别 formal_effective",
                verification="unverifiable" if bare_source else "not_applicable",
                unverifiable_reason="no_citation" if bare_source else None,
            ),
            TraceNode(
                kind="knowledge",
                entity_id="RULE-001",
                label="目标客群",
                status="effective",
                happened_at=now,
                summary="当前目标客群是符合准入要求的存量客户。",
            ),
            TraceNode(
                kind="issue",
                entity_id="ISSUE-001",
                label="客群规则待收紧",
                status="decided",
                happened_at=now,
                summary="风险意见要求收紧客群。",
            ),
            TraceNode(
                kind="decision",
                entity_id="DECISION-001",
                label="接受迭代",
                status="accept_change",
                happened_at=now,
                summary="会议确认采纳风险意见。",
            ),
            TraceNode(
                kind="change",
                entity_id="CHANGE-001",
                label="目标版本 LLD-724_2",
                status="published",
                happened_at=now,
                summary="依据风险意见和会议结论调整。",
            ),
            TraceNode(
                kind="baseline",
                entity_id="BASE-LLD-724_2",
                label="版本 LLD-724_2",
                status="effective",
                happened_at=now,
                summary="审批人 产品经理",
            ),
        ]
        edges = [
            TraceEdge(source_id="SRC-BASE", target_id="RULE-001", relation_type="derived_from"),
            TraceEdge(source_id="RULE-001", target_id="ISSUE-001", relation_type="conflicts_with"),
            TraceEdge(source_id="ISSUE-001", target_id="DECISION-001", relation_type="resolved_by"),
            TraceEdge(
                source_id="DECISION-001",
                target_id="CHANGE-001",
                relation_type="proposes_change_to",
            ),
            TraceEdge(
                source_id="CHANGE-001",
                target_id="BASE-LLD-724_2",
                relation_type="approved_as",
            ),
        ]
        return TraceView(main_chain=nodes, edges=edges, missing_links=[])

    class TraceStub:
        def execute(self, command):
            if trace_error is not None:
                raise trace_error
            return trace_view()

        def list_entry_cards(self, project_id):
            cards = [entry_card()]
            if second_card:
                cards.append(
                    entry_card().model_copy(update={"id": "RULE-002", "title": "奖励规则"})
                )
            return cards

        def list_model_calls(self, project_id, *, limit):
            return list(logs)

        def value_metrics(self, project_id):
            return list(metrics)

        def market_evidence_gaps(self, project_id):
            return list(gaps)

        def list_cost_sources(self, project_id):
            from datetime import date

            return [
                SourceRecord(
                    id="SRC-COST-001",
                    project_id="LLD",
                    original_filename="演示测算参数.md",
                    archive_path="data/source_archive/LLD/SRC-COST-001/演示测算参数.md",
                    sha256="d" * 64,
                    mime_type="text/markdown",
                    size_bytes=256,
                    source_type="demo_cost_parameter",
                    authority_level=AuthorityLevel.DISCUSSION_REFERENCE,
                    source_department="财务",
                    provider=None,
                    document_date=date(2026, 7, 20),
                    document_version="v1.0",
                    applicable_baseline_version="LLD-724_1",
                    security_level=SecurityLevel.L1_PUBLIC_SIMULATED,
                    is_redacted=True,
                    allow_external_model=False,
                    is_sandbox=True,
                    ingest_status="completed",
                    created_at=now,
                )
            ]

        def calculate_cost_impact(self, project_id, command):
            result = calculate_cost_impact(command)
            return result.model_copy(update={"is_simulation": True})

    load_theme()
    trace_page.render(
        AppContainer(
            settings=AppSettings(
                name="产品智策",
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("pdf", "docx", "txt", "md"),
                demo_mode=True,
                schema_version="1.0",
                lint_input_contract_version="2.0",
            ),
            trace=TraceStub() if services else None,
        )
    )


def _html(page: AppTest) -> str:
    markdown = "\n".join(item.value for item in page.markdown)
    captions = "\n".join(item.value for item in page.caption)
    return markdown + "\n" + captions


def _gap() -> MarketEvidenceGap:
    return MarketEvidenceGap(
        claim="客户普遍接受该奖励机制",
        classification="unvalidated_assumption",
        evidence_sufficiency="insufficient",
        evidence_refs=[],
        missing_materials=["客户或市场验证材料"],
        suggested_validation="补充客户或市场验证材料，或制定明确的验证计划",
    )


def _log() -> ModelCallLog:
    return ModelCallLog(
        id="CALL-001",
        project_id="LLD",
        task_type="query",
        workflow_run_id=None,
        correlation_id="CORR-CALL-001",
        source_ids=["SRC-BASE"],
        baseline_version="LLD-724_1",
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
        finished_at=NOW + timedelta(milliseconds=1500),
        elapsed_ms=1500,
        error_code=None,
    )


def _metric(label: str = "系统查询耗时") -> ValueMetric:
    return ValueMetric(label=label, value="2.0 秒", source_note="来自本地 SQLite 实测数据")


def test_trace_page_shows_six_node_chain_with_relations() -> None:
    """Catches the main story chain losing nodes, relation labels, or entry details."""
    page = AppTest.from_function(_render).run()

    assert not page.exception
    rendered = _html(page)
    for text in (
        "原始资料",
        "结构化知识",
        "人工决定",
        "变更单",
        "生效基线",
        "来源于",
        "冲突于",
        "会议决定",
        "建议修改",
        "批准形成",
        "SRC-BASE",
        "RULE-001",
        "BASE-LLD-724_2",
        "文件版本 v1.0，权威级别 formal_effective",
    ):
        assert text in rendered, text


def test_trace_page_marks_bare_source_ref_as_no_locatable_citation() -> None:
    """V3-A16: 裸来源引用显示“未提供可定位引用”，不显示已验证徽标。"""
    page = AppTest.from_function(_render, kwargs={"bare_source": True}).run()

    assert not page.exception
    rendered = _html(page)
    assert "未提供可定位引用" in rendered
    assert "引用不可验证" not in rendered
    assert "已验证" not in rendered


def test_trace_page_flags_unvalidated_market_judgment_without_stating_fact() -> None:
    """Catches an unvalidated market claim being presented as an accepted fact."""
    page = AppTest.from_function(_render, kwargs={"gaps": [_gap()]}).run()

    assert not page.exception
    assert any("未验证判断，不能作为事实依据" in item.value for item in page.warning)
    rendered = _html(page)
    assert "客户普遍接受该奖励机制" in rendered
    assert "未验证假设" in rendered
    assert "不足" in rendered
    assert "建议验证方式" in rendered
    assert "市场已认可" not in rendered


def test_trace_page_shows_empty_market_gap_state() -> None:
    page = AppTest.from_function(_render).run()

    assert not page.exception
    assert any("当前版本没有市场判断卡片" in item.value for item in page.info)


def test_cost_form_requires_source_refs() -> None:
    """Catches cost calculation running without a parameter source."""
    page = AppTest.from_function(_render).run()

    page.text_input(key="trace_cost_parameter").set_value("单笔有效推荐奖励")
    page.number_input(key="trace_cost_count").set_value(100)
    page.button(key="trace_cost_run").click().run()

    assert not page.exception
    assert any("成本参数缺少来源" in item.value for item in page.warning)
    assert any("COST_SOURCE_REQUIRED" in item.value for item in page.warning)


def test_cost_form_rejects_incomplete_parameters() -> None:
    """Catches cost calculation silently filling missing parameters."""
    page = AppTest.from_function(_render).run()

    page.multiselect(key="trace_cost_refs").select("SRC-COST-001")
    page.button(key="trace_cost_run").click().run()

    assert not page.exception
    assert any("成本参数不完整，无法计算" in item.value for item in page.warning)
    assert any("COST_INPUT_INCOMPLETE" in item.value for item in page.warning)


def test_cost_form_computes_decimal_result_with_disclaimer() -> None:
    """Catches the cost panel losing formula, Decimal result, sources, or disclaimer."""
    page = AppTest.from_function(_render).run()

    page.text_input(key="trace_cost_parameter").set_value("单笔有效推荐奖励")
    page.number_input(key="trace_cost_old").set_value(50.0)
    page.number_input(key="trace_cost_new").set_value(60.0)
    page.number_input(key="trace_cost_count").set_value(100)
    page.multiselect(key="trace_cost_refs").select("SRC-COST-001")
    page.button(key="trace_cost_run").click().run()

    assert not page.exception
    rendered = _html(page)
    assert "单笔有效推荐奖励 × 预计有效推荐笔数" in rendered
    assert "（参数来自模拟数据）" in rendered
    assert "5000.00" in rendered
    assert "6000.00" in rendered
    assert "1000.00" in rendered
    assert "SRC-COST-001" in rendered
    assert "仅供业务影响提示，正式口径需财务确认。" in rendered


def test_value_tab_shows_only_measured_metrics() -> None:
    """Catches unmeasured value indicators rendering as placeholders or estimates."""
    page = AppTest.from_function(
        _render,
        kwargs={"metrics": [_metric(), _metric("有效冲突数量")]},
    ).run()

    assert not page.exception
    assert [item.label for item in page.metric] == ["系统查询耗时", "有效冲突数量"]
    assert any("实测" in item.value for item in page.caption)

    empty = AppTest.from_function(_render).run()
    assert not empty.exception
    assert any("当前没有已完成实测的价值指标" in item.value for item in empty.info)
    assert all("人工查询耗时" not in item.label for item in empty.metric)


def test_audit_tab_lists_call_summary_columns() -> None:
    """Catches the audit table dropping summary columns or leaking sensitive content."""
    page = AppTest.from_function(_render, kwargs={"logs": [_log()]}).run()

    assert not page.exception
    frame = page.dataframe[0].value
    assert list(frame.columns) == [
        "调用编号",
        "任务",
        "模型",
        "来源文件",
        "授权状态",
        "脱敏状态",
        "实时／缓存",
        "开始时间",
        "耗时",
        "结果状态",
    ]
    row = frame.iloc[0]
    assert row["调用编号"] == "CALL-001"
    assert row["任务"] == "知识查询"
    assert row["模型"] == "demo-model"
    assert row["来源文件"] == "SRC-BASE"
    assert row["授权状态"] == "已授权"
    assert row["脱敏状态"] == "已脱敏"
    assert row["实时／缓存"] == "实时"
    assert row["耗时"] == "1500 ms"
    assert row["结果状态"] == "成功"

    empty = AppTest.from_function(_render).run()
    assert any("当前没有模型调用记录" in item.value for item in empty.info)


def test_trace_error_shows_user_message_and_code() -> None:
    """Catches a trace failure rendering without the public error code."""
    page = AppTest.from_function(
        _render,
        kwargs={"trace_error": DomainError(ErrorCode.NOT_FOUND)},
    ).run()

    assert not page.exception
    assert any("未找到目标记录" in item.value for item in page.error)
    assert any("NOT_FOUND" in item.value for item in page.error)


def test_trace_page_without_service_degrades_without_exception() -> None:
    """Catches an uninitialized workspace turning the trace page into an exception."""
    page = AppTest.from_function(_render, kwargs={"services": False}).run()

    assert not page.exception
    assert any("追溯服务尚未就绪" in item.value for item in page.info)


def test_trace_page_preselects_card_passed_from_release() -> None:
    """Catches the release-to-trace handoff losing the target card selection."""
    page = AppTest.from_function(_render, kwargs={"second_card": True})
    page.session_state["trace_target_card_id"] = "RULE-002"
    page.run()

    assert not page.exception
    assert page.selectbox(key="trace_entry_card").value == "RULE-002"
    assert "trace_target_card_id" not in page.session_state
