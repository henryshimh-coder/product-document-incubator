"""T14-R01：默认组合根从项目根 .env 装配实时服务的验收测试。

覆盖评审四项要求：`.env` 有效时装配三项服务；空 Key 保持本地模式；
重复 Key 拒绝；Key 不进入异常文本或 repr。测试通过 monkeypatch.delenv
登记原始环境，teardown 时清除 load_dotenv 写入的进程变量，避免泄漏。
"""

from __future__ import annotations

import importlib

import pytest

from scripts.bootstrap_demo import bootstrap

_DIFY_NAMES = (
    "DIFY_BASE_URL",
    "DIFY_INGEST_API_KEY",
    "DIFY_QUERY_API_KEY",
    "DIFY_LINT_API_KEY",
)

_SECRET_INGEST = "app-ingest-secret-t14r01"
_SECRET_QUERY = "app-query-secret-t14r01"
_SECRET_LINT = "app-lint-secret-t14r01"

_APP_YAML = """
app:
  name: 产品智策
  project_id: LLD
  default_query_scope: effective
  max_upload_mb: 20
  accepted_extensions: [pdf, docx, txt, md]
  demo_mode: true
""".strip()

_SCHEMA_YAML = "schema_version: '1.0'\nlint_input_contract_version: '2.0'\n"


def _project(tmp_path, monkeypatch, dotenv_body: str):
    for name in _DIFY_NAMES:
        monkeypatch.delenv(name, raising=False)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    app_yaml = config_dir / "app.yaml"
    schema_yaml = config_dir / "schema.yaml"
    app_yaml.write_text(_APP_YAML, encoding="utf-8")
    schema_yaml.write_text(_SCHEMA_YAML, encoding="utf-8")
    (tmp_path / ".env").write_text(dotenv_body, encoding="utf-8")
    bootstrap(tmp_path)
    monkeypatch.chdir(tmp_path)
    return app_yaml, schema_yaml


def test_default_container_assembles_realtime_services_from_dotenv(tmp_path, monkeypatch):
    """Catches the README .env path silently staying in local-only mode."""
    container_module = importlib.import_module("src.application.container")
    app_yaml, schema_yaml = _project(
        tmp_path,
        monkeypatch,
        "DIFY_BASE_URL=https://dify.example.test/v1\n"
        f"DIFY_INGEST_API_KEY={_SECRET_INGEST}\n"
        f"DIFY_QUERY_API_KEY={_SECRET_QUERY}\n"
        f"DIFY_LINT_API_KEY={_SECRET_LINT}\n",
    )

    container = container_module.build_container(app_yaml, schema_yaml)

    assert container.import_source is not None
    assert container.query is not None
    assert container.lint is not None
    assert container.dashboard is not None


def test_default_container_stays_local_only_when_dotenv_keys_empty(tmp_path, monkeypatch):
    """Catches empty .env keys being treated as live configuration."""
    container_module = importlib.import_module("src.application.container")
    app_yaml, schema_yaml = _project(
        tmp_path,
        monkeypatch,
        "DIFY_BASE_URL=\nDIFY_INGEST_API_KEY=\nDIFY_QUERY_API_KEY=\nDIFY_LINT_API_KEY=\n",
    )

    container = container_module.build_container(app_yaml, schema_yaml)

    assert container.import_source is None
    assert container.query is None
    assert container.lint is None
    assert container.dashboard is not None
    assert container.record_decision is not None
    assert container.review_change_request is not None
    assert container.publish_baseline is not None
    assert container.trace is not None


def test_default_container_rejects_duplicate_keys_from_dotenv(tmp_path, monkeypatch):
    """Catches one shared key silently merging the three governed workflows."""
    container_module = importlib.import_module("src.application.container")
    app_yaml, schema_yaml = _project(
        tmp_path,
        monkeypatch,
        "DIFY_BASE_URL=https://dify.example.test/v1\n"
        f"DIFY_INGEST_API_KEY={_SECRET_INGEST}\n"
        f"DIFY_QUERY_API_KEY={_SECRET_INGEST}\n"
        f"DIFY_LINT_API_KEY={_SECRET_LINT}\n",
    )

    with pytest.raises(container_module.ConfigurationError) as captured:
        container_module.build_container(app_yaml, schema_yaml)

    assert "distinct" in str(captured.value)


def test_dotenv_keys_never_appear_in_exception_text_or_settings_repr(tmp_path, monkeypatch):
    """Catches API keys leaking through validation errors or reprs."""
    from src.infrastructure.gateways.composition import DifyGatewaySettings

    container_module = importlib.import_module("src.application.container")
    app_yaml, schema_yaml = _project(
        tmp_path,
        monkeypatch,
        "DIFY_BASE_URL=https://dify.example.test/v1\n"
        f"DIFY_INGEST_API_KEY={_SECRET_INGEST}\n"
        f"DIFY_QUERY_API_KEY={_SECRET_INGEST}\n"
        f"DIFY_LINT_API_KEY={_SECRET_LINT}\n",
    )

    with pytest.raises(container_module.ConfigurationError) as captured:
        container_module.build_container(app_yaml, schema_yaml)
    exception_text = str(captured.value)
    assert _SECRET_INGEST not in exception_text
    assert _SECRET_LINT not in exception_text

    settings = DifyGatewaySettings(
        base_url="https://dify.example.test/v1",
        ingest_api_key=_SECRET_INGEST,
        query_api_key=_SECRET_QUERY,
        lint_api_key=_SECRET_LINT,
    )
    for secret in (_SECRET_INGEST, _SECRET_QUERY, _SECRET_LINT):
        assert secret not in repr(settings)
        assert secret not in str(settings)
