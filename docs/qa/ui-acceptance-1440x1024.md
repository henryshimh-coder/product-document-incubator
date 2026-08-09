# UI 验收记录（1440×1024）

- 验收日期：2026-08-07 至 2026-08-09
- 视口：1440×1024，deviceScaleFactor=1（CDP `Emulation.setDeviceMetricsOverride` 实测 `innerWidth=1440, innerHeight=1024, dpr=1`）
- 环境：本地 Streamlit 演示实例（127.0.0.1:8799），数据目录为演示快照推进到 `LLD-724_3` 的真实状态；外部模型侧为本地 mock 网关（与联合验收同构的 fixtures），非真实 Dify。
- 操作方式：真实 Chrome 浏览器（WebBridge 扩展驱动）逐页操作与截图，非静态渲染推断。
- 验收会话期间的版本演进：导入/查询/自检/发布四张流程截图（02–05c）拍摄于 `LLD-724_2 → LLD-724_3` 发布流程执行期间；首页（01）与追溯页（06）于发布完成后重拍，反映 `LLD-724_3` 生效的最终状态。

## 逐条核对（计划 Task 13 Step 8 的 10 条）

| # | 验收条目 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 六个导航顺序 | 通过。侧边栏固定为「项目首页 / 资料导入 / 当前查询 / 一键自检 / 变更发布 / 追溯与价值」，DOM 顺序与之一致 | 01-home.png |
| 2 | 当前版本视觉优先级 | 通过。首页 hero 区 `LLD-724_3` 为全页最大字号，配「当前生效」状态徽章与最近更新时间；项目概况不拆 KPI 卡 | 01-home.png |
| 3 | 每页唯一主操作 | 通过。DOM 实测（选择器 `a.pi-button--primary, button[kind="primary"], [data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"]`）：首页 1（导入新资料）、导入页 1（开始编译）、查询页 1（查询）、自检页 1（启动一键自检）、发布页 0（当前无待发布变更；有待发布变更时为 1，见 05c 批准并发布）、追溯页 0（纯只读页，无主操作属预期） | 01–06 全部截图 |
| 4 | 状态不仅依赖颜色 | 通过。状态均有文字标注（当前生效 / 已替代 / 已发布 / 已决定 / 接受迭代 / 已入库等），开放问题与证据充分度用文字+说明，不依赖颜色区分 | 04-lint.png、06-trace.png |
| 5 | 无卡片嵌套 | 通过。六页 DOM 实测 `stVerticalBlockBorderWrapper` 嵌套计数均为 0 | 01–06 全部截图 |
| 6 | 无横向滚动 | 通过。六页 DOM 实测 `scrollWidth ≤ innerWidth`，1440×1024 下均无横向滚动条 | 01–06 全部截图 |
| 7 | 实时／缓存标识 | 通过。实时态：查询结果页明示「实时生成」（03-query.png）。缓存态：查询页新增「查询方式：实时查询／冻结缓存」选择，frozen 快照真实执行一次 Query cache，结果页同时可见问题、回答、基线版本 LLD-724_1 与「冻结缓存 · 缓存生成时间 2026-08-07T04:20:46+00:00」，无「实时生成」字样（03b-query-cache.png；查询方式区补充见 03c-query-cache-mode.png）。UI 回归测试 `test_cached_result_shows_frozen_cache_baseline_and_generation_time` 在移除缓存标签映射时失败、恢复后通过 | 03-query.png、03b-query-cache.png、03c-query-cache-mode.png |
| 8 | 重大问题双引用 | 通过。待决定问题同时展示依据 A（当前基线侧引用）与依据 B（挑战来源侧引用） | 04-lint.png |
| 9 | 发布前后 Diff | 通过。发布页提供修改前/修改后双栏 Diff，批准前必经人工确认弹窗 | 05b-release-diff.png、05c-release-confirm.png |
| 10 | 追溯主链一屏可读 | 通过。六节点主链（原始资料→来源于→结构化知识→冲突于→问题→会议决定→建议修改→变更单→批准形成→生效基线 `LLD-724_3`）一屏完整可读；市场证据缺口区明示「未验证判断，不能作为事实依据」；完整追溯/价值验证/调用审计三标签页齐全 | 06-trace.png |

## 观察项（如实记录，不构成阻断）

1. **首页 eyebrow 文字裁切**：首页「当前项目」eyebrow 上半部被 Streamlit 框架顶部工具栏（stToolbar）遮挡，属框架层叠加问题，不影响导航与主内容可读性（见 01-home.png 顶部）。
2. **框架原生 chrome**：右上角「Deploy」按钮为 Streamlit 框架原生元素，非产品 UI 内容。
3. **自检覆盖率预算阻断（设计行为）**：发布 `LLD-724_3` 后 notices 被消费，全部当前资料范围下可比对材料只剩小基线，出站覆盖率 37.7% 超过 25% 预算，自检按设计 fail-closed 报 `OUTBOUND_COVERAGE_EXCEEDED` 并给出准确错误消息（见 04-lint.png）。这是治理正确行为，不是缺陷。

## 验收期间发现并修复的缺陷（共 4 项，均已修复并回归）

1. **导入页适用版本硬编码**：`src/ui/pages/ingest.py` 默认适用版本写死 `LLD-724_1`，改为从 dashboard 视图读取当前基线版本（`_default_baseline_version`），异常时回退空值。
2. **自检/查询错误消息笼统**：小材料触发覆盖率预算时被笼统报成「资料尚未完成脱敏确认」。`run_lint.py`、`run_query.py` 补齐与导入同约定的覆盖率/尺寸预检（`_canonical_input_chars`），越界抛出准确的 `OUTBOUND_COVERAGE_EXCEEDED`。
3. **旧 UI 测试适配**：`tests/e2e/test_ingest_flow.py` 因导入页默认值变化挂掉 9 个用例，已补充显式设置适用版本。
4. **追溯主链断链（本轮发现）**：ingest 来源的冲突问题未持久化 `target_rule_id` 与「规则卡→问题」`conflicts_with` 关系（仅 lint 路径持久化），导致发布 `LLD-724_3` 后追溯主链仍终止于已替代的 `LLD-724_2`。修复：`import_source._to_domain` 持久化 `target_rule_id` 并补回连关系（与 run_lint 同约定）；`IngestUnitOfWork.complete` 的 issue INSERT 补上 `target_rule_id` 列；`build_trace._walk_chain` 同级多链决胜由「链长」改为「链长 + 链尾节点发生时间」，保证主链反映最新一次演化。新增回归测试 `test_trace_prefers_latest_complete_chain_on_ties`（新链问题创建更早、基线更新，与真实演示数据同构）。修复后 06-trace.png 主链正确终止于 `LLD-724_3`（生效中）。

## 独立评审整改（2026-08-09，T13-R01/T13-R02）

- **T13-R01（查询页缓存态证据）**：`src/ui/pages/query.py` 新增「查询方式」选择（实时查询／冻结缓存，`preferred_mode` 透传），缓存态标签由「演示缓存」更正为「冻结缓存」，并与缓存生成时间同行展示（`冻结缓存 · 缓存生成时间 …`，单行布局保证 1440×1024 一屏内问题/回答/版本/标签同显无截断）。新增 UI 回归测试 `test_cached_result_shows_frozen_cache_baseline_and_generation_time`（移除缓存标签映射时失败、恢复后通过）。浏览器实演：从 frozen 快照恢复独立目录启动应用，选择冻结缓存执行「当前目标客群是什么？」，命中真实冻结缓存（生成时间 2026-08-07T04:20:46+00:00），证据 03b/03c。
- **T13-R02（完整成功 E2E 恒真断言）**：`tests/e2e/test_full_success.py` 删除 `assert PUBLISHED_RULE_CONTENT` 恒真断言，改为从 Manifest 指向的可信产物验证：`full.md` 含新规则文本且不再含旧规则文本、`cards.json` 中 `RULE-LLD-001.content` 等于变更单 `after_content`，并补发布后实时查询断言（回答=新规则文本、版本=LLD-724_2）。配套破坏性反证用例 `test_publish_artifact_assertions_fail_when_content_reverted`：产物被改回旧规则时断言必然失败。

## 截图清单

| 文件 | 内容 |
| --- | --- |
| 01-home.png | 首页：`LLD-724_3` hero、唯一主操作「导入新资料」、六导航、项目概况/最近活动 |
| 02-ingest.png | 导入页：三步指示器 + 安全边界表单（拍摄时默认适用版本为修复前状态，见缺陷 1） |
| 03-query.png | 查询页：回答/版本/引用/候选提示/证据充分度/实时生成标识 |
| 03b-query-cache.png | 查询页缓存态（T13-R01）：问题、回答、基线版本 LLD-724_1、「冻结缓存 · 缓存生成时间 2026-08-07T04:20:46+00:00」一屏同显 |
| 03c-query-cache-mode.png | 查询页缓存态补充：查询方式选择「冻结缓存」+ 精确匹配说明 |
| 04-lint.png | 自检页：双引用问题 + 覆盖率预算准确错误消息（设计行为） |
| 05-release.png | 发布成功态 |
| 05b-release-diff.png | 修改前/修改后双栏 Diff |
| 05c-release-confirm.png | 人工确认弹窗 |
| 06-trace.png | 追溯页：六节点主链终止于 `LLD-724_3`（生效中）+ 市场证据缺口 |
