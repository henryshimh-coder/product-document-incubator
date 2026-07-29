from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConfigurationError(ValueError):
    """Raised when application configuration cannot establish a valid contract."""


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    default_query_scope: Literal["effective", "effective_with_notices", "historical"]
    max_upload_mb: int = Field(ge=1, le=20)
    accepted_extensions: tuple[str, ...] = Field(min_length=1)
    demo_mode: bool
    schema_version: str = Field(min_length=1)


@dataclass(frozen=True)
class AppContainer:
    settings: AppSettings


def load_settings(app_path: Path, schema_path: Path) -> AppSettings:
    try:
        app_document = yaml.safe_load(app_path.read_text(encoding="utf-8"))
        schema_document = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        app_data = app_document["app"]
        schema_version = schema_document["schema_version"]
        return AppSettings(**app_data, schema_version=schema_version)
    except (OSError, KeyError, TypeError, yaml.YAMLError, ValidationError) as error:
        raise ConfigurationError(f"Invalid application configuration: {error}") from error


def build_container(
    app_path: Path = Path("config/app.yaml"),
    schema_path: Path = Path("config/schema.yaml"),
) -> AppContainer:
    return AppContainer(settings=load_settings(app_path, schema_path))
