# 产品文档孵化器 2.1 验收报告

## 范围

2.1 覆盖单文件确认归档、固定材料治理、显式版本链、历史分类调整，以及 L3/L4 的本地对照与本地候选。

## 自动化验收证据

| 验收范围 | 自动化证据 | 结果 |
| --- | --- | --- |
| 固定 8 类与两级权威 | `test_material_catalog.py`、`test_material_archive_input.py` | 通过 |
| 材料名称、系列与版本链 | `test_archive_raw_source.py`、`test_list_materials.py` | 通过 |
| 原始文件归档与失败清理 | `test_project_source_archive.py`、`test_archive_raw_source.py` | 通过 |
| 历史材料分类调整 | `test_reclassify_source.py` | 通过 |
| L3/L4 本地对照与高亮 | `test_compare_sensitive_source.py`、`test_sensitive_comparison.py` | 通过 |
| 本地候选与发布完整性复核 | `test_create_local_document_draft.py`、`test_publish_document_draft.py` | 通过 |
| 外部工作流治理元数据 | `test_document_gateway.py`、`test_incubate_document.py` | 通过 |

## 最终质量门禁

执行命令：

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

本报告仅在上述命令全部通过后可标记为通过。`.superpowers/` 下的历史取证脚本不属于应用运行代码，
不纳入 2.1 发布门禁；若执行 `ruff check .`，当前历史脚本仍有独立的 E501/I001 整理项，须在其原
任务范围内维护。

## Owner 验收流程

1. 创建或切换项目，在“原始材料”选择单个文件；确认前检查 `raw/`、数据库和来源索引无新增。
2. 归档 L2 标准材料，验证固定分类、两级权威和项目内 `raw/` 归档。
3. 以不同文件名归档“新版本”，显式选择原材料系列，验证版本链和材料名称继承。
4. 对历史类型执行“调整历史材料分类”，验证原文件、路径和 SHA-256 不变。
5. 对 L3/L4 材料执行“与当前方案对照”和“创建本地候选”，确认页面提示未调用外部模型。
6. 发布本地候选前篡改测试副本，确认发布被 `PUBLISH_SOURCE_INTEGRITY_FAILED` 阻断；恢复后完成 Owner 发布。

配套操作指引见 [`docs/runbook/owner-user-guide-2.1.md`](../runbook/owner-user-guide-2.1.md)。
