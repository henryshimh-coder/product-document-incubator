# T10/T11 真实浏览器验收报告（v4 补充加固换证轮 + T12 失败截图换证轮）

- 验收时间：2026-08-05（UTC+8）；失败截图换证：2026-08-06（UTC+8）
- 验收提交 SHA：`c694778`（分支 `feat/lightweight-t01`，提交信息 `fix: bind baseline evidence to target card and exact excerpt`）；失败截图换证轮 SHA：`46de3fd`（分支 `codex/t12-remediation`，提交信息 `fix: close snapshot safety gaps and wire query lint cache consumers`，T12 整改最终代码提交，拍摄时工作区代码与该提交完全一致）。
- 换证说明：2026-08-05 轮按评审方追加要求，在基线证据目标卡片绑定 + 摘录精确等值加固后的最终代码上重新执行全部浏览器验收并覆盖此前轮次截图。2026-08-06 轮针对 T12 独立评审指出的「失败截图不自洽」问题（原 `release-failure-mobile-390x844.png` 仅见「发布操作」标题、不含失败告警），在 T12 整改代码上以全新隔离工程完整复现「篡改归档 → 发布失败 → 安全重试成功」链路并重拍失败截图；T12 整改不涉及发布页行为，其余截图仍取自 `c694778` 轮次，本报告两处 SHA 均如实标注。
- 浏览器通道：本机隔离 Chrome（`--user-data-dir=/tmp/wb-profile --remote-debugging-port=9223`，不触碰日常 Chrome）+ 官方 Kimi WebBridge 扩展（经 External Extensions 自动安装）+ 本机守护进程 `http://127.0.0.1:10086`。
- 隔离工程根：`/tmp/t10t11_browser`（config 从仓库拷入，`project_root` 指向隔离目录；仓库 `data/` 全程零写入，已 `git status` 确认无污染）。2026-08-06 轮以 `46de3fd` 代码重建该工程。
- 隔离工程初始化：`/tmp/browser_setup.py` 一键完成 bootstrap 基线 LLD-724_1 → 导入风险材料 → lint → 生成问题 → 人工决定 → 创建变更单 → 批准。2026-08-06 轮产出已批准待发布变更 `CHANGE-C04A27059B28417697DB2B5C14F67C04`（目标 LLD-724_2，目标卡 RULE-LLD-001，风险源 `SRC-739BF7DF1497A8C1`）。
- 应用启动：`cd /tmp/t10t11_browser && <repo>/.venv/bin/streamlit run <repo>/streamlit_app.py --server.port 8799 --server.address 127.0.0.1 --server.headless true`（临时进程，拍摄结束后已停止并确认端口释放）。

## 输入方式说明（如实记录）

WebBridge v1.11.5 下 CDP passthrough 的 `Input.*` 域对本页面无效（鼠标/键盘可信事件均不改变焦点与控件状态，此前轮次已实测），扩展 `fill`/`key_type` 对 Streamlit 受控组件无效。实际操作路径：复选框与按钮经扩展 `click`（DOM 级点击，React onChange/onClick 真实触发并同步服务端会话）；发布说明文本域经「HTMLTextAreaElement 原生 setter + `input` 事件 + `focusout` 事件」提交（Streamlit text_area 在 blur 时向服务端提交）。服务端校验、确认弹窗、发布闸、失败回退全部为真实应用行为，无任何 Mock。

## 验收视口与证据

| 证据文件 | 视口 | 内容 |
| --- | --- | --- |
| `release-desktop-1440x1024.png` | 1440×1024 | 发布页桌面布局（已批准候选、修改前后对照、发布操作区） |
| `release-confirm-mobile-390x844.png` | 390×844 @2x | 「人工确认」弹窗（358×392，位于 (16,48)，完整在视口内） |
| `release-failure-mobile-390x844.png` | 390×844 @2x | **2026-08-06 于 `46de3fd` 重拍**：篡改归档后发布失败告警（红色 stAlert：发布未完成｜失败步骤：原子发布｜错误码 `PUBLISH_SOURCE_INTEGRITY_FAILED`）与安全重试提示（蓝色 stAlert：原版本仍然生效……不会重复批准）同屏完整可见 |
| `release-success-mobile-390x844.png` | 390×844 @2x | 还原归档重试后「新基线已发布并生效。」 |
| `trace-six-node-mobile-390x844.png` | 390×844 @2x | 追溯页移动端首屏（该区域内容为确定性 ID，渲染结果与上轮一致） |
| `trace-six-node-mobile-390-full.png` | 390×1700 @2x（扩展高度整链图） | 六节点纵向堆叠完整链路（含首卡详情展开与已脱敏原文片段） |
| `trace-six-node-desktop-1440x1024.png` | 1440×1024 | 六节点横向链路首屏完整可见 |

## 测量结果（CDP `Runtime.evaluate` 实测）

1. 横向溢出：发布页桌面 1440、追溯页移动 390、追溯页桌面 1440 三处 `document.documentElement.scrollWidth === clientWidth`（1440/1440、390/390、1440/1440），无横向滚动。
2. 确认弹窗边界：390×844 视口下 `[role=dialog]` 矩形 `x=16 y=48 w=358 h=392`，`right=374 ≤ 390`、`bottom=440 ≤ 844`，完整在视口内。
3. 六节点顺序（两视口一致，实测卡片文本）：原始资料（SRC-LLD-BASE，已入库）→ 结构化知识（RULE-LLD-001，生效中）→ 问题（ISSUE-74C2DDBE13743AEA87E0，已决定）→ 人工决定（DECISION-5AD568FF432E4122A443F72FD4877D16，接受迭代）→ 变更单（CHANGE-59C3FB2F8D2F456B8456DE8161A104B8，已发布，目标 LLD-724_2）→ 生效基线（BASE-LLD-724_2 / LLD-724_2，生效中）。
4. 移动端六节点：390×844 标准视口下每卡 `x=20 w=350 right=370 ≤ 390`，纵向 y=307…1435 堆叠不越界，最大 bottom=1547。整链呈现：CDP `captureBeyondViewport` 在设备仿真覆盖下无效（如实记录，此前轮次标注"整页"的 390×844 图实际仅为首屏），本轮改用扩展仿真高度 390×1700 拍摄 `trace-six-node-mobile-390-full.png`，六节点单图完整可见；布局测量仍在 390×844 标准视口下进行。
5. 桌面端六节点：横向排列 y≈354（首卡 y=315），最大 bottom=555 ≤ 1024，最大 right=1404 ≤ 1440，六节点首屏全部可见。

## 失败→回退→重试验证（真实篡改测试）

### 2026-08-06 复验（`46de3fd`，T12 失败截图换证轮）

1. 篡改：`/tmp/t10t11_browser/data/source_archive/LLD/SRC-LLD-BASE/当前产品方案.md` 追加 58 字节（原文 19731 字节，先完整备份至 `/tmp/t12_archive_backup.md`）。
2. 勾选「我已检查修改前后、依据和影响」→ 填写发布说明（29 字）→ 点击「重新校验并发布」→「人工确认」弹窗（变更单 `CHANGE-C04A27059B28417697DB2B5C14F67C04`）→ 确认 → 页面告警两条：`发布未完成｜失败步骤：原子发布｜正式来源材料未通过发布前完整性校验｜错误码：PUBLISH_SOURCE_INTEGRITY_FAILED`（红色 stAlert）与 `原版本仍然生效。变更保持已批准状态，重新校验后可重试发布，不会重复批准。`（蓝色 stAlert）。告警矩形 x=20 y=167 w=350 h=128，完整位于 390×844 视口内；`release-failure-mobile-390x844.png` 即为该时刻实拍，错误码与重试提示同屏可见。
3. 失败后服务端状态（只读直查隔离库）：`current_baseline.json` 仍为 `LLD-724_1` 且 `change_request_id` 为空；`change_requests` 中该变更仍为 `approved`；`baselines` 仅 `LLD-724_1 effective`。
4. 还原归档（SHA-256 `71d19c2f…` 与备份一致）→ 重新勾选 → 重填发布说明 → 点击「重新校验并发布」→ 弹窗 → 确认 → 成功：`新基线已发布并生效。`
5. 成功后服务端状态：`current_version = LLD-724_2` 且 `change_request_id` 关联该变更；`baselines` 表 `LLD-724_1 superseded` / `LLD-724_2 effective`；变更单 `published`。

### 2026-08-05 实测（`c694778`，v4 补充加固换证轮）

1. 篡改：`/tmp/t10t11_browser/data/source_archive/LLD/SRC-LLD-BASE/当前产品方案.md` 追加 29 字节（原文 19731 字节，先完整备份）。
2. 点击「确认发布新基线」→ 页面告警：「发布未完成｜失败步骤：原子发布｜正式来源材料未通过发布前完整性校验｜错误码：PUBLISH_SOURCE_INTEGRITY_FAILED」，并提示「原版本仍然生效。变更保持已批准状态，重新校验后可重试发布，不会重复批准。」——发布闸在浏览器端真实生效。
3. 失败后服务端状态（直接读库验证）：`current_baseline.json` 仍为 `LLD-724_1` 且 `change_request_id` 为空；`product_intelligence.db` 中变更单仍为 `approved`、基线仅 `BASE-LLD-724_1 effective`。
4. 还原归档（SHA-256 与备份一致）→ 重新点击「重新校验并发布」→ 弹窗 → 确认 → 成功：「新基线已发布并生效。」
5. 成功后服务端状态：`current_version = LLD-724_2` 且 `change_request_id` 关联该变更；`baselines` 表 `LLD-724_1 superseded` / `LLD-724_2 effective`；变更单 `published`；`02_Current_Baseline/LLD-724_2/release.json` 写入 `release_note`（发布说明：调整目标客群规则，保留版本间差异。）与逐文件 sha256。

## 控制台检查

`Page.addScriptToEvaluateOnNewDocument` 注入对本次导航未生效（passthrough 限制，如实记录），改用页内注入 console 钩子（error/warn/window.onerror/unhandledrejection 四类）后执行真实交互（展开/收起节点详情），结果：`errors: []`、`warnings: []`；全程各关键步骤（发布失败、发布成功、追溯页）页面 `stException` 组件均为 0 个，失败场景仅出现业务告警 `stAlert`。

## 结论

T10（原子发布）与 T11（六节点追溯）在真实浏览器、真实隔离工程、真实篡改攻击下行为符合 v4 及补充加固修改建议预期：发布闸前移至创建临时目录之前、证据绑定（正式来源全字段；基线侧 citation 身份 + 目标卡片受控关系 + 摘录精确等值）、来源完整性失败可安全回退并免重复批准重试、六节点链路在双视口完整有序且无横向溢出。2026-08-05 轮截图以最终代码提交 `c694778` 重新拍摄，覆盖此前轮次旧图；2026-08-06 轮针对 T12 独立评审指出的失败截图不自洽问题，在 T12 整改代码 `46de3fd` 上以全新隔离工程复验「篡改 → 失败 → 重试成功」全链路（结果与上轮一致），并重拍 `release-failure-mobile-390x844.png` 使失败告警、错误码与安全重试提示同屏可见，T12 评审 T12-P1-04 就此关闭。
