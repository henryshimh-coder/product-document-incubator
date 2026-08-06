# Task 11 实施报告（整改轮）

## 实现摘要

- **Relation 成为追溯唯一事实来源**：`src/application/ports/repositories.py` 新增 `RelationRepository` 端口；`SqliteRelationRepository.load_connected()` 以 SQLite recursive CTE 实现项目强隔离、深度上限 6（clamp 0–6）、UNION 去环、`ORDER BY created_at, id` 稳定序。模块级 `_insert_relation_guarded()`：同 ID 同事实幂等跳过，同 ID 异事实以 `RELATION_CONFLICT:{id}` fail closed。
- **生命周期 Relation 事务性写入**：Ingest 为每张卡写 `derived_from`（与 Source/Card/Issue 同事务）；RunLint 对有 `target_rule_id` 的去重 issue 写 `conflicts_with`（新 `SqliteLintUnitOfWork.apply(issues, relations)` 单 `BEGIN IMMEDIATE` 事务，失败映射 `LINT_PERSISTENCE_FAILED`）；RecordDecision 写 `resolved_by` + `proposes_change_to`（`SqliteDecisionUnitOfWork.record` 增加 `relations` 参数，与决定、Issue 状态、ChangeRequest 同事务）；PublishBaseline 写 `approved_as` + `supersedes`（T10 已有，保留）。
- **BuildTrace 全重写**：只从 Relation 图选六节点主链（`derived_from → conflicts_with → resolved_by → proposes_change_to → approved_as`），候选按 `(created_at, id)` 升序、最深下游优先；缺边写入 `missing_links`，禁止按实体字段或"最新记录"补边。入口卡片改走 `BaselineCardReader`（Manifest 指向的当前快照，不再依赖可能滞后的 SQLite 筛选）。
- **可验证"回到原文"**：Source 节点 `_verify_source_excerpt()`——`material_reader.read_source` 验归档路径+SHA-256+大小 → 按卡 citation 定位 fragment → 五词典脱敏 → 120 字截断 → `{locator}｜{text}`；篡改或定位失败 `verification="unverifiable"` 且 `excerpt=None`，不回退其他文本。UI 显示"引用不可验证"红徽标与"原文片段（已脱敏）"caption，不展示完整 L2 原文或绝对路径。
- **发布页→追溯页定位**：发布成功 flash 携带 `target_card_id`，"查看完整追溯"按钮写入 `st.session_state["trace_target_card_id"]` 再跳转；追溯页入口 selectbox `pop` 该键并预选中。
- **市场证据收紧**：`VerifiedMarketEvidence` 须同时满足同项目、非沙箱、`source_type ∈ {customer_market_material, market_research_report}`、归档哈希通过、citation 可定位；任意字符串、普通卡片 ID、定位不到的片段一律不算证据。验证计划只认同卡 `MKT-001` issue 的结构化 `validation_note`。
- **轻量成本联动**：`list_cost_sources` 只留 `is_sandbox and source_type ∈ {cost_parameter, demo_cost_parameter}`；`calculate_cost_impact` 服务端守卫（空 refs/不存在/跨项目/非沙箱/非成本类型一律 `COST_SOURCE_REQUIRED` + `COST_SOURCE_INVALID:{ref}`）；`is_simulation` 由 SourceRecord 推导，`CostImpactInput` 无该字段，前端无法伪装正式；Decimal 到分、固定免责声明、无损益结论。UI 删除独立沙箱复选框。
- **审计**：`list_model_calls` 近期优先 + limit；`value_metrics` 只展示本地实测指标（系统查询耗时、有效冲突、误报、变更单形成耗时），全部标注实测来源。
- **新错误码**：`LINT_PERSISTENCE_FAILED`、`RELATION_CONFLICT`（均入 ERROR_CATALOG）。
- **合并纪律**：stash 恢复后仅 `db/repositories.py` 一处冲突（T10 `_validate_mirror_payload` 与 T11 `SqliteModelCallLogRepository` 两侧保留）；合并后全量 604 无 T10 回退。

## 整改中发现的两个真实缺陷（已修复）

1. **bootstrap 基线无法通过 T10-3 正式来源闸**：`RULE-LLD-001.source_refs=["SRC-LLD-BASE"]` 但不存在该 SourceRecord，真实流程发布必以 `PUBLISH_SOURCE_MISSING` fail closed。
2. **基线卡永远没有 derived_from**：`derived_from` 唯一写入点是 Ingest（只指向新写的候选卡），入口卡又只读基线快照，任何真实流程都无法为快照卡产生来源边，六节点链不可能成立；且 bootstrap 项目 `allow_external_model=False` 使真实 ingest/lint 一律 `EXTERNAL_CALL_DENIED`。

修复 `scripts/bootstrap_demo.py`：归档真实基座材料 `SRC-LLD-BASE`（当前产品方案.md，含背景章节使出站安全证明 25% 覆盖率可达）；卡片引用改为真实 chunk（`SRC-LLD-BASE:{chunk_id}`）；幂等写入两条 `derived_from` 关系；项目允许外部模型（逐来源授权与脱敏检查仍分别把关）；基线增加一张 `market_judgment` 演示卡（引用基座材料→永远不满足市场证据类型，用于不足提示路径）。`test_get_dashboard` 来源计数期望 2→3（含基座材料，OTHER 项目仍隔离）。

## T11-A01～A24 验收映射

| 用例 | 场景 | 覆盖证据 |
|---|---|---|
| A01 | 真实 Ingest→Lint→Decision→Publish 六节点五边来自 relations | 联合验收 14 步全 PASS（evidence/t10-t11/joint-acceptance.log 步骤 02/04/05/07/10）；`test_trace_contains_source_issue_decision_change_and_release`；e2e `test_trace_page_shows_six_node_chain_with_relations` |
| A02 | 删除 Decision→Change Relation 保留外键 | `test_deleted_relation_leaves_explicit_gap_without_auto_repair`（缺三环入 missing_links，不补边） |
| A03 | 更新但不相连的 Issue/Decision | `test_trace_ignores_newer_unconnected_issue_and_decision`（本轮新增：较晚 created_at 的不相连记录不进主链） |
| A04 | 关系循环 | `test_load_connected_terminates_on_cycles_without_duplicates`、`test_load_connected_depth_defaults_to_six_and_caps` |
| A05 | 跨项目同名实体/关系 | `test_load_connected_isolates_other_projects`、`test_trace_ignores_relations_from_other_projects` |
| A06 | 重复执行决定/发布恢复/启动对账 | `test_decision_uow_idempotent_replay_does_not_duplicate_relations`、`test_lint_uow_repeat_apply_is_idempotent`；联合验收步骤 05b/14（relations 恒为 7→9，重放与重启零增长） |
| A07 | 合法来源引用展示 | `test_source_node_excerpt_is_located_and_redacted`；联合验收步骤 11（`heading:当前产品方案 > 目标客群; line:7｜仅作为脱敏演示基线使用。`） |
| A08 | archive 篡改 | `test_source_node_unverifiable_when_archive_tampered`（不可验证、不展示篡改内容） |
| A09 | chunk ID 不存在 | `test_source_node_unverifiable_when_citation_fragment_missing`（不回退其他片段） |
| A10 | L2 只展示脱敏片段 | 同 A07 测试（手机号脱敏为 `[已脱敏:phone]`、120 字截断、只显示 locator 不显示绝对路径） |
| A11 | 发布页点击查看完整追溯 | e2e `test_publish_success_trace_jump_hands_off_target_card` + `test_trace_page_preselects_card_passed_from_release`（本轮新增） |
| A12 | 任意字符串 source_ref | `test_arbitrary_or_unlocatable_refs_never_count_as_market_evidence`（本轮新增） |
| A13 | 引用普通产品规则卡 | 同上（`RULE-001` ref）+ `test_formal_document_refs_never_count_as_market_evidence` |
| A14 | 沙箱调研材料 | `test_sandbox_market_material_never_counts_as_evidence` |
| A15 | 合法正式市场材料可验证 | `test_verified_market_material_counts_as_evidence`（evidence_supported） |
| A16 | 非 MKT-001 validation_note | `test_validation_plan_from_other_rule_is_ignored` |
| A17 | MKT-001 明确验证计划 | `test_validation_plan_only_comes_from_mkt_rule_of_same_card`（validation_planned + 计划原文） |
| A18 | 不选来源 | `test_cost_impact_requires_refs` + e2e `test_cost_form_requires_source_refs`（COST_SOURCE_REQUIRED） |
| A19 | 选择普通 RULE-001 | `test_cost_impact_rejects_formal_or_unknown_sources`、`test_list_cost_sources_only_accepts_sandbox_cost_parameter_materials`；联合验收步骤 13（正式基座材料被 `COST_SOURCE_REQUIRED` 阻断） |
| A20 | 沙箱成本参数来源 | `test_cost_impact_auto_marks_simulation_from_source_records` + e2e 断言"（参数来自模拟数据）"；联合验收步骤 13（is_simulation=True 不可取消） |
| A21 | 沙箱伪装正式 | `CostImpactInput` 无 is_simulation 字段（结构性保证），服务端恒从 SourceRecord 推导（同 A20 测试） |
| A22 | 正式来源无结构化参数 | `test_cost_impact_rejects_formal_or_unknown_sources`（正式来源直接阻断，正式参数模式留待结构化记录） |
| A23 | Decimal 边界与金额量化 | `test_cost_impact_quantizes_to_fen`、`test_cost_impact_uses_decimal_and_fixed_disclaimer`；联合验收步骤 13（5000.00/6000.00/1000.00） |
| A24 | 固定免责声明无损益结论 | `test_cost_impact_uses_decimal_and_fixed_disclaimer` + e2e `test_cost_form_computes_decimal_result_with_disclaimer` |

## 完整验证结果（2026-08-04，当前有效）

- 全套：`.venv/bin/python -m pytest -q` → `647 passed`（T10 收口 569 + T11 净增 78）。
- 覆盖率：domain+application `95%`（TOTAL 2420 行缺 116），门槛 90% 通过。
- 静态检查：`ruff check src tests scripts` 全过；`ruff format --check` 142 files already formatted。
- 联合验收：全新临时根目录（/tmp/t10t11_joint）真实 14 步全 PASS，证据 `evidence/t10-t11/joint-acceptance.log`（脚本同目录可复跑）；仓库 `data/` 零污染（曾发现 CWD 相对归档根导致的污染并修复脚本）。

## 浏览器证据状态

WebBridge 守护进程在线但扩展未连接（`no extension connected`，与 T10 同一用户侧环境阻塞），详见 `evidence/t10-t11/browser-acceptance-blocked.md`。1440x1024 六节点可读性与 390x844 堆叠溢出检查待扩展恢复后补截图；期间由 AppTest e2e 真实渲染管线覆盖（647 passed 内）。未伪造任何浏览器证据。

## 提交

- 分支 `codex/t11-remediation`，提交 `c56678c` — `feat: add persisted traceability and governed value hints`，已 fast-forward 合入 `feat/lightweight-t01`（b1193a3..c56678c），合入后主线全量 647 passed、工作区干净。

## T12 准入复核（v2 文档第 6 节逐项）

| 门槛 | 结论 | 证据 |
|---|---|---|
| T10 整改已提交且工作区干净 | 满足 | b1193a3，合入后 worktree 0 改动 |
| T11 从 T10 完成提交继续并独立提交 | 满足 | c56678c 直接基于 b1193a3，独立提交后 fast-forward |
| T10-A01～A19 全部通过 | 满足 | T10 整改轮 569 passed + T10 报告映射（task-10-implementer-report.md） |
| T11-A01～A24 全部通过 | 满足 | 本报告映射表 + 647 passed + 联合验收 14 步 |
| 全量测试/覆盖率/静态检查 | 满足 | 647 passed、95%、ruff check/format 全过 |
| 当前/历史查询真实发布后联合测试 | 满足 | 联合验收步骤 08/09（LLD-724_2 新规则 / LLD-724_1 旧规则） |
| 正式/沙箱边界不可绕过 | 满足 | 服务端守卫 + A13/A14/A19-A22 测试 + 联合验收步骤 13 正式来源阻断 |
| 六节点链完全来自持久化 Relation | 满足 | 联合验收步骤 10（五边全部来自 relations 表，missing=[]） |
| 原文引用可验证且不泄露敏感内容 | 满足 | 联合验收步骤 11 + A07-A10（脱敏片段、无绝对路径） |
| 轻量成本仅沙箱参数且无损益 | 满足 | 联合验收步骤 13 + A18-A24（模拟标记不可取消、到分、固定免责声明） |
| 桌面和移动真实浏览器证据齐全 | **不满足（环境阻塞）** | WebBridge 扩展未连接，evidence/t10-t11/browser-acceptance-blocked.md |
| 实施报告/进度台账/独立 review 完整 | 部分满足 | T10/T11 报告与 progress.md 齐；**独立 reviewer 复核尚未执行** |

**结论：T12 暂不准入。** 两项未满足——真实浏览器证据（用户侧环境阻塞，扩展恢复后补 1440x1024 与 390x844 截图）与独立 reviewer 复核。按 v2 约定，在此之前 T12 只能阅读和设计脚本，不能生成正式演示快照或冻结缓存。两项补齐后本表可直接转为准入通过。

## v3 整改轮（2026-08-05，分支 codex/v3-remediation，提交 b3845d5）

依据 `docs/superpowers/handoffs/2026-08-04-t10-t11-acceptance-report-and-remediation-v3.md` 执行，范围 M3 + M4 中 T11 侧：

- **M3 裸引用 fail-closed**：`TraceNode` 新增 `unverifiable_reason: Literal["no_citation","integrity_failed"] | None`；裸引用（无 citation）节点标记 unverifiable/no_citation；UI 三态徽标——未提供可定位引用（琥珀）/ 引用不可验证（红）/ verified 展示脱敏片段。
- **V3 新增测试**：A13/A15（test_build_trace.py）、A16（test_trace_page.py）。
- **M4 真实浏览器验收（原"环境阻塞"已解除）**：六节点链双视口实测——移动 390×844 每卡 350px 宽纵向堆叠不越界、桌面 1440×1024 六节点横向首屏全见（bottom≤515）；顺序 原始资料→结构化知识→问题→人工决定→变更单→生效基线（发布前 5 节点 +「缺失环节：生效基线」为预期）；控制台 error/warn 均为 0、无 stException。证据 `evidence/t10-t11/browser/`（含 trace-six-node-mobile-390x844.png / trace-six-node-desktop-1440x1024.png）。
- **验证**：全量 659 passed，覆盖率 95%，静态检查全过。

## T12 准入复核更新（v3 轮，2026-08-05）

v2 第 6 节 12 项门槛中此前 2 项未满足，本轮状态：

| 门槛 | v3 轮结论 | 证据 |
|---|---|---|
| 桌面和移动真实浏览器证据齐全 | **已满足** | evidence/t10-t11/browser/ 5 图 + browser-acceptance.md（弹窗边界、双视口六节点、篡改失败回退重试、控制台干净） |
| 独立 reviewer 复核 | **仍待外部执行** | 本轮为实施与自验收；独立 review 需由评审代理/人工执行，无法由实施方自我声明 |

其余 10 项维持满足（T10/T11 已提交、A 系列全绿、联合 14 步、服务端守卫、relation-only 六节点、脱敏可验证引用、沙箱成本无损益、报告台账齐）。浏览器证据阻塞解除后，T12 仅剩独立 reviewer 复核一项外部门槛。

## v4 整改轮（2026-08-05，分支 codex/v4-remediation，提交 2b02b56）

- T11 功能本轮无新阻断（独立复核原话）；run_lint 比较侧基线 citation 改由共享规则 `baseline_citations.build_baseline_citations` 生成，与发布侧同源，行为不变。
- **V4-P1-02 关闭（交付侧）**：`.superpowers/sdd/.gitignore` 设最小白名单（默认忽略保留），本报告、T10 报告、progress.md、joint_acceptance.py、joint-acceptance.log、browser-acceptance-blocked.md 与 browser/ 7 件证据全部进入版本控制，逐项 `git check-ignore` 验证；`browser-acceptance-blocked.md` 追加 2026-08-05 阻塞解除记录（历史保留）。
- **T12 准入复核更新**：12 项门槛中「独立 reviewer 复核」已执行并完成一轮整改闭环（本轮即复核产物）；「桌面和移动真实浏览器证据」以最终 SHA 2b02b56 换证后维持满足。最终准入结论以待复核方确认无遗留 Critical/Important 为准。
