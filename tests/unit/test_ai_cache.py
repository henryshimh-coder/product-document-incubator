from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from src.infrastructure.db.migrations import migrate


class CurrentSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    evidence_sufficiency: str


def _cache_module():
    return importlib.import_module("src.infrastructure.cache.ai_cache")


def _identity(baseline_version: str = "LLD-724_1"):
    module = _cache_module()
    return module.CacheIdentity(
        task_type="query",
        source_sha256="a" * 64,
        baseline_version=baseline_version,
        prompt_version="query-v1",
        model_label="dify-query",
        schema_version="1.0",
        question="当前目标客群是什么？",
    )


def _cache(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "state.db"
    migrate(db_path)
    return _cache_module().AiCache(db_path), db_path


def test_build_cache_key_uses_exact_canonical_fields_and_normalized_question():
    """Catches field reordering or whitespace drift causing incorrect cache reuse."""
    module = _cache_module()

    key = module.build_cache_key(
        task_type="query",
        source_sha256="source-digest",
        baseline_version="LLD-724_1",
        prompt_version="query-v1",
        model_label="dify-query",
        schema_version="1.0",
        question=" 当前  目标客群\n是什么？ ",
    )

    assert key == "f3a786e133bb6ac98ad45278c495bc95b7e09316bfbad46dd40d2862958c5e64"


def test_cache_persists_canonical_utf8_file_and_sqlite_index(tmp_path: Path, monkeypatch):
    """Catches cache writes omitting the fixed file, hash, or searchable metadata index."""
    cache, db_path = _cache(tmp_path, monkeypatch)
    identity = _identity()
    result = {"evidence_sufficiency": "sufficient", "answer": "当前客群规则"}

    cache.put(identity, result)

    expected_json = '{"answer":"当前客群规则","evidence_sufficiency":"sufficient"}'
    cache_file = tmp_path / "data" / "local_state" / "cache" / f"{identity.cache_key}.json"
    assert cache_file.read_text(encoding="utf-8") == expected_json
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT task_type, source_sha256, baseline_version, prompt_version,
                   model_label, schema_version, response_json
            FROM cache_entries WHERE cache_key = ?
            """,
            (identity.cache_key,),
        ).fetchone()
    assert row == (
        "query",
        "a" * 64,
        "LLD-724_1",
        "query-v1",
        "dify-query",
        "1.0",
        expected_json,
    )


def test_cache_does_not_cross_baseline(tmp_path: Path, monkeypatch):
    """Catches cached analysis being reused for a different effective baseline."""
    cache, _ = _cache(tmp_path, monkeypatch)
    cache.put(_identity("LLD-724_1"), {"answer": "旧规则", "evidence_sufficiency": "sufficient"})

    assert cache.get(_identity("LLD-724_2")) is None


def test_cache_production_path_cannot_be_overridden(tmp_path: Path):
    """Catches callers redirecting production cache writes outside the fixed state root."""
    module = _cache_module()

    with pytest.raises(TypeError):
        module.AiCache(tmp_path / "state.db", cache_dir=tmp_path / "other")


def test_cache_rejects_tampered_file_or_index(tmp_path: Path, monkeypatch):
    """Catches file or SQLite tampering bypassing exact hash and metadata checks."""
    cache, db_path = _cache(tmp_path, monkeypatch)
    identity = _identity()
    cache.put(identity, {"answer": "可信结果", "evidence_sufficiency": "sufficient"})
    cache_file = tmp_path / "data" / "local_state" / "cache" / f"{identity.cache_key}.json"
    cache_file.write_text('{"answer":"篡改结果"}', encoding="utf-8")

    assert cache.get(identity) is None

    cache.put(identity, {"answer": "可信结果", "evidence_sufficiency": "sufficient"})
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE cache_entries SET baseline_version = 'LLD-INVENTED' WHERE cache_key = ?",
            (identity.cache_key,),
        )
    assert cache.get(identity) is None


def test_cache_revalidates_bytes_against_current_schema(tmp_path: Path, monkeypatch):
    """Catches stale cached JSON bypassing the caller's current strict output schema."""
    cache, _ = _cache(tmp_path, monkeypatch)
    identity = _identity()
    cache.put(
        identity,
        {
            "answer": "旧结构",
            "evidence_sufficiency": "sufficient",
            "removed_field": True,
        },
    )

    assert cache.get(identity, schema=CurrentSchema) is None


def test_cache_rejects_invalid_json_even_if_file_and_index_are_tampered_together(
    tmp_path: Path,
    monkeypatch,
):
    """Catches coordinated stale bytes being trusted without JSON parsing."""
    cache, db_path = _cache(tmp_path, monkeypatch)
    identity = _identity()
    cache.put(identity, {"answer": "可信结果", "evidence_sufficiency": "sufficient"})
    invalid = b"{invalid-json"
    cache_file = tmp_path / "data" / "local_state" / "cache" / f"{identity.cache_key}.json"
    cache_file.write_bytes(invalid)
    import hashlib

    digest = hashlib.sha256(invalid).hexdigest()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE cache_entries SET response_json = ?, response_sha256 = ?
            WHERE cache_key = ?
            """,
            (invalid.decode(), digest, identity.cache_key),
        )

    assert cache.get(identity) is None
