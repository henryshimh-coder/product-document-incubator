# T15 整改独立复核报告

> 复核日期：2026-08-10（America/Los_Angeles）  
> 复核对象：T15-R01～T15-R04 整改结果  
> 复核版本：`8aa5d45b8a894dd5b62779c481ce4148d795056c`  
> 版本标签：`v0.2.1-live-demo`  
> 复核方式：提交差异审查、专项/全量测试、干净副本三轮演练、用户授权的独立真实 Dify Lint 调用

## 1. 复核结论

**T15 的代码与核心实时演示能力已通过整改复核；正式冻结签署仍暂缓。**

原两个 Blocker 已关闭：

1. Ingest/Query/Lint 的 60/30/60 秒配置已经进入网关和 HTTP 运行时；独立真实 Lint 调用观测到 60 秒配置并成功返回。
2. Lint 页面已经具备完全匹配缓存接续、缓存 miss 禁用态、缓存溯源展示，并能在缓存接续后继续决定、审批和发布。

当前仅剩：

- **T15-R04-E01（Important，证据缺口）**：整改报告声称三服务冒烟和十次 Lint 均记录 run ID，但提交证据没有 Query/Lint 的远端 workflow run ID；Ingest 所列 `CALL-...` 是应用模型调用 ID，不是 Dify `workflow_run_id`。
- **T15-O01（现场运营前置项）**：备用演示视频仍由现场负责人补充，仓库与预检记录均没有实际文件、哈希和试播结果。

因此：

```text
T15-R01：通过
T15-R02：通过
T15-R03：通过
T15-R04：部分通过（流程已改，执行证据未完整归档）
T15 核心代码门禁：通过
v0.2.1-live-demo 正式冻结签署：暂缓
```

## 2. 整改提交边界

```text
00df0c4  v0.2.0-live-demo 原冻结版本
8052e3d  独立验收报告
5869748  timeout 注入、Lint 缓存接续、测试和证据结构
8aa5d45  整改后真实采样汇总与 v0.2.1-live-demo 标签
```

`v0.2.1-live-demo` 是 annotated tag，指向当前 HEAD `8aa5d45`；原 `v0.2.0-live-demo` 标签未移动。整改范围没有新增一期业务能力、页面或 Lint 类型，锁文件未变化。

## 3. T15-R01：超时配置复核

### 3.1 代码路径

复核确认：

- `WorkflowTimeouts` 使用严格整数，拒绝缺失字段、额外字段、字符串/浮点、零、负值和超过 600 秒。
- `_load_settings()` 强制读取顶层 `timeouts`，不再静默忽略。
- `build_container()` 将配置传入 `build_workflow_gateways()`。
- 三个网关构造时分别保存 Ingest 60、Query 30、Lint 60 秒。
- 用例未传覆盖值时，网关把已注入值传给 `DifyClient`，HTTP 请求再使用该值。

专项回归覆盖：配置读取、非法配置 fail closed、组合根分流、35～45 秒模拟响应、Query 30 秒超时边界。

### 3.2 独立真实 Dify 复跑

在干净副本检出 `v0.2.1-live-demo`，恢复 frozen 快照，先以完全匹配 Ingest 缓存导入固定风险材料，再对该 source 执行一次实时 Lint。API Key 未输出、未入库。

```json
{
  "result": "INDEPENDENT_LINT_OK",
  "git_sha": "8aa5d45b8a894dd5b62779c481ce4148d795056c",
  "result_mode": "realtime",
  "baseline_version": "LLD-724_1",
  "issue_count": 1,
  "http_status": 200,
  "elapsed_s": 26.754,
  "configured_timeout_s": 60,
  "workflow_run_id": "63ad32c9-033e-4929-a18d-610cf6ba5baf"
}
```

该结果直接复现了原故障路径，并证明整改后的 Lint 不再使用隐藏的 30 秒默认值。临时副本随后恢复 initial 快照并再次通过 `VALIDATION_OK baseline=LLD-724_1`。

第一次诊断尝试直接在 frozen 状态运行 `all_current_sources`，在发网前被 `OUTBOUND_COVERAGE_EXCEEDED` 拦截，因此没有消耗外部调用。根因是 frozen 快照冻结缓存但不预置已导入风险资料；按仓库正式离线流程先消费精确 Ingest 缓存后，`current_plus_source` 实时 Lint 成功。这不是 Dify 或 timeout 整改失败。

## 4. T15-R02：Lint 精确缓存接续复核

复核确认：

- 页面可显式选择「实时自检／冻结缓存」。
- 实时 `MODEL_TIMEOUT` 后使用同一 `RunLintInput`，只把 `preferred_mode` 改为 `cache`，因此缓存身份仍由本次材料哈希、baseline、prompt、model、schema 重建。
- 缓存命中后显示「冻结缓存」、生成时间和 baseline 版本。
- 缓存 miss 显示「未找到同材料、同版本的可用缓存」，不提供近似缓存。
- 非超时错误不会伪装为缓存接续成功。
- `test_lint_timeout_fallback.py` 覆盖缓存接续后决定、审批、发布到 `LLD-724_2`。

没有发现新的 Critical 或功能 Blocker。

## 5. T15-R03：性能证据复核

`docs/qa/evidence/t15-performance-samples.json` 已随版本提交：

- original 轮包含 7 类操作各 10 条，共 70 条；
- remediation 轮包含整改后 Lint 10 条；
- remediation SHA 为 `586974827cff6bc6b758b82a58e0979dfcc5ea06`；
- Lint n=10、P50 24.915s、P95 26.855s、max 26.855s、失败 0；
- 确定性测试从逐条样本重新计算 n/min/P50/P95/max/失败数；
- 样本不含 Key、请求正文或未脱敏材料。

T15-R03 的性能数值和可重算性通过。

## 6. 自动门禁证据

### 6.1 整改专项

```text
pytest:
  tests/unit/test_config.py
  tests/integration/gateways/test_workflow_timeouts.py
  tests/e2e/test_lint_page.py
  tests/e2e/test_lint_timeout_fallback.py
  tests/unit/test_t15_performance_evidence.py

→ 37 passed
```

### 6.2 全量与质量

```text
coverage run -m pytest
→ 778 passed in 25.70s

coverage report --include='src/domain/*,src/application/*'
→ TOTAL 2597 statements, 119 missed, 95%

ruff check
→ All checks passed!

ruff format --check
→ 171 files already formatted

compileall
→ passed

git diff --check
→ passed
```

### 6.3 干净副本连续三轮

```text
第 1 轮：RESET_OK → VALIDATION_OK → 5 passed
第 2 轮：RESET_OK → VALIDATION_OK → 5 passed
第 3 轮：RESET_OK → VALIDATION_OK → 5 passed
```

复验完成后临时副本工作区 clean；主工作区在新增本报告前为 clean。

## 7. 剩余问题与最小补证

### T15-R04-E01（Important）：真实调用 run ID 没有按报告口径归档

当前报告存在三处不一致：

1. `docs/qa/test-report-2026-08-30.md` 说三 Workflow 冒烟记录成功/错误码/耗时/run ID，但只列 Ingest 的 `CALL-...`，没有 Query 的任何 ID。
2. `CALL-39B...` 是应用模型调用 ID；整改标准要求的是 Dify 返回的 `workflow_run_id`。
3. 报告说十次 Lint “逐次记录 run ID”，但提交的十条 remediation 样本只有 operation、iteration、started_at、seconds、ok、error，没有 run ID；仓库其他文件也未找到这十个 ID。

最小整改要求：

- 新增独立脱敏证据文件，例如 `docs/qa/evidence/t15-live-smoke-2026-08-10.json`；不要破坏现有 performance evidence schema。
- 至少记录：`code_sha`、时间、Workflow、耗时、成功/公开错误码、Dify `workflow_run_id`。
- 补齐整改后 Ingest、Query、Lint 三个 Workflow 的实际远端 ID。
- 若报告继续保留“Lint 十次逐次记录 run ID”，必须归档十个 ID；否则删除该不实描述，并说明十次采样仅固化了时间与耗时。
- 可把本报告中的独立 Lint ID作为当前 HEAD 的 reviewer 证据，但它不能替代缺失的 Ingest/Query 整改后 ID。
- 把 `CALL-39B...` 明确标为“应用模型调用 ID”，不得继续写成 Dify run ID。

通过标准：

- [ ] 三个 Workflow 均有当前整改版本的远端 `workflow_run_id`、耗时和结果。
- [ ] 证据文件不含 Key、请求正文或未脱敏材料。
- [ ] 文档中的 ID 类型、数量与证据文件一致。
- [ ] `git diff --check`、证据格式测试和全量测试保持通过。

### T15-O01（现场运营前置项）：备用视频尚未补证

预检清单已正确说明“仓库不含视频工件，由现场负责人补证”，但当前还没有：

- 演示设备上的文件名或受控路径；
- 文件 SHA-256；
- 最近一次完整试播时间与负责人；
- 离线、音画、分辨率检查结果。

这是现场放行条件，不要求改业务代码。上述记录完成前，不能把预检第 8 项勾为通过。

## 8. 最终签署

```text
T15 代码整改：通过
T15 本地质量门禁：通过
T15 独立真实 Lint：通过
T15 性能目标：通过
T15-R04 可审计证据：未完全通过
备用视频现场预检：待补证
v0.2.1-live-demo 正式冻结签署：暂缓
```

本轮不要求继续修改业务代码。补齐 T15-R04-E01 和 T15-O01 后，只需执行证据一致性、预检和工作区复核；若没有新的代码变化，可不重复十次 Lint 性能采样。
