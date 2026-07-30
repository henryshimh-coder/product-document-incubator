from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from src.infrastructure.db.connection import connect
from src.infrastructure.gateways.schemas import (
    IngestWorkflowOutput,
    LintWorkflowOutput,
    QueryWorkflowOutput,
)

CACHE_ROOT = Path("data/local_state/cache")
CURRENT_OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "ingest": IngestWorkflowOutput,
    "query": QueryWorkflowOutput,
    "lint": LintWorkflowOutput,
}


def build_cache_key(
    task_type: str,
    source_sha256: str,
    baseline_version: str,
    prompt_version: str,
    model_label: str,
    schema_version: str,
    question: str = "",
) -> str:
    canonical = "\n".join(
        (
            task_type,
            source_sha256,
            baseline_version,
            prompt_version,
            model_label,
            schema_version,
            " ".join(question.split()),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheIdentity:
    task_type: str
    source_sha256: str
    baseline_version: str
    prompt_version: str
    model_label: str
    schema_version: str
    question: str = ""

    @property
    def cache_key(self) -> str:
        return build_cache_key(
            self.task_type,
            self.source_sha256,
            self.baseline_version,
            self.prompt_version,
            self.model_label,
            self.schema_version,
            self.question,
        )


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class AiCache:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.cache_dir = CACHE_ROOT

    def put(self, identity: CacheIdentity, result: Mapping[str, Any]) -> None:
        cache_key = identity.cache_key
        response_json = _canonical_json(result)
        response_bytes = response_json.encode("utf-8")
        response_sha256 = hashlib.sha256(response_bytes).hexdigest()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / f"{cache_key}.json"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.cache_dir,
                prefix=f".{cache_key}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(response_json)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, target)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO cache_entries (
                    cache_key, task_type, source_sha256, baseline_version,
                    prompt_version, model_label, schema_version, response_json,
                    response_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    task_type = excluded.task_type,
                    source_sha256 = excluded.source_sha256,
                    baseline_version = excluded.baseline_version,
                    prompt_version = excluded.prompt_version,
                    model_label = excluded.model_label,
                    schema_version = excluded.schema_version,
                    response_json = excluded.response_json,
                    response_sha256 = excluded.response_sha256,
                    created_at = excluded.created_at
                """,
                (
                    cache_key,
                    identity.task_type,
                    identity.source_sha256,
                    identity.baseline_version,
                    identity.prompt_version,
                    identity.model_label,
                    identity.schema_version,
                    response_json,
                    response_sha256,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def get(self, identity: CacheIdentity) -> dict[str, Any] | None:
        schema = CURRENT_OUTPUT_SCHEMAS.get(identity.task_type)
        if schema is None:
            return None
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM cache_entries WHERE cache_key = ?",
                (identity.cache_key,),
            ).fetchone()
        if row is None:
            return None
        expected_metadata = (
            identity.task_type,
            identity.source_sha256,
            identity.baseline_version,
            identity.prompt_version,
            identity.model_label,
            identity.schema_version,
        )
        actual_metadata = tuple(
            row[field]
            for field in (
                "task_type",
                "source_sha256",
                "baseline_version",
                "prompt_version",
                "model_label",
                "schema_version",
            )
        )
        if actual_metadata != expected_metadata:
            return None
        cache_file = self.cache_dir / f"{identity.cache_key}.json"
        try:
            response_bytes = cache_file.read_bytes()
            if hashlib.sha256(response_bytes).hexdigest() != row["response_sha256"]:
                return None
            response_json = response_bytes.decode("utf-8")
            if response_json != row["response_json"]:
                return None
            value = json.loads(response_json)
            if not isinstance(value, dict):
                return None
            if _canonical_json(value) != response_json:
                return None
            schema.model_validate(value)
            return value
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValidationError):
            return None
