# 产品文档孵化器 2.0 验收报告

验收实现 SHA：`157dbd1`
验收日期：2026-08-12
范围：2.0 首期（单 Owner、多项目、本地优先）

| 编号 | 验收项 | 证据 | 状态 |
| --- | --- | --- | --- |
| A01 | 新建项目自动建立 Wiki-LLM 目录 | `test_manage_projects.py` | PASS |
| A02 | `raw/` 归档、哈希与去重 | `test_archive_raw_source.py` | PASS |
| A03 | 首版候选方案孵化 | `test_incubate_document.py` | PASS |
| A04 | Owner 发布、增量与恢复 | `test_publish_document_draft.py` | PASS |
| A05 | 当前 Markdown 单文件下载 | `test_export_current_document.py` | PASS |
| A06 | 授权项目标题结构建议 | `test_suggest_document_structure.py` | PASS |
| A07 | 双项目隔离和越权阻断 | `test_incubator_full_success.py`、`test_incubator_cross_project_isolation.py` | PASS |
| A08 | LLD 干跑零写入、正式迁移幂等 | `test_migrate_lld_to_v2.py`；临时库演练输出 `DRY_RUN_OK → MIGRATED → ALREADY_MIGRATED` | PASS |
| A09 | 重启恢复与项目库离线校验 | `test_incubator_restart_recovery.py`、`test_validate_incubator.py`；临时库输出 `INCUBATOR_VALIDATION_OK projects=1 current_projects=1 sources=1` | PASS |
| A10 | 成本门禁 | 计划 13.5 人日＋1.5 人日缓冲；本期未扩展多人、富文本、文件监听、DOCX/PDF 导出 | PASS |

自动化验收命令：

```bash
.venv/bin/python -m pytest -q
.venv/bin/coverage run -m pytest
.venv/bin/coverage report --include='src/domain/*,src/application/*'
.venv/bin/ruff check src scripts tests streamlit_app.py
.venv/bin/ruff format --check src scripts tests streamlit_app.py
.venv/bin/python -m compileall -q src scripts tests
```

残余风险：真实 Dify 的连通性取决于 Owner 配置的 `DIFY_DOCUMENT_API_KEY` 与工作流契约；自动化测试使用严格契约替身验证本地边界与数据隔离。
