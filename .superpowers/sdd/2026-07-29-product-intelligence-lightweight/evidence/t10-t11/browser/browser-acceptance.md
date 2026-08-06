# T10/T11 真实浏览器验收报告（v4 换证轮）

- 验收时间：2026-08-05（UTC+8）
- 验收提交 SHA：`35ea4cf`（分支 `codex/v4-remediation`，提交信息 `fix: keep random task ids clear of sensitive-data patterns`，为 v4 最终代码提交；浏览器证据在其后的纯文档提交之前拍摄，代码与该提交完全一致）
- 换证说明：本轮以 v4 最终整改代码重新执行全部浏览器验收并覆盖此前轮次截图；v4 仅收紧发布闸证据元数据校验并修复任务 ID 抖动，页面行为不变，下方测量结果为本轮实测。
- 浏览器通道：本机隔离 Chrome（`--user-data-dir=/tmp/wb-profile --remote-debugging-port=9223`，不触碰日常 Chrome）+ 官方 Kimi WebBridge 扩展 v1.11.5（Chrome Web Store 正版，经 External Extensions 自动安装）+ 本机守护进程 `http://127.0.0.1:10086`。
- 隔离工程根：`/tmp/t10t11_browser`（config 从仓库拷入，`project_root` 指向隔离目录；仓库 `data/` 全程零写入，已 `git status` 确认无污染）。
- 隔离工程初始化：`/tmp/browser_setup.py` 一键完成 bootstrap 基线 LLD-724_1 → 导入风险材料 → lint → 生成问题 → 人工决定 → 创建变更单 → 批准，产出已批准待发布变更 `CHANGE-847581BC5E6D48D9B319A90C8317371F`（目标 LLD-724_2，目标卡 RULE-LLD-001，风险源 `SRC-739BF7DF1497A8C1`）。
- 应用启动：`cd /tmp/t10t11_browser && .venv/bin/streamlit run <repo>/streamlit_app.py --server.port 8799 --server.address 127.0.0.1 --server.headless true`。

## 输入方式说明（如实记录）

WebBridge v1.11.5 下 CDP passthrough 的 `Input.*` 域对本页面无效（鼠标/键盘可信事件均不改变焦点与控件状态，已实测），扩展 `fill`/`key_type` 对 Streamlit 受控组件无效。实际操作路径：复选框与按钮经扩展 `click`（DOM 级点击，React onChange/onClick 真实触发并同步服务端会话）；发布说明文本域经「HTMLTextAreaElement 原生 setter + `input` 事件 + `focusout` 事件」提交（Streamlit text_area 在 blur 时向服务端提交，前两种方式均实测不同步，加 `focusout` 后服务端校验通过）。服务端校验、确认弹窗、发布闸、失败回退全部为真实应用行为，无任何 Mock。

## 验收视口与证据

| 证据文件 | 视口 | 内容 |
| --- | --- | --- |
| `release-desktop-1440x1024.png` | 1440×1024 | 发布页桌面布局（已批准候选、修改前后对照、发布操作区） |
| `release-confirm-mobile-390x844.png` | 390×844 @2x | 「人工确认」弹窗（358×392，位于 (16,48)，完整在视口内） |
| `release-failure-mobile-390x844.png` | 390×844 @2x | 篡改归档后发布失败告警，错误码 `PUBLISH_SOURCE_INTEGRITY_FAILED` |
| `release-success-mobile-390x844.png` | 390×844 @2x | 还原归档重试后「新基线已发布并生效。」 |
| `trace-six-node-mobile-390x844.png` | 390×844 @2x（整页） | 六节点纵向堆叠完整链路 |
| `trace-six-node-desktop-1440x1024.png` | 1440×1024 | 六节点横向链路首屏完整可见 |

## 测量结果（CDP `Runtime.evaluate` 实测）

1. 横向溢出：发布页桌面 1440、追溯页移动 390、追溯页桌面 1440 三处 `document.documentElement.scrollWidth === clientWidth`（390/390、1440/1440、1440/1440），无横向滚动。
2. 确认弹窗边界：390×844 视口下 `[role=dialog]` 矩形 `x=16 y=48 w=358 h=392`，`right=374 ≤ 390`、`bottom=440 ≤ 844`，完整在视口内。
3. 六节点顺序（两视口一致，实测卡片文本）：原始资料（SRC-LLD-BASE，已入库）→ 结构化知识（RULE-LLD-001，生效中）→ 问题（ISSUE-74C2DDBE13743AEA87E0，已决定）→ 人工决定（DECISION-6B01593A69124A5B98E5C6F09727298F，接受迭代）→ 变更单（CHANGE-7206FD6A880349B9A65B179A306ECBA9，已发布，目标 LLD-724_2）→ 生效基线（BASE-LLD-724_2 / LLD-724_2，生效中）。发布前同页仅 5 节点并提示「缺失环节：生效基线」属预期行为。
4. 移动端六节点：每卡 `x=20 w=350 right=370 ≤ 390`，纵向 y=323…1563 堆叠不越界。
5. 桌面端六节点：横向排列 y≈354，最高卡 bottom=515 ≤ 1024，六节点首屏全部可见。

## 失败→回退→重试验证（真实篡改测试）

1. 篡改：`/tmp/t10t11_browser/data/source_archive/LLD/SRC-LLD-BASE/当前产品方案.md` 追加 27 字节（原文 19731 字节，先完整备份）。
2. 点击「确认发布新基线」→ 页面告警：「发布未完成｜失败步骤：原子发布｜正式来源材料未通过发布前完整性校验｜错误码：PUBLISH_SOURCE_INTEGRITY_FAILED」，并提示「原版本仍然生效。变更保持已批准状态，重新校验后可重试发布，不会重复批准。」——M2 新增发布闸在浏览器端真实生效。
3. 失败后服务端状态（直接读库验证）：`current_baseline.json` 仍为 `LLD-724_1`；`product_intelligence.db` 中变更单仍为 `approved`、基线仅 `BASE-LLD-724_1 effective`。
4. 还原归档（SHA-256 与备份一致）→ 重新点击「重新校验并发布」→ 弹窗 → 确认 → 成功：「新基线已发布并生效。」
5. 成功后服务端状态：`current_version = LLD-724_2` 且 `change_request_id` 关联该变更；`baselines` 表 `LLD-724_1 superseded` / `LLD-724_2 effective`；变更单 `published`；`02_Current_Baseline/LLD-724_2/release.json` 写入 `release_note`（发布说明：调整目标客群规则，保留版本间差异。）与逐文件 sha256。

## 控制台检查

`Page.addScriptToEvaluateOnNewDocument` 注入对本次导航未生效（passthrough 限制，如实记录），改用页内注入 console 钩子（error/warn/window.onerror/unhandledrejection 四类）后执行真实交互（展开/收起节点详情触发 rerun），结果：`errors: []`、`warnings: []`；全程页面无 `stException` 组件（0 个），失败场景仅出现业务告警 `stAlert`。

## 结论

T10（原子发布）与 T11（六节点追溯）在真实浏览器、真实隔离工程、真实篡改攻击下行为符合 v3/v4 修改建议预期：发布闸前移至创建临时目录之前、证据元数据（citation 身份/版本/locator/excerpt）全字段绑定、来源完整性失败可安全回退并免重复批准重试、六节点链路在双视口完整有序且无横向溢出。本轮截图以 v4 最终代码提交 `35ea4cf` 重新拍摄，覆盖此前轮次旧图。
