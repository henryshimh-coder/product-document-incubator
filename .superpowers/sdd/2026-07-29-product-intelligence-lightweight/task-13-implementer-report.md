# Task 13 实施报告（黄金测试、E2E、安全测试和设计验收）

> 本报告为 T13 完成时（2026-08-09）的最终事实版。T13 准入门禁（T12 评审签署项）由用户 2026-08-07 直接指令「开始完成T13」放行，此处如实记录。计划中的 `uv run` 一律以 `.venv/bin/python` 等价执行。本轮 E2E 与浏览器验收的外部模型侧为本地 mock 网关（与联合验收同构 fixtures），未连接真实 Dify。

## 实现摘要

- **E2E Harness（Step 1）**：`tests/e2e/harness.py` 提供 `DemoHarness`（import_source/query/run_lint/record_accept_change/approve_change/publish，与计划 Step 1 接口一致）与 mock 网关结果工厂（`_ingest_result/_query_result/_lint_result`）；`tests/e2e/conftest.py` 每次测试从 initial 快照创建独立临时数据目录并构建 `AppContainer`，测试后丢弃。
- **四个计划点名的 E2E（Step 2–5）**：`test_full_success.py`（导入→查询→自检→会议决定→变更单→审批→发布→Manifest 落账全链路）、`test_realtime_timeout_fallback.py`（实时超时后精确缓存继续）、`test_release_failure.py`（注入发布写失败，Manifest 逐字节不变）、`test_security_block.py`（L3 资料绝不发起模型调用，LOCAL_ONLY + `count_started_for_source==0`）。
- **安全测试**：`tests/security/test_prompt_injection.py`（注入文本只落 `inputs.source_chunks[...]`，顶层键全集越权字段为零）与 `tests/security/test_log_redaction.py`（证件号/手机号出站前掩码为 `[已脱敏:id_card]`/`[已脱敏:phone]`，日志与安全证明复核无残留）。security+e2e 合计 76 项全过。
- **黄金指标（Step 7）**：`tests/golden/test_query_golden.py`（10 问）、`tests/golden/test_lint_golden.py`（8 例）纳入全量回归；同口径逐例复算：query 事实/区间/规则/引用/范围隔离五维 10/10；lint tp=7 fn=0 fp=0 recall=1.0000、severity 一致率 1.0000、重大问题 2 例双引用 100%。全部达到或超过计划门槛（query ≥90%、隔离=100%、引用=100%、lint recall ≥80%、双引用=100%）。
- **全量自动测试（Step 6）**：725 passed / 0 failed；domain+application 合并覆盖率 95.40%（2587 语句，119 未覆盖；门槛 85）；全 src 参考口径 93%（5800 语句，426 未覆盖）。
- **逐页 UI 验收（Step 8）**：真实 Chrome（WebBridge 扩展驱动）1440×1024（dpr=1，CDP override 实测）逐页操作验收，10 条全部通过，证据 8 张截图入 `docs/qa/ui-1440x1024/`，逐条记录见 `docs/qa/ui-acceptance-1440x1024.md`。DOM 实测：每页主操作 ≤1（首页/导入/查询/自检各 1，发布/追溯只读态 0，发布流程态 1）、嵌套卡片 0、横向滚动 0。
- **三次连续全流程（Step 9）**：`reset_demo.py --snapshot initial` + `test_full_success.py` 连续三轮全部 `RESET_OK → VALIDATION_OK → 1 passed`；重置再生目录 `data/obsidian_vault/`、`data/source_archive/` 已加入 `.gitignore`（可从已跟踪快照再生），复跑后工作区无残留。

## 验收期间发现并修复的缺陷（4 项，均含回归测试）

1. **导入页适用版本硬编码**（`src/ui/pages/ingest.py`）：默认值写死 `LLD-724_1`，改为 `_default_baseline_version(container)` 从 dashboard 视图读当前基线版本（catch KeyError/OSError/ValueError 回退空值）。
2. **自检/查询覆盖率错误消息笼统**（`run_lint.py`、`run_query.py`）：小材料触发覆盖率预算时被误报为 REDACTION_REQUIRED「资料尚未完成脱敏确认」。补齐与 import_source 同约定的预检（`_canonical_input_chars`，`json.dumps(ensure_ascii=False, sort_keys=True, separators=(",",":"))`），越界抛准确 `DomainError(ErrorCode.OUTBOUND_COVERAGE_EXCEEDED)`。根因链：发布后 notices 被消费 → lint comparison_items=0 → 材料只剩基线（1968 字符）→ 742/1968=37.7% > 25% 预算 → 按设计 fail-closed，但错误码必须准确。
3. **旧 UI 测试适配**（`tests/e2e/test_ingest_flow.py`）：导入页默认值变化导致 9 个用例失败，补显式 `ingest_baseline` 设置。
4. **追溯主链断链**（`import_source.py`、`repositories.py`、`build_trace.py`）：ingest 来源冲突问题未持久化 `target_rule_id` 与「规则卡→问题」`conflicts_with` 关系（仅 run_lint 路径持久化），浏览器实测发布 `LLD-724_3` 后追溯主链仍终止于已替代的 `LLD-724_2`；且 `_walk_chain` 同级等长多链取最早创建者，与"主链反映最新演化"语义相悖（演示数据中创建更早的问题反而通往更新基线）。修复三点：`_to_domain` 冲突问题持久化 `target_rule_id=item.target_card_id` 并补 `REL-{target}-CONFLICTS-WITH-{issue}` 回连关系（与 run_lint 同约定）；`IngestUnitOfWork.complete` 的 issue INSERT 补 `target_rule_id` 列（原语句丢列是数据落库仍为 NULL 的根因）；`_walk_chain` 决胜改为 `_chain_rank`（链长 + 链尾节点发生时间），与问题创建顺序无关。回归测试：`test_trace_prefers_latest_complete_chain_on_ties`（新链问题/决定/变更单创建更早、基线更新，与真实演示数据同构）、`test_import_source` 主成功用例新增回连关系与 `target_rule_id` 落库断言（关系计数 2→3，重复导入用例同步）。修复后浏览器重验：主链六节点正确终止于 `LLD-724_3`（生效中），见 06-trace.png。

## 如实记录的边界与观察项

1. 外部模型侧为本地 mock 网关，非真实 Dify；真实联通性留待 T14 runbook 演练。
2. 发布后小基线状态下全量自检被覆盖率预算按设计阻断（治理正确行为，非缺陷）。
3. 首页 eyebrow「当前项目」被 Streamlit 框架 toolbar 裁切上半部（框架层叠加）；右上角 Deploy 为框架原生 chrome。
4. 查询页「冻结缓存」标识未在浏览器会话实演；缓存态语义由超时回退 E2E 与本地缓存条目佐证。
5. 首页（01）与追溯页（06）截图反映 `LLD-724_3` 生效后最终状态；02–05c 拍摄于 `724_2→724_3` 流程执行期间，版本演进已在验收文档中注明。

## 交付物清单

- 测试：`tests/e2e/{harness,conftest,test_full_success,test_realtime_timeout_fallback,test_release_failure,test_security_block}.py`、`tests/security/{conftest,test_log_redaction,test_prompt_injection}.py`、`tests/integration/use_cases/test_build_trace.py`（+1 回归）、`tests/integration/use_cases/test_import_source.py`（断言增强）、`tests/e2e/test_ingest_flow.py`（适配）
- 缺陷修复：`src/ui/pages/ingest.py`、`src/application/use_cases/{run_lint,run_query,import_source,build_trace}.py`、`src/infrastructure/db/repositories.py`
- 文档：`docs/qa/ui-acceptance-1440x1024.md`、`docs/qa/test-report-2026-08-24.md`、`docs/qa/ui-1440x1024/*.png`（8 张）
- 配置：`.gitignore`（再生目录）、`.superpowers/sdd/.gitignore`（T13 报告白名单）
- 提交与合入记录：见 progress.md 台账（codex/t13 → feat/lightweight-t01 fast-forward）
