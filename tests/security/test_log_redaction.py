"""T13 安全测试：模型调用日志与出站载荷不含未脱敏的受词典保护原文。

脱敏词典经环境变量注入（与生产装配一致）。导入含客户名与战略词的
材料后：出站载荷（实际 HTTP 请求体）与 model_call_logs 全部文本列都
不得出现原文，且日志行 redacted 标记为 1。
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from scripts.bootstrap_demo import BASELINE_VERSION
from scripts.demo_materials import RISK_SENTENCE
from src.application.dto.ingest import ImportSourceInput
from src.domain.enums import AuthorityLevel, SecurityLevel
from tests.e2e.harness import MOCK_ENVIRON

CUSTOMER = "甲方控股集团"
STRATEGY = "北极星战略"

RISK_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sources" / "risk_opinion.md"


def _command() -> ImportSourceInput:
    # 以真实夹具为基底，受保护词与风险句同行（保证同 chunk 进入出站载荷）。
    base = RISK_FIXTURE.read_text(encoding="utf-8")
    assert RISK_SENTENCE in base
    content = base.replace(
        RISK_SENTENCE,
        f"{RISK_SENTENCE}{CUSTOMER}提出渠道收缩诉求，涉及{STRATEGY}调整。",
        1,
    )
    return ImportSourceInput(
        project_id="LLD",
        uploaded_name="敏感材料.md",
        uploaded_bytes=content.encode("utf-8"),
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
        preferred_mode="realtime",
    )


def test_outbound_payload_and_model_call_log_carry_no_raw_terms(
    demo_root: Path,
    make_container,
) -> None:
    record: list[dict] = []
    environ = {
        **MOCK_ENVIRON,
        "REDACTION_CUSTOMER_NAMES": CUSTOMER,
        "REDACTION_STRATEGY_TERMS": STRATEGY,
    }
    container = make_container(environ=environ, record=record)

    report = container.import_source.execute(_command())
    assert report.source_id
    assert record, "实时导入应产生一次出站调用"
    raw_body = record[0]["raw_body"]
    assert CUSTOMER not in raw_body
    assert STRATEGY not in raw_body

    db_path = demo_root / "data/local_state/product_intelligence.db"
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, project_id, task_type, workflow_run_id, correlation_id,
                   source_ids_json, baseline_version, model_label, prompt_version,
                   schema_version, result_mode, status, error_code, redacted
            FROM model_call_logs
            """
        ).fetchall()
    assert rows, "实时导入应记录模型调用日志"
    for row in rows:
        text_fields = [str(value) for value in row[:-1] if value is not None]
        assert all(CUSTOMER not in field for field in text_fields)
        assert all(STRATEGY not in field for field in text_fields)
        assert row[-1] == 1, "日志行 redacted 标记必须为 1"
