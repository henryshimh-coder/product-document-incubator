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
| 7 | 实时／缓存标识 | 通过。查询结果页明示「实时生成」标识与基线版本；缓存态语义由 E2E（超时回退精确缓存，`tests/e2e/test_realtime_timeout_fallback.py`）与本地缓存条目佐证 | 03-query.png |
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

## 截图清单

| 文件 | 内容 |
| --- | --- |
| 01-home.png | 首页：`LLD-724_3` hero、唯一主操作「导入新资料」、六导航、项目概况/最近活动 |
| 02-ingest.png | 导入页：三步指示器 + 安全边界表单（拍摄时默认适用版本为修复前状态，见缺陷 1） |
| 03-query.png | 查询页：回答/版本/引用/候选提示/证据充分度/实时生成标识 |
| 04-lint.png | 自检页：双引用问题 + 覆盖率预算准确错误消息（设计行为） |
| 05-release.png | 发布成功态 |
| 05b-release-diff.png | 修改前/修改后双栏 Diff |
| 05c-release-confirm.png | 人工确认弹窗 |
| 06-trace.png | 追溯页：六节点主链终止于 `LLD-724_3`（生效中）+ 市场证据缺口 |
