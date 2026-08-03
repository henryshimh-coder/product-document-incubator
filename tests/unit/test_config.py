from __future__ import annotations

import importlib

import pytest


def test_load_settings_rejects_invalid_schema_version(tmp_path):
    """Catches accepting an empty schema version and starting with an undefined contract."""
    container = importlib.import_module("src.application.container")
    app_yaml = tmp_path / "app.yaml"
    schema_yaml = tmp_path / "schema.yaml"
    app_yaml.write_text(
        """
app:
  name: 产品智策
  project_id: LLD
  default_query_scope: effective
  max_upload_mb: 20
  accepted_extensions: [pdf, docx, txt, md]
  demo_mode: true
""".strip(),
        encoding="utf-8",
    )
    schema_yaml.write_text("schema_version: ''\n", encoding="utf-8")

    with pytest.raises(container.ConfigurationError, match="schema_version"):
        container.load_settings(app_yaml, schema_yaml)


def test_build_container_loads_valid_app_and_schema_configuration(tmp_path):
    """Catches starting the app without the project and schema settings it consumes."""
    container_module = importlib.import_module("src.application.container")
    app_yaml = tmp_path / "app.yaml"
    schema_yaml = tmp_path / "schema.yaml"
    app_yaml.write_text(
        """
app:
  name: 产品智策
  project_id: LLD
  default_query_scope: effective
  max_upload_mb: 20
  accepted_extensions: [pdf, docx, txt, md]
  demo_mode: true
""".strip(),
        encoding="utf-8",
    )
    schema_yaml.write_text("schema_version: '1.0'\n", encoding="utf-8")

    result = container_module.build_container(app_yaml, schema_yaml)

    assert result.settings.model_dump() == {
        "name": "产品智策",
        "project_id": "LLD",
        "default_query_scope": "effective",
        "max_upload_mb": 20,
        "accepted_extensions": ("pdf", "docx", "txt", "md"),
        "demo_mode": True,
        "schema_version": "1.0",
    }
    assert result.import_source is None


def test_build_container_composes_real_import_source_when_runtime_configuration_is_present(
    tmp_path,
    monkeypatch,
):
    from scripts.bootstrap_demo import bootstrap

    container_module = importlib.import_module("src.application.container")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    app_yaml = config_dir / "app.yaml"
    schema_yaml = config_dir / "schema.yaml"
    app_yaml.write_text(
        """
app:
  name: 产品智策
  project_id: LLD
  default_query_scope: effective
  max_upload_mb: 20
  accepted_extensions: [pdf, docx, txt, md]
  demo_mode: true
""".strip(),
        encoding="utf-8",
    )
    schema_yaml.write_text("schema_version: '1.0'\n", encoding="utf-8")
    bootstrap(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = container_module.build_container(
        app_yaml,
        schema_yaml,
        environ={
            "DIFY_BASE_URL": "https://dify.example.test/v1",
            "DIFY_INGEST_API_KEY": "app-ingest-secret",
            "DIFY_QUERY_API_KEY": "app-query-secret",
            "DIFY_LINT_API_KEY": "app-lint-secret",
        },
    )

    assert result.import_source is not None
    assert result.import_source.__class__.__name__ == "ImportSource"


def test_build_container_runs_real_ingest_vertical_slice_from_composition_root(
    tmp_path,
    monkeypatch,
):
    import json
    import sqlite3
    from datetime import UTC, date, datetime

    import httpx

    from scripts.bootstrap_demo import bootstrap
    from src.application.dto.ingest import ImportSourceInput
    from src.domain.enums import AuthorityLevel, KnowledgeStatus, SecurityLevel
    from src.domain.models import KnowledgeCard
    from src.infrastructure.db.repositories import SqliteKnowledgeRepository

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    app_yaml = config_dir / "app.yaml"
    schema_yaml = config_dir / "schema.yaml"
    app_yaml.write_text(
        """
app:
  name: 产品智策
  project_id: LLD
  default_query_scope: effective
  max_upload_mb: 20
  accepted_extensions: [pdf, docx, txt, md]
  demo_mode: true
""".strip(),
        encoding="utf-8",
    )
    schema_yaml.write_text("schema_version: '1.0'\n", encoding="utf-8")
    bootstrap(tmp_path)
    db_path = tmp_path / "data/local_state/product_intelligence.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE projects SET allow_external_model = 1 WHERE id = 'LLD'")
    now = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)
    SqliteKnowledgeRepository(db_path).upsert_cards(
        [
            KnowledgeCard(
                id="RULE-LLD-001",
                project_id="LLD",
                card_type="rule",
                title="目标客群",
                content="当前目标客群规则。",
                status=KnowledgeStatus.EFFECTIVE,
                product_version="LLD-724_1",
                applicable_scope="演示",
                source_refs=["SRC-BASE"],
                authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
                owner="产品经理",
                created_at=now,
                updated_at=now,
            )
        ]
    )

    def http_factory():
        def handler(request):
            inputs = json.loads(request.content)["inputs"]
            chunk = inputs["source_chunks"][0]
            result = {
                "schema_version": "1.0",
                "task_id": inputs["task_id"],
                "summary": "组合根纵切成功",
                "items": [
                    {
                        "item_id": "ITEM-001",
                        "item_type": "professional_opinion",
                        "title": "客群冲突",
                        "content": "建议收紧目标客群。",
                        "target_card_id": "RULE-LLD-001",
                        "result_type": "conflict_discussion",
                        "status": "conflict",
                        "source_citations": [
                            {
                                "source_id": inputs["source"]["id"],
                                "chunk_id": chunk["chunk_id"],
                                "locator": chunk["locator"],
                                "excerpt": chunk["text"][:8],
                            }
                        ],
                        "confidence": 0.9,
                        "uncertainty": "待会议决定",
                    }
                ],
                "relations": [
                    {
                        "source_id": "ITEM-001",
                        "relation_type": "conflicts_with",
                        "target_id": "RULE-LLD-001",
                    }
                ],
            }
            return httpx.Response(
                200,
                json={
                    "workflow_run_id": "WF-COMPOSED",
                    "data": {"outputs": {"result": result}},
                },
            )

        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.chdir(tmp_path)
    container_module = importlib.import_module("src.application.container")
    container = container_module.build_container(
        app_yaml,
        schema_yaml,
        environ={
            "DIFY_BASE_URL": "https://dify.example.test/v1",
            "DIFY_INGEST_API_KEY": "app-ingest-secret",
            "DIFY_QUERY_API_KEY": "app-query-secret",
            "DIFY_LINT_API_KEY": "app-lint-secret",
        },
        http_factory=http_factory,
    )
    content = (
        "# 风险意见\n建议收紧目标客群。\n" + "用于满足安全外调覆盖率预算的脱敏正文。" * 1500
    ).encode()
    report = container.import_source.execute(
        ImportSourceInput(
            project_id="LLD",
            uploaded_name="风险意见.md",
            uploaded_bytes=content,
            source_type="risk_opinion",
            authority_level=AuthorityLevel.PROFESSIONAL_OPINION,
            source_department="风险",
            provider=None,
            document_date=date(2026, 7, 29),
            document_version="v1.0",
            applicable_baseline_version="LLD-724_1",
            security_level=SecurityLevel.L2_INTERNAL,
            is_redacted_confirmed=True,
            allow_external_model=True,
            is_sandbox=False,
        )
    )

    assert report.conflict_count == 1
    assert report.model_call_id is not None
