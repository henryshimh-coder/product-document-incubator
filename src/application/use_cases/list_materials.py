from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.application.ports.repositories import SourceRepository
from src.domain.material_catalog import authority_label, material_type_label


@dataclass(frozen=True)
class MaterialVersionView:
    source_id: str
    filename: str
    material_version: str
    source_type_label: str
    authority_label: str
    security_level: str


@dataclass(frozen=True)
class MaterialSeriesView:
    series_id: str
    name: str
    versions: tuple[MaterialVersionView, ...]


class ListMaterials:
    def __init__(self, sources: SourceRepository) -> None:
        self.sources = sources

    def list_series(self, project_id: str) -> list[MaterialSeriesView]:
        records = self.sources.list_for_project(project_id)
        grouped: dict[str, list] = {}
        for record in records:
            series_id = record.material_series_id or f"LEGACY-{record.id}"
            grouped.setdefault(series_id, []).append(record)
        result = []
        for series_id, items in grouped.items():
            ordered = self._order_chain(items)
            first = ordered[0]
            result.append(
                MaterialSeriesView(
                    series_id=series_id,
                    name=first.material_name or Path(first.original_filename).stem,
                    versions=tuple(
                        MaterialVersionView(
                            source_id=item.id,
                            filename=item.original_filename,
                            material_version=item.document_version,
                            source_type_label=material_type_label(item.source_type),
                            authority_label=authority_label(item.authority_level),
                            security_level=item.security_level.value,
                        )
                        for item in ordered
                    ),
                )
            )
        return sorted(result, key=lambda item: (item.name, item.series_id))

    @staticmethod
    def _order_chain(items: list) -> list:
        by_previous = {item.previous_source_id: item for item in items}
        roots = [item for item in items if item.previous_source_id is None]
        if len(roots) != 1:
            raise ValueError("MATERIAL_SERIES_FORKED")
        ordered = []
        current = roots[0]
        while current is not None:
            ordered.append(current)
            current = by_previous.get(current.id)
            if current in ordered:
                raise ValueError("MATERIAL_SERIES_CYCLE")
        if len(ordered) != len(items):
            raise ValueError("MATERIAL_SERIES_BROKEN")
        return ordered
