from __future__ import annotations

import importlib

import pytest


def test_app_settings_defaults_lint_contract_for_non_lint_construction():
    """Catches a Lint-only setting breaking Home, Ingest, or Query test containers."""
    container = importlib.import_module("src.application.container")

    settings = container.AppSettings(
        name="产品智策",
        project_id="LLD",
        default_query_scope="effective",
        max_upload_mb=20,
        accepted_extensions=("pdf", "docx", "txt", "md"),
        demo_mode=True,
        schema_version="1.0",
    )

    assert settings.lint_input_contract_version == "2.0"


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
    schema_yaml.write_text(
        "schema_version: ''\nlint_input_contract_version: '2.0'\n",
        encoding="utf-8",
    )

    with pytest.raises(container.ConfigurationError, match="schema_version"):
        container.load_settings(app_yaml, schema_yaml)


def test_load_settings_rejects_legacy_lint_input_contract(tmp_path):
    """Catches deploying the v2 local shape against an old v1 Dify Lint workflow."""
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
    schema_yaml.write_text(
        "schema_version: '1.0'\nlint_input_contract_version: '1.0'\n",
        encoding="utf-8",
    )

    with pytest.raises(container.ConfigurationError, match="lint_input_contract_version"):
        container.load_settings(app_yaml, schema_yaml)


def test_load_settings_defaults_missing_lint_contract_for_local_only_config(tmp_path):
    """Catches a legacy local dashboard schema becoming unusable after Lint v2."""
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
    schema_yaml.write_text("schema_version: '1.0'\n", encoding="utf-8")

    settings = container.load_settings(app_yaml, schema_yaml)

    assert settings.lint_input_contract_version == "2.0"


def test_build_container_requires_explicit_lint_contract_for_live_composition(tmp_path):
    """Catches silently connecting the structured v2 input to a legacy Dify workflow."""
    from scripts.bootstrap_demo import bootstrap

    container = importlib.import_module("src.application.container")
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

    with pytest.raises(
        container.ConfigurationError,
        match="Live Lint deployment requires explicit lint_input_contract_version",
    ):
        container.build_container(
            app_yaml,
            schema_yaml,
            environ={
                "DIFY_BASE_URL": "https://dify.example.test/v1",
                "DIFY_INGEST_API_KEY": "app-ingest-secret",
                "DIFY_QUERY_API_KEY": "app-query-secret",
                "DIFY_LINT_API_KEY": "app-lint-secret",
            },
        )


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
    schema_yaml.write_text(
        "schema_version: '1.0'\nlint_input_contract_version: '2.0'\n",
        encoding="utf-8",
    )

    result = container_module.build_container(app_yaml, schema_yaml)

    assert result.settings.model_dump() == {
        "name": "产品智策",
        "project_id": "LLD",
        "default_query_scope": "effective",
        "max_upload_mb": 20,
        "accepted_extensions": ("pdf", "docx", "txt", "md"),
        "demo_mode": True,
        "schema_version": "1.0",
        "lint_input_contract_version": "2.0",
    }
    assert result.import_source is None
    assert result.lint is None


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
    schema_yaml.write_text(
        "schema_version: '1.0'\nlint_input_contract_version: '2.0'\n",
        encoding="utf-8",
    )
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
    assert result.query is not None
    assert result.query.__class__.__name__ == "RunQuery"
    assert result.lint is not None
    assert result.lint.__class__.__name__ == "RunLint"
    assert result.lint.comparison_builder.input_contract_version == "2.0"
    assert result.record_decision is not None
    assert result.record_decision.__class__.__name__ == "RecordDecision"


def test_build_container_runs_real_query_vertical_slice_with_manifest_and_trusted_citations(
    tmp_path,
    monkeypatch,
):
    """Catches a page-only query implementation or a composition root bypassing safety proof."""
    import json
    import sqlite3
    from datetime import UTC, date, datetime

    import httpx

    from scripts.bootstrap_demo import bootstrap
    from src.application.dto.query import RunQueryInput
    from src.domain.enums import AuthorityLevel, KnowledgeStatus, SecurityLevel
    from src.domain.models import KnowledgeCard, SourceRecord
    from src.infrastructure.db.repositories import (
        SqliteKnowledgeRepository,
        SqliteSourceRepository,
    )
    from src.infrastructure.files.archive import SourceArchive

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
    schema_yaml.write_text(
        "schema_version: '1.0'\nlint_input_contract_version: '2.0'\n",
        encoding="utf-8",
    )
    bootstrap(tmp_path)
    monkeypatch.chdir(tmp_path)
    now = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)
    body = "# 当前产品方案\n当前目标客群是符合准入要求的存量客户。\n" + "已脱敏基线资料。" * 2000
    archived = SourceArchive(project_id="LLD", source_id="SRC-001").save(
        "当前产品方案.md",
        body.encode(),
    )
    db_path = tmp_path / "data/local_state/product_intelligence.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE projects SET allow_external_model = 1 WHERE id = 'LLD'")
    SqliteSourceRepository(db_path).add(
        SourceRecord(
            id="SRC-001",
            project_id="LLD",
            original_filename="当前产品方案.md",
            archive_path=str(archived.path),
            sha256=archived.sha256,
            mime_type="text/markdown",
            size_bytes=archived.size_bytes,
            source_type="formal_document",
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            source_department="产品部",
            provider=None,
            document_date=date(2026, 7, 29),
            document_version="v1.0",
            applicable_baseline_version="LLD-724_1",
            security_level=SecurityLevel.L2_INTERNAL,
            is_redacted=True,
            allow_external_model=True,
            is_sandbox=False,
            ingest_status="completed",
            created_at=now,
        )
    )
    SqliteKnowledgeRepository(db_path).upsert_cards(
        [
            KnowledgeCard(
                id="RULE-LLD-001",
                project_id="LLD",
                card_type="rule",
                title="目标客群",
                content="当前目标客群是符合准入要求的存量客户。",
                status=KnowledgeStatus.EFFECTIVE,
                product_version="LLD-724_1",
                applicable_scope="当前产品方案 > 目标客群",
                source_refs=["SRC-001"],
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
            result = {
                "answer": inputs["effective_cards"][0]["content"],
                "effective_rules": [inputs["effective_cards"][0]["id"]],
                "citations": [inputs["citations"][0]],
                "candidate_notice": None,
                "conflict_notice": None,
                "baseline_version": inputs["baseline_version"],
                "evidence_sufficiency": "sufficient",
                "result_mode": "realtime",
                "model_call_id": "CALL-QUERY-ROOT",
            }
            return httpx.Response(
                200,
                json={
                    "workflow_run_id": "WF-QUERY-ROOT",
                    "data": {"outputs": {"result": result}},
                },
            )

        return httpx.Client(transport=httpx.MockTransport(handler))

    container_module = importlib.import_module("src.application.container")
    container = container_module.build_container(
        app_yaml,
        schema_yaml,
        environ={
            "DIFY_BASE_URL": "https://dify.example.test/v1",
            "DIFY_INGEST_API_KEY": "app-ingest-secret",
            "DIFY_QUERY_API_KEY": "app-query-secret",
            "DIFY_LINT_API_KEY": "app-lint-secret",
            "REDACTION_CUSTOMER_NAMES": "某客户",
            "REDACTION_STRATEGY_TERMS": "北极星计划",
            "REDACTION_FINANCIAL_TERMS": "预算利润",
            "REDACTION_LEADER_NAMES": "王总",
            "REDACTION_UNPUBLISHED_DECISIONS": "未发布决定",
        },
        http_factory=http_factory,
    )

    assert container.query is not None
    response = container.query.execute(
        RunQueryInput(
            project_id="LLD",
            question="当前目标客群是什么？",
            scope="effective",
            historical_version=None,
        )
    )

    assert response.answer == "当前目标客群是符合准入要求的存量客户。"
    assert response.effective_rules == ["RULE-LLD-001"]
    assert response.citations[0].id == "CIT-SRC-001-01"
    assert response.citations[0].filename == "当前产品方案.md"


def test_build_container_repairs_legacy_null_event_before_startup_reconciliation(
    tmp_path,
    monkeypatch,
):
    """Catches the real composition root failing on a pre-1.2 audit row."""
    import json
    import sqlite3

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
    schema_yaml.write_text(
        "schema_version: '1.0'\nlint_input_contract_version: '2.0'\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "data/local_state/product_intelligence.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (version TEXT PRIMARY KEY);
            INSERT INTO schema_migrations(version) VALUES ('1.0'), ('1.1'), ('1.2');
            CREATE TABLE event_logs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL, event_type TEXT NOT NULL,
                entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
                actor TEXT NOT NULL, payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO event_logs VALUES (
                'EVENT-LEGACY', 'LLD', 'legacy_event', 'source',
                'SRC-001', 'system', '{}', '2026-07-29T00:00:00+00:00'
            );
            """
        )
    bootstrap(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE event_logs SET correlation_id = NULL WHERE id = 'EVENT-LEGACY'")

    monkeypatch.chdir(tmp_path)
    runtime = {
        "DIFY_BASE_URL": "https://dify.example.test/v1",
        "DIFY_INGEST_API_KEY": "app-ingest-secret",
        "DIFY_QUERY_API_KEY": "app-query-secret",
        "DIFY_LINT_API_KEY": "app-lint-secret",
    }

    first = container_module.build_container(app_yaml, schema_yaml, environ=runtime)
    second = container_module.build_container(app_yaml, schema_yaml, environ=runtime)

    assert first.import_source is not None
    assert second.import_source is not None
    documents = [
        json.loads(line)
        for line in (tmp_path / "data/local_state/app.log.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(documents) == 1
    assert documents[0]["event_id"] == "EVENT-LEGACY"
    assert documents[0]["correlation_id"] == "LEGACY-EVENT-LEGACY"
    assert documents[0]["level"] == "INFO"


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
    schema_yaml.write_text(
        "schema_version: '1.0'\nlint_input_contract_version: '2.0'\n",
        encoding="utf-8",
    )
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
