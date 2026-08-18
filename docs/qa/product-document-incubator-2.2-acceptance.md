# 产品文档孵化器 2.2 验收报告

验收范围：2.2 的项目脚手架、Raw 到 Wiki Ingest、L3/L4 本地确认、Wiki 驱动孵化、跨项目隔离与项目根目录重新定位。自动化命令及结果记录在本文件提交对应的 T12 报告中。

## 质量门禁说明

- 全量测试：`../../.venv/bin/pytest -q`，结果为 `1040 passed`。
- 覆盖率：全量执行结果为 92%，低于原 94% 目标；这是既有覆盖率债务，Owner 已接受，不作为本次 15 人日范围内的阻断项。
- 仓库级 `ruff check .`：仅历史 `.superpowers/sdd/2026-07-29-product-intelligence-lightweight/evidence/t10-t11/joint_acceptance.py` 的 E501/I001 失败；Owner 已接受为既有质量债。
- 仓库级 `ruff format --check .`：报告 31 个既有范围外文件待格式化；Owner 已接受为既有质量债。T12 的四个 Python 验收文件已通过 scoped Ruff 与格式检查。

| AC | 自动化测试节点 | 人工证据路径 | 结果 | Commit SHA |
| --- | --- | --- | --- | --- |
| AC-01 | `tests/integration/use_cases/test_manage_projects.py::test_scaffolder_builds_complete_2_2_wiki_llm_tree` | 新项目根目录的 `README.md`、`AGENTS.md` | PASS | `f8e5047` |
| AC-02 | `test_manage_projects.py::test_create_project_scaffolds_complete_wiki_atomically` | 项目根目录的 `raw/`、`wiki/`、`schema/`、`exports/`、`.incubator/` | PASS | `f8e5047` |
| AC-03 | `test_manage_projects.py::test_scaffolder_builds_complete_2_2_wiki_llm_tree` | `README.md` | PASS | `f8e5047` |
| AC-04 | `test_manage_projects.py::test_scaffolder_builds_complete_2_2_wiki_llm_tree` | `AGENTS.md`、`schema/ingest-contract.md`、`wiki/index.md` | PASS | `f8e5047` |
| AC-05 | `test_manage_projects.py::test_create_project_failure_leaves_no_registered_or_visible_project` | 无残留项目目录及数据库项目记录 | PASS | `f8e5047` |
| AC-06 | `test_archive_raw_source.py::test_archive_marks_new_2_2_project_material_pending_ingest` | `raw/` 与 `.incubator/source-index.json` | PASS | `f8e5047` |
| AC-07 | `test_wiki_ingest.py::test_ingest_archived_l2_source_updates_complete_wiki` | `schema/ingest-contract.md` | PASS | `f8e5047` |
| AC-08 | `test_wiki_ingest.py::test_ingest_archived_l2_source_updates_complete_wiki` | `wiki/sources/` | PASS | `f8e5047` |
| AC-09 | `test_wiki_ingest.py::test_ingest_archived_l2_source_updates_complete_wiki` | `wiki/topics/` | PASS | `f8e5047` |
| AC-10 | `test_wiki_ingest.py::test_ingest_archived_l2_source_updates_complete_wiki` | `wiki/index.md` | PASS | `f8e5047` |
| AC-11 | `test_wiki_transaction.py::test_audit_log_renders_one_deterministic_ingest_entry` | `wiki/log.md` | PASS | `f8e5047` |
| AC-12 | `test_wiki_ingest.py::test_ingest_archived_l2_source_updates_complete_wiki` | 来源页与主题页的冲突区段 | PASS | `f8e5047` |
| AC-13 | `test_wiki_ingest.py::test_successful_duplicate_returns_without_gateway_or_wiki_change` | `wiki/log.md` 与 `.incubator/transactions/` | PASS | `f8e5047` |
| AC-14 | `test_wiki_ingest.py::test_gateway_failure_records_safe_error_and_preserves_wiki` | 失败前后 `wiki/` 文件哈希 | PASS | `f8e5047` |
| AC-15 | `test_materials_page.py`、`test_local_wiki_ingest.py` | “原始材料”页面的状态与错误码 | PASS | `f8e5047` |
| AC-16 | `test_incubate_document.py::test_incubation_lists_only_ingested_sources` | “文档孵化”来源清单 | PASS | `f8e5047` |
| AC-17 | `test_wiki_incubation_flow.py::test_l2_archive_ingest_wiki_incubate_publish` | `raw/` 文件 SHA-256 前后比对 | PASS | `f8e5047` |
| AC-18 | `test_wiki_incubation_flow.py::test_l4_archive_local_edit_confirm_without_gateway` | 本地草稿目录与模型调用记录 | PASS | `f8e5047` |
| AC-19 | `test_wiki_incubation_flow.py::test_two_projects_in_different_roots_never_cross_read` | 两个项目根目录文件树哈希 | PASS | `f8e5047` |
| AC-20 | `test_incubator_restart_recovery.py::test_active_project_is_restored_after_container_restart` | 2.0/2.1 历史项目库与设置文件 | PASS | `f8e5047` |
| AC-21 | `test_manage_projects.py::test_scaffolder_builds_complete_2_2_wiki_llm_tree` | Obsidian 打开项目根目录后的 `README.md` 链接 | PASS | `f8e5047` |
| AC-22 | `test_wiki_ingest.py::test_ingest_archived_l2_source_updates_complete_wiki` | `wiki/current/`、`wiki/versions/` 与 Manifest 哈希 | PASS | `f8e5047` |
| AC-23 | `test_wiki_incubation_flow.py::test_two_projects_in_different_roots_never_cross_read` | 两个 Owner 选择的父目录 | PASS | `f8e5047` |
| AC-24 | `test_manage_projects.py::test_create_registers_owner_selected_root` | 中央数据库 `project_root_path` | PASS | `f8e5047` |
| AC-25 | `test_manage_projects.py::test_scaffolder_requires_an_explicit_parent_root` | 项目中心创建失败提示 | PASS | `f8e5047` |
| AC-26 | `test_manage_projects.py::test_create_project_rejects_duplicate_id_without_touching_existing_files` | 目标目录原文件哈希 | PASS | `f8e5047` |
| AC-27 | `test_manage_projects.py::test_create_registers_owner_selected_root` | 重启后的项目卡片路径 | PASS | `f8e5047` |
| AC-28 | `test_wiki_incubation_flow.py::test_move_relocate_then_continue_ingest` | 项目中心“路径不可用”与重新定位动作 | PASS | `f8e5047` |
| AC-29 | `test_wiki_incubation_flow.py::test_two_projects_in_different_roots_never_cross_read` | A、B 独立项目目录及中央数据库记录 | PASS | `f8e5047` |

`f8e5047` 在验收提交完成后替换为该提交的实际 SHA。
