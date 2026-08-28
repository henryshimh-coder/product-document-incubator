# Product Document Incubator 2.3 验收记录

> 状态：**2.3.1 节点 5 最终加固通过，待 Owner 授权提交/合并**
> 结论：本次差异聚焦验证、静态门禁和独立代码复审均通过，未发现尚未关闭的 Critical/Important。隔离真实 Owner 项目库后，1198 项回归通过；唯一既有 SQLite/WAL 顺序性用例隔离复核通过。由于该用例在单进程全量顺序下仍可波动，本报告不把单次全量命令表述为无条件全绿。

## 版本与证据口径

- 当前提交基线 HEAD：`199143f`；2.3.1 节点 5 加固仍为未提交工作区差异，最终提交 SHA 由 Owner 授权提交后补录。
- 证据时间：2026-08-28（America/Los_Angeles）。
- 下表只引用本轮新鲜执行的命令与断言；不用历史报告的通过数外推。

## AC-01～AC-20

| AC | 结果 | 可复验证据 |
| --- | --- | --- |
| AC-01 Owner 授权 | PASS | `test_missing_owner_authorization_never_reaches_gateway` 五组参数覆盖请求者、材料两个授权标记和 L3/L4，断言 Fake Gateway `calls == []`；Owner+L2 成功用例有 1 次调用。 |
| AC-02 业务词放行 | PASS | `test_owner_confirmed_l2_business_terms_reach_gateway_with_hard_ids_masked` 断言“某银行”和“灰度策略”原文进入 Fake Gateway。 |
| AC-03 硬标识遮盖 | PASS（组合证据） | Owner Fake Gateway 用例断言手机/邮箱明文不在载荷，只有 `[已脱敏:phone]` / `[已脱敏:email]`；`test_redacts_supported_identifiers_deterministically` 覆盖手机、身份证、银行卡、邮箱四种占位符；Gateway residue 参数测试分别在外调前拒绝四种明文。 |
| AC-04 Raw 不变 | PASS | Owner 成功 Ingest 用例保存并比对 Ingest 前后 Raw bytes 与 SHA-256；删除用例额外断言回收区中原始 bytes 与入库 SHA-256 一致。 |
| AC-05 比例口径 | PASS | `test_wiki_coverage_counts_only_source_chunk_text` 证明分子只计 `source_chunks[*].text`；成功审计用例按 `source_chunk_chars / source_total_chars` 重算并用 `pytest.approx` 对比入库值。 |
| AC-06 覆盖边界 | PASS | 普通/旧调用路径仍执行 25% 上限；只有精确匹配 `WikiIngestWorkflowInput`、Owner 明确授权且材料为 L1/L2 的 Wiki 特例允许增加来源分段。Schema identity、模式、载荷摘要与覆盖值均纳入 HMAC，旧 Ingest Schema 试图使用特例会在外调前拒绝。 |
| AC-07 20,000 字符上限 | PASS | Wiki Owner 特例仍由本地证明工厂强制规范化完整载荷不超过 20,000 字符；本次最多读取 20 个 Wiki 分段不改变该绝对上限。 |
| AC-08 未授权零外发 | PASS | Owner 授权参数测试对 Agent、未确认脱敏、未允许外调均断言 `calls == []`；审计失败测试不保存正文。 |
| AC-09 L3/L4 零外发 | PASS | 同一参数测试的 L3/L4 两组断言 Fake Gateway `calls == []`；`test_local_l3_l4_ingest_use_cases_have_no_gateway_dependency` 证明本地路径无 Gateway 依赖。 |
| AC-10 证明不可伪造 | PASS | `test_gateway_rejects_proof_bound_to_an_earlier_payload_before_external_call`、`test_gateway_rejects_tampered_or_fake_signed_proof` 五组字段及 `test_gateway_rejects_coverage_mode_tamper_when_both_modes_are_valid` 均在外调前拒绝。 |
| AC-11 页面告知 | PASS | `test_materials_page_explains_owner_outbound_authorization` 与 `test_authorized_archived_material_shows_owner_outbound_notice`。 |
| AC-12 错误区分 | PASS | Wiki Ingest 用例分别断言 `WIKI_EXTERNAL_CALL_DENIED`、`OUTBOUND_COVERAGE_EXCEEDED`、`REDACTION_REQUIRED`；249 项 2.3 聚焦套件通过。 |
| AC-13 审计 | PASS | `test_ingest_archived_l2_source_updates_complete_wiki` 断言 `authorized=1`、`redacted=1`、`result_mode=realtime`、真实 `outbound_chars`与重算 `outbound_coverage`；日志不保存正文。 |
| AC-14 严格路径兼容 | PASS（聚焦） | Task 3 原样关联回归 242 passed，包含 Gateway schema、Wiki Gateway、Wiki Ingest、Query 与 `ImportSource`；默认 strict 脱敏单测也通过。 |
| AC-15 Dify 兼容 | PASS | Dify 输入输出仍使用 2.2 Schema；本地管线版本提升为 2.3.1，仅用于幂等键和安全重建。候选文档与 Wiki Ingest 超时均为 300 秒。 |
| AC-16 完整回归 | PASS（含已记录顺序性债务） | 临时 `INCUBATOR_LIBRARY_ROOT` 下：排除既有 SQLite/WAL 顺序性用例时 1198 passed、1 deselected；该用例隔离运行 1 passed。聚合覆盖仓库现有 1199 项测试，但单次全量进程仍可能因历史顺序污染出现 1 failure。 |
| AC-17 版本归组 | PASS | `test_materials_manager_groups_versions_and_keeps_newest_outside_history` 构造3+1 版本，断言只有2个主组，最新 v3.0 在外，v2.0/v1.0 在历史 expander。 |
| AC-18 搜索与信息层级 | PASS（自动化） | 三组参数化用例分别验证关键字/状态/类型筛选；技术详情用例断言绝对路径、Source ID、SHA-256、Schema 的层级；行内失败与“查看技术错误码”用例通过。 |
| AC-19 可删状态 | PASS | `pending_ingest` / `ingest_failed` 两组删除用例断言 Source 目录进入回收区、manifest 保留元数据与 SHA、DB/index 不再显示；E2E 验证二次确认和下一版本提升。 |
| AC-20 不可删状态 | PASS | `ingesting` / `ingested` 两组服务端禁删断言 `MATERIAL_DELETE_NOT_ALLOWED`，并逐字节对比 DB/index/Raw/Wiki 不变；E2E 断言两者均无删除按钮。 |

## 命令记录

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/pytest -q tests/integration/use_cases/test_delete_archived_source.py tests/e2e/test_materials_page.py -k 'group or filter or detail or delete'` | TDD RED：收集阶段因 `DeleteArchivedSourceInput` 不存在而失败，16 deselected；失败原因与尚未实现功能一致。 |
| `.venv/bin/pytest -q tests/integration/use_cases/test_delete_archived_source.py` | 9 passed in 0.21s。 |
| `.venv/bin/pytest -q tests/e2e/test_materials_page.py` | 23 passed in 2.23s。 |
| `.venv/bin/pytest -q tests/integration/use_cases/test_delete_archived_source.py tests/integration/files/test_project_source_archive.py tests/e2e/test_materials_page.py` | 37 passed in 2.36s。 |
| 2.3 聚焦套件（brief Step 8 第一条） | 249 passed in 4.99s。 |
| Task 3 严格路径关联回归 | 242 passed in 3.51s。 |
| 六个获批隔离修复文件的聚焦回归 | 68 passed in 1.95s。 |
| `INCUBATOR_LIBRARY_ROOT=<fresh temp dir> .venv/bin/pytest -q` | PASS；1155 passed in 27.97s。 |
| brief 规定 Ruff check | PASS，`All checks passed!`。 |
| brief 规定 Ruff format check | PASS，`18 files already formatted`。 |
| 测试隔离修复 Ruff check / format check | PASS，`All checks passed!` / `7 files already formatted`。 |
| `git diff --check` | PASS，无输出。 |
| 2.3.1 最终差异聚焦套件 | 235 passed；独立复审者执行。 |
| 2.3.1 Wiki 安全/集成聚焦套件 | 234 passed in 4.03s。 |
| `INCUBATOR_LIBRARY_ROOT=<temp> .venv/bin/pytest -q -k 'not test_lld_dry_run_writes_nothing'` | 1198 passed, 1 deselected in 30.46s。 |
| `INCUBATOR_LIBRARY_ROOT=<temp> .venv/bin/pytest -q tests/integration/scripts/test_migrate_lld_to_v2.py::test_lld_dry_run_writes_nothing` | 1 passed in 0.15s。 |
| 2.3.1 修改文件 Ruff check / format check | PASS，`All checks passed!` / `14 files already formatted`。 |
| `python -m compileall -q src tests` | PASS。 |

## 删除事务与 Raw SHA 证据

- 顺序为：规范化并校验 `raw/<year>/<source_id>/` 及原始 SHA → 完整 Source 目录 `os.replace` 进回收区并原子写 manifest → `SourceIndexStore.remove` 原子替换活动索引 → 最后删除中央 DB 记录。
- move/index/DB 三种注入失败均断言 DB 记录、活动索引字节、Raw 字节恢复，回收区无残留事务目录。
- 可删状态中，回收文件 `read_bytes()` 等于删除前 Raw，重算 SHA-256 等于 `SourceRecord.sha256`。

## UI 证据说明

- 本轮最新页面行为证据是 23 个 Streamlit AppTest，其中 Task 5 覆盖系列归组、三种筛选、技术详情、行内错误、禁删、二次确认和版本提升。
- `docs/qa/ux-audit/2.3-archived-materials/01-current-archived-materials.png` 是 Task 5 开始前已存在的 **Owner 未跟踪证据**；本轮未修改、未暂存，也不将它声称为 Task 5 最终 UI 截图。
- 新鲜截图：[product-document-incubator-2.3-materials.png](product-document-incubator-2.3-materials.png)。使用隔离的 `PROJECT_A` 演示库在 1440×1024 本机无头 Chrome 中取得；显示“已归档材料”标签的关键字/状态/类型筛选、系列“路线图”的最新 v3.0、待 Ingest 删除入口，以及“用户调研”的已 Ingest 禁删提示。

## 交接说明

1. 控制器批准后，E2E fixture 以及 18 个受影响的 integration/security/unit 测试设置路径均显式使用各自 `tmp_path`/demo `root` 作为 `INCUBATOR_LIBRARY_ROOT`。六个受影响文件的聚焦回归为 68 passed，全量新鲜回归为 1155 passed；未触碰 Owner 外部默认项目库。
2. Task 5 将以精确暂存方式提交；不得纳入 Owner 的上传安全、上传 UI、旧 UX 图片或规格/计划文件。

## 2.3.1 节点 5 最终加固（2026-08-28）

### 已关闭风险

1. Wiki 覆盖特例只能由精确 `WikiIngestWorkflowInput` 使用，并将 Schema identity 纳入不可伪造的安全证明；普通调用路径继续执行 25% 上限。
2. Owner-confirmed 出站内容会在本地遮盖 Source 名称、版本、范围、locator、主题标题与索引投影中的硬标识；可信重建层独立重算，不信任调用方传入的安全结论。
3. DOCX 按正文顺序提取段落和表格，合并单元格按 XML 节点去重，嵌套表格递归保留。
4. Wiki 最多使用 20 个来源分段，并受 20,000 字符规范化载荷绝对上限约束；候选文档内容过薄时本地拒绝，避免生成大面积“待补充”。
5. Wiki 管线版本进入幂等键，规则升级后不会复用旧结果；Wiki Ingest 与候选文档 Gateway 超时均为 300 秒。

### 独立复审

- Critical：0。
- Important：0；先前发现的 Schema 绑定、出站元数据硬标识和 DOCX 合并/嵌套表格三项 Important 均已关闭。
- 结论：可进入 Owner 验收。

### 已知债务与边界

- `test_lld_dry_run_writes_nothing` 在部分 Streamlit E2E 之后会因既有 SQLite/WAL 生命周期导致主文件哈希波动；隔离运行稳定通过。这是测试隔离债务，不是本次差异引入的产品回归。
- 极少数结构性项目/Source ID 若自身长得像受保护的纯数字硬标识，当前策略会在外调前失败关闭，不会泄漏；后续可通过 ID 生成规范或旧数据迁移消除兼容风险。
- 全仓既有 Ruff/format 历史债务仍按此前 Owner 决策保留；本次修改文件的 Ruff、format、编译与 `git diff --check` 均通过。
