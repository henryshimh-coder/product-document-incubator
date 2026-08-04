from __future__ import annotations

import json
from pathlib import Path

from src.domain.services.deterministic_lint import DeterministicRuleFacts
from src.infrastructure.db.connection import connect


class SqliteLintFactReader:
    """Read only persisted audit and relation facts used by deterministic lint."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def for_card(
        self,
        *,
        project_id: str,
        baseline_version: str,
        card_id: str,
        source_ids: tuple[str, ...],
    ) -> DeterministicRuleFacts:
        with connect(self.db_path) as connection:
            audit_rows = connection.execute(
                """
                SELECT source_ids_json FROM model_call_logs
                WHERE project_id = ? AND baseline_version = ? AND authorized = 0
                  AND outbound_chars > 0
                  AND (
                    error_code IS NULL
                    OR error_code NOT IN (
                      'EXTERNAL_CALL_DENIED',
                      'SECURITY_POLICY_DENIED',
                      'OUTBOUND_SAFETY_REJECTED'
                    )
                  )
                  AND (
                    workflow_run_id IS NOT NULL
                    OR status IN ('started', 'failed', 'timeout')
                  )
                """,
                (project_id, baseline_version),
            ).fetchall()
            change_mapping = connection.execute(
                """
                SELECT 1
                FROM relations AS relation
                JOIN knowledge_cards AS target
                  ON target.id = relation.target_id
                 AND target.project_id = relation.project_id
                WHERE relation.project_id = ? AND relation.source_id = ?
                  AND relation.relation_type = 'proposes_change_to'
                  AND target.product_version = ?
                  AND target.status = 'effective'
                UNION ALL
                SELECT 1
                FROM relations AS relation
                JOIN change_requests AS change_request
                  ON change_request.id = relation.target_id
                 AND change_request.project_id = relation.project_id
                JOIN knowledge_cards AS target
                  ON target.id = change_request.target_card_id
                 AND target.project_id = change_request.project_id
                WHERE relation.project_id = ? AND relation.source_id = ?
                  AND relation.relation_type = 'proposes_change_to'
                  AND change_request.status IN ('pending_approval', 'approved', 'published')
                  AND target.product_version = ?
                  AND target.status = 'effective'
                LIMIT 1
                """,
                (
                    project_id,
                    card_id,
                    baseline_version,
                    project_id,
                    card_id,
                    baseline_version,
                ),
            ).fetchone()
            cost_recalculation = connection.execute(
                """
                SELECT 1
                FROM relations AS relation
                JOIN knowledge_cards AS target
                  ON target.id = relation.target_id
                 AND target.project_id = relation.project_id
                WHERE relation.project_id = ? AND relation.source_id = ?
                  AND relation.relation_type = 'recalculated_by'
                  AND target.card_type = 'cost_recalculation_result'
                  AND target.product_version = ?
                  AND target.status = 'effective'
                LIMIT 1
                """,
                (project_id, card_id, baseline_version),
            ).fetchone()
        relevant_sources = set(source_ids)
        unauthorized = any(
            relevant_sources.intersection(json.loads(row["source_ids_json"])) for row in audit_rows
        )
        return DeterministicRuleFacts(
            unauthorized_model_call=unauthorized,
            change_mapping_exists=change_mapping is not None,
            cost_recalculation_exists=cost_recalculation is not None,
        )
