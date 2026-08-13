from __future__ import annotations

from pathlib import Path

from src.infrastructure.cache.ai_cache import AiCache, CacheIdentity
from src.infrastructure.db.migrations import migrate


def test_identical_cache_input_is_not_read_by_another_project(tmp_path: Path, monkeypatch) -> None:
    """Catches Project B reading Project A's model result with an identical question."""
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "library/.incubator/product_incubator.db"
    migrate(db_path)
    cache = AiCache(db_path)
    common = dict(
        task_type="query",
        source_sha256="a" * 64,
        baseline_version="V1",
        prompt_version="P1",
        model_label="M1",
        schema_version="1.0",
        question="当前规则？",
    )
    project_a = CacheIdentity(project_id="PROJECT_A", **common)
    project_b = CacheIdentity(project_id="PROJECT_B", **common)

    cache.put(project_a, {"answer": "not a valid query payload"})

    assert project_a.cache_key != project_b.cache_key
    assert cache.get(project_b) is None
