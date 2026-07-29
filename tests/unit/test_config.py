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
