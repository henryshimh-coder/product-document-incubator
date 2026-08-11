# T15 独立验收与整改标准报告

> 复核日期：2026-08-10（America/Los_Angeles）  
> 复核对象：T15「8 月 25—30 日正式实时演示加固」  
> 复核版本：`00df0c4a08f49a575fb5d0ef4d57c15f02a784d6`  
> 版本标签：`v0.2.0-live-demo`  
> 复核方式：代码独立审查、干净副本验证、全量自动测试、用户授权的真实 Dify 调用

## 1. 验收结论

**T15 当前不通过，`v0.2.0-live-demo` 暂不具备正式现场演示冻结版的放行条件。**

本地功能、快照恢复、静态质量和全量回归均通过；但真实 Dify 独立复跑在 Lint 环节发生 `MODEL_TIMEOUT: DIFY_TIMEOUT`。根因已经定位为：`config/app.yaml` 声明的 Ingest/Lint 60 秒超时没有进入应用设置和运行时调用，实际仍使用网关默认 30 秒。与此同时，Lint 页面在实时超时后只显示错误，不能从页面切换到完全匹配的冻结缓存，因而现场完整流程会在「一键自检」环节中断。

这两项都直接违反 T15 的核心验收目标：现场优先实时运行、实时异常时用完全匹配缓存继续、后续人工决定和本地发布仍可真实执行。

| 验收域 | 结果 | 说明 |
| --- | --- | --- |
| T15 文件与版本边界 | 通过 | T15 提交和 `v0.2.0-live-demo` 标签存在，提交边界清晰 |
| 7 项操作各 10 次性能采样 | 有条件通过 | 本机临时原始记录确有 7×10 条且与报告汇总一致，但未纳入版本库；新的独立 Lint 调用发生超时 |
| 实时失败自动提示 | 部分通过 | Query 与 Ingest 有精确缓存路径；Lint 页面没有缓存接续路径 |
| 5—8 分钟演示脚本 | 文档通过、运行口径不通过 | 7 分钟主线齐全，但部分异常预案与当前能力不一致 |
| 10 项现场预检 | 文档通过、有效性部分通过 | 清单项目齐全，但三个 Workflow 的 Key/调用可用性没有逐一验证 |
| 快照、全量测试与三轮主流程 | 通过 | 干净副本连续三轮均无本地阻断；758 项测试通过 |
| 正式实时演示放行 | **不通过** | 真实 Lint 超时会中断主线，且无 UI 缓存接续 |

## 2. 已通过的独立证据

### 2.1 版本与工作区

```text
HEAD: 00df0c4a08f49a575fb5d0ef4d57c15f02a784d6
tag:  v0.2.0-live-demo
branch: feat/lightweight-t01
review 开始时工作区：clean
```

T15 提交新增了 Query 超时后的精确缓存探测、回退状态组件、演示脚本、预检清单和测试报告；没有修改锁文件、Schema 或一期业务边界。

### 2.2 全量测试与质量门禁

```text
.venv/bin/coverage run -m pytest
→ 758 passed in 25.18s

.venv/bin/coverage report --include='src/domain/*,src/application/*'
→ TOTAL 2594 statements, 119 miss, 95% coverage

.venv/bin/ruff check src scripts tests streamlit_app.py
→ All checks passed!

.venv/bin/ruff format --check src scripts tests streamlit_app.py
→ 168 files already formatted

.venv/bin/python -m compileall -q src scripts tests streamlit_app.py
→ passed

git diff --check
→ passed
```

### 2.3 干净副本恢复与连续三轮主流程

在新建干净副本、检出 `v0.2.0-live-demo` 后执行：

```text
初始 reset + validate
→ RESET_OK snapshot=initial
→ VALIDATION_OK baseline=LLD-724_1

第 1 轮 reset + validate + test_full_success
→ RESET_OK → VALIDATION_OK → 5 passed

第 2 轮 reset + validate + test_full_success
→ RESET_OK → VALIDATION_OK → 5 passed

第 3 轮 reset + validate + test_full_success
→ RESET_OK → VALIDATION_OK → 5 passed

复验后干净副本工作区：clean
```

这证明本地决定、审批、发布、追溯和快照恢复链路本身稳定，但该 E2E 使用本地测试网关，不能替代真实 Dify 时延验证。

### 2.4 T15 性能原始记录核对

本机 `/private/tmp/t15_perf_{op}.json` 中实际存在以下 8 份文件：7 项操作各 10 条记录及 1 份汇总；每条记录包含序号、时间、成功状态、秒数和错误字段。独立重算结果与 `docs/qa/test-report-2026-08-30.md` 一致：

| 操作 | n | P50 | P95 | 原采样失败数 |
| --- | ---: | ---: | ---: | ---: |
| 首页读取 | 10 | 0.002s | 0.003s | 0 |
| Ingest 实时 | 10 | 16.861s | 19.861s | 0 |
| Query 实时 | 10 | 12.858s | 14.234s | 0 |
| Lint 实时 | 10 | 24.286s | 27.737s | 0 |
| 缓存读取 | 10 | 0.001s | 0.013s | 0 |
| 发布 | 10 | 0.009s | 0.012s | 0 |
| 重置 | 10 | 0.311s | 0.316s | 0 |

这些数据可以证明原采样当时成功，但文件只位于临时目录，没有随 `v0.2.0-live-demo` 冻结，不能作为可移交、可长期复核的发布证据。

## 3. 用户授权的真实 Dify 独立复跑

### 3.1 授权与安全边界

本轮在用户明确授权后，使用当前 `.env` 的三个互异 Workflow Key，向现有 Dify 环境发送仓库内的模拟演示材料及固定查询/Lint 输入。API Key 未输出、未写入报告；复跑在干净副本进行，失败后已恢复 initial 快照并再次得到 `VALIDATION_OK baseline=LLD-724_1`。

### 3.2 运行结果

```text
容器：Ingest / Query / Lint 三服务均完成装配
Ingest：成功，约 17.9s
Query：成功，随后进入 Lint
Lint：失败
错误：src.domain.errors.GatewayError: MODEL_TIMEOUT: DIFY_TIMEOUT
```

原 T15 十次 Lint 样本最大值为 27.737 秒，刚好低于实际默认 30 秒。独立复跑跨过 30 秒后被客户端中断，因此“P95 小于 45 秒”和“配置为 60 秒”并不能证明当前程序允许 30—45 秒的合法响应完成。

## 4. 独立审查发现

### T15-R01（Blocker）：YAML 超时配置没有进入运行时，Ingest/Lint 实际仍为 30 秒

证据链：

1. `config/app.yaml` 声明 `ingest_seconds: 60`、`query_seconds: 30`、`lint_seconds: 60`。
2. `AppSettings` 没有任何 timeout 字段。
3. `_load_settings()` 只读取 YAML 的 `app` 节点和 Schema 文件，完全忽略顶层 `timeouts` 节点。
4. `build_container()` 和三个用例没有传递 timeout。
5. `IngestGateway.run()` 与 `LintGateway.run()` 的默认值都是 30 秒。
6. `DifyClient.run()` 将该默认值直接交给 HTTP 客户端。
7. 独立真实 Lint 调用在 30 秒附近返回 `MODEL_TIMEOUT: DIFY_TIMEOUT`。

影响：

- T15 报告中“ingest/lint 60s 已配置且无需改动”的结论与实际运行不一致。
- T15 目标允许 Ingest/Lint 在 45 秒内完成，但当前程序会提前在 30 秒杀掉 30—45 秒之间的合法响应。
- 现场实时主线存在已复现的阻断风险。

### T15-R02（Blocker）：Lint 页面没有实时超时后的完全匹配缓存接续

`RunLintInput` 和 `RunLint` 后端已经支持 `preferred_mode="cache"`，冻结快照也含 Lint 精确缓存；但 `src/ui/pages/lint.py` 创建命令时没有提供模式选择，捕获 `AppError` 后只显示错误。真实 Lint 超时后，用户无法在页面内继续当前演示主线。

Query 页面新增的自动精确缓存探测和 Ingest 页面已有缓存按钮均通过测试，不能替代 Lint 页面缺失的入口。演示脚本所写“三个 Key 任一失效时导入/查询/自检转为本地治理演示”也不符合当前实现：Query 没有本地语义模式，Lint 页面没有缓存或本地接续。

### T15-R03（Important）：性能证据未随冻结版本固化，报告没有纳入独立失败样本

`docs/qa/test-report-2026-08-30.md` 引用了 `/tmp/t15_perf_{op}.json`，但这些文件未跟踪；新环境无法重算 7×10 采样、核对时间戳或错误码。报告还写“失败率 0%，无超时”和“60 秒配置无需改动”，与本轮授权复跑的实际结果不再一致。

这里不是否定原 70 次样本，而是要求区分：

- 原始采样窗口内：70/70 成功；
- 当前独立复跑：Lint 出现一次可复现、与代码默认值吻合的超时；
- 整改后：必须重新生成与新代码 SHA 绑定的证据。

### T15-R04（Important）：预检第 4 项不能证明三个 Workflow 均可用

当前预检方法主要检查 `.env` 中三个 Key 在位、容器成功装配，必要时只执行一次 Query。容器装配只验证 Key 非空且互异，不会验证远端授权、Workflow 状态或实时响应；一次 Query 也不能证明 Ingest 和 Lint 可用。

因此现场开场前仍可能在第 03:00 的 Lint 环节首次发现失效或超时。当前异常口径还建议“转为本地治理演示”，但实际可用兜底必须严格区分完全匹配缓存、本地确定性能力和不可继续的语义能力。

## 5. 整改方案

### 5.1 T15-R01：打通超时配置

1. 将 `timeouts` 建成严格、可校验的配置模型；必须拒绝缺失、非整数、零值、负数和不合理上限。
2. `load_settings()` 必须读取并暴露 Ingest/Query/Lint 三个值。
3. 在组合根中把三个值分别注入对应网关或用例，禁止依赖网关函数的隐式默认值。
4. 保持目标口径：Query 30 秒，Ingest/Lint 60 秒；性能通过线仍为 Query 20 秒、Ingest/Lint 45 秒。超时时间不是性能通过线。
5. 修订测试报告中“无需改动”的错误结论。

建议新增测试：

- `test_load_settings_reads_all_workflow_timeouts`
- `test_invalid_workflow_timeout_fails_configuration`
- `test_container_routes_distinct_timeouts_to_each_workflow`
- `test_ingest_and_lint_allow_response_after_thirty_seconds`
- `test_query_still_times_out_at_configured_thirty_seconds`

### 5.2 T15-R02：补齐 Lint 精确缓存接续

1. Lint 实时调用出现 `MODEL_TIMEOUT` 时，只探测本次真实输入身份对应的完全匹配缓存。
2. 命中时可自动接续，或显示明确可点击的“使用冻结缓存”按钮；必须展示“冻结缓存”、缓存生成时间和当前 baseline 版本。
3. 未命中时显示“未找到同材料、同版本的可用缓存”，按钮禁用；不得枚举、推荐或使用近似缓存。
4. 非超时错误不得伪装成缓存接续成功。
5. 缓存接续后必须能继续完成问题选择、人工决定、审批和本地发布。

建议新增测试：

- `test_lint_timeout_continues_with_exact_frozen_cache`
- `test_lint_timeout_without_exact_cache_disables_fallback`
- `test_lint_fallback_displays_cache_provenance`
- `test_lint_fallback_can_continue_to_decision_and_publish`

### 5.3 T15-R03：冻结可审计性能证据

1. 把脱敏后的 7×10 原始记录写入版本库，例如 `docs/qa/evidence/t15-performance-samples.json`。
2. 每条只保留：操作、序号、开始时间、耗时、成功状态、公开错误码、代码 SHA、运行环境标识；不得包含 API Key、请求正文或未脱敏材料。
3. 提供确定性汇总脚本或测试，从原始记录重算 n、min、P50、P95、max、失败率。
4. 修订报告，分别记录原采样、独立失败和整改后复验，不得覆盖或隐藏失败历史。
5. 整改后至少重新执行 Lint 实时 10 次，并把结果绑定到整改提交 SHA。

### 5.4 T15-R04：把预检改成三 Workflow 实际冒烟

1. 预检第 4 项必须用固定模拟输入分别执行 Ingest、Query、Lint 的最小真实调用。
2. 三项都要记录成功/错误码、耗时和 Workflow run ID；不得记录 Key。
3. 任一项失败时，开场前明确验证对应的完全匹配缓存能从 UI 命中。
4. 修订演示脚本：只有存在完全匹配缓存的任务才能按缓存继续；本地确定性检查不得表述为 Query/Lint 语义模型的等价替代。
5. 备用视频路径、可播放结果和最近一次试播时间应进入当日预检记录；当前仓库未发现视频工件，该项需由演示设备现场负责人补证。

## 6. T15 最终通过标准

以下条件必须全部满足，才能重新签署 T15：

### A. 代码与配置

- [ ] `config/app.yaml` 的 60/30/60 三个值能在运行时被 spy/fake client 观测到。
- [ ] Ingest/Lint 的 30—45 秒模拟响应不会被 30 秒默认值提前中断。
- [ ] 非法或缺失 timeout 配置启动即失败，错误信息不泄漏 Key。
- [ ] 不扩大一期业务能力，不修改冻结 Schema，不新增页面或 Lint 类型。

### B. 三类实时与缓存行为

- [ ] Ingest、Query、Lint 分别完成最小真实 Dify 冒烟调用。
- [ ] 整改后 Lint 实时重新采样 10 次，失败数为 0，P95 小于 45 秒。
- [ ] Query、Ingest、Lint 超时后只允许完全匹配缓存。
- [ ] Lint 缓存命中、缓存 miss、非超时错误三个 UI 分支均有回归测试。
- [ ] Lint 缓存接续后，人工决定、审批和本地发布真实成功。

### C. 证据与文档

- [ ] 7×10 脱敏原始记录进入版本库，并与整改 SHA、汇总报告一致。
- [ ] 测试报告如实记录本次独立 Lint 超时、根因和整改后的复验结果。
- [ ] 演示脚本不再把本地确定性能力描述成所有 Dify 语义任务的等价替代。
- [ ] 预检第 4 项逐一验证三个 Workflow，而不是只看 Key 是否存在。
- [ ] 备用视频由现场负责人补充实际文件与试播记录。

### D. 最终门禁

- [ ] 干净副本执行依赖锁定、reset、validate 全部通过。
- [ ] 全量测试 0 failed；领域/应用覆盖率不低于当前 95%。
- [ ] Ruff、format、compileall、`git diff --check` 全部通过。
- [ ] 连续三次 `reset → validate → test_full_success` 无阻断。
- [ ] 工作区干净，提交边界仅包含 T15 阻断修复、测试和证据。
- [ ] 保留现有 `v0.2.0-live-demo` 历史标签，不强制移动；整改通过后建议建立新补丁标签 `v0.2.1-live-demo`，避免同一标签指向变化。

## 7. 推荐整改批次与停点

| 批次 | 内容 | 完成后停点 |
| --- | --- | --- |
| 1 | timeout 配置模型、读取、注入和单元/集成测试 | 展示 60/30/60 已由 client spy 观测 |
| 2 | Lint 超时精确缓存 UI、来源标识和 miss 禁用态 | 展示三条 UI 测试及页面行为 |
| 3 | Lint 缓存后决定/发布 E2E | 展示完整接续链 |
| 4 | 脱敏性能证据固化、报告与预检修订 | 展示可重算的 7×10 证据 |
| 5 | 用户授权后真实 Dify 复验与 Lint 10 次采样 | 展示耗时、失败率和 run ID，不展示 Key |
| 6 | 全量门禁、三轮主流程、干净提交和补丁标签 | 重新申请 T15 最终签署 |

## 8. 最终签署

```text
T15 功能完成度：部分通过
T15 本地质量门禁：通过
T15 真实实时演示门禁：不通过
v0.2.0-live-demo 正式冻结签署：暂缓
```

关闭 T15-R01 至 T15-R04 并满足第 6 节全部标准后，再执行一次独立复核；复核通过后可签署 T15 和新的现场演示补丁版本。
