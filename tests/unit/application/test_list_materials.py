from __future__ import annotations

from datetime import UTC, datetime

from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.models import SourceRecord


def test_list_materials_groups_explicit_version_chain_without_filename_inference() -> None:
    """Catches a material list joining similarly named files rather than recorded links."""
    from src.application.use_cases.list_materials import ListMaterials

    now = datetime(2026, 8, 14, tzinfo=UTC)
    first = SourceRecord(
        id="SRC-1",
        project_id="PROJECT_A",
        original_filename="需求.md",
        archive_path="raw/a.md",
        sha256="a" * 64,
        mime_type="text/plain",
        size_bytes=1,
        source_type="product_requirement",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        source_department="产品",
        provider=None,
        document_date=now.date(),
        document_version="v1.0",
        applicable_baseline_version="未关联基线",
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted=True,
        allow_external_model=False,
        is_sandbox=False,
        ingest_status="archived",
        created_at=now,
        material_name="需求说明",
        material_series_id="MAT-PROJECT_A-000000000001",
        previous_source_id=None,
    )
    second = first.model_copy(
        update={
            "id": "SRC-2",
            "original_filename": "终稿.md",
            "sha256": "b" * 64,
            "document_version": "v2.0",
            "previous_source_id": "SRC-1",
        }
    )

    class Sources:
        def list_for_project(self, project_id: str):
            assert project_id == "PROJECT_A"
            return [first, second]

    series = ListMaterials(Sources()).list_series("PROJECT_A")

    assert len(series) == 1
    assert [item.source_id for item in series[0].versions] == ["SRC-1", "SRC-2"]
    assert series[0].name == "需求说明"
