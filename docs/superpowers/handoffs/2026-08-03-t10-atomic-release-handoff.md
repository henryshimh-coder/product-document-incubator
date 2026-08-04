# T10 交接文档：变更检查、人工批准、原子发布和恢复

## 1. 接手结论

- 项目：产品智策轻量交付版，路线 A。
- 当前分支：`feat/lightweight-t01`。
- 接手基线提交：`30a2332 fix: complete governed lint workflow`。
- 当前进度：T01—T09 已完成；T10 尚未开始；不得提前进入 T11。
- T10 目标：把 T09 生成的 `pending_approval` ChangeRequest 走完“人工复核 → 原子发布 → 新基线生效 → SQLite 镜像恢复”的本地可信闭环。
- 计划工期：3.5 人天，正式计划窗口为 2026-08-14—2026-08-18。
- 现场目标：9 月汇报可实时演示完整流程；正式演示版须在 8 月 30 日就绪。

接手 agent 开始前必须确认：

```bash
git status --short
git rev-parse --short HEAD
.venv/bin/python -m pytest -q
```

期望：工作区干净，HEAD 为 `30a2332`，基线为 `471 passed`。若基线不一致，先停止并报告，不要在未知改动上继续。

建议从该提交建立独立分支：

```bash
git switch -c codex/t10-atomic-release 30a2332
```

## 2. 必须遵循的方案和文档

按以下优先级执行：

1. 任务范围、步骤和验收：`docs/superpowers/plans/2026-07-29-product-intelligence-lightweight.md` 的 `Task 10`，约第 1423—1619 行。
2. 领域契约和发布算法：`产品智策_轻量交付版_技术开发文档_v1.0.md` 的 14.6、14.7 和第十九章。
3. 页面结构和交互：`产品智策_轻量交付版_产品与界面设计文档_v1.0.md` 的 7.5“变更发布”。
4. 当前真实代码：以 `30a2332` 为准；优先复用既有领域模型、策略、仓储和 UI 组件。
5. T09 上下文：`.superpowers/sdd/2026-07-29-product-intelligence-lightweight/task-9-implementer-report.md` 和 `progress.md`。

如文档描述与当前实现存在小差异，先守住以下不可变约束，再做最小兼容修改：

- Manifest 是当前生效基线的唯一权威。
- AI 不得批准变更或发布版本。
- 未批准、未完成影响复核、沙盘资料或完整性失败均不得发布。
- Manifest 原子替换前的任何失败都不能改变当前版本。
- Manifest 替换后的 SQLite 失败必须按 Manifest 修复，不能回滚或反向覆盖 Manifest。
- 发布失败后保留 `approved`，重试发布时不得重复创建批准记录。

## 3. 已完成能力和可复用接口

| 能力 | 当前状态 | 直接复用位置 |
|---|---|---|
| 当前基线领域模型 | 已完成 | `src/domain/models.py`：`Baseline`、`BaselineManifest` |
| 变更单及审计字段 | 已完成 | `ChangeRequest` 已含修改前后、依据、影响对象、批准角色、演示确认人、目标版本和 review 字段 |
| 复核枚举和状态 | 已完成 | `ChangeReviewAction`、`ChangeStatus` |
| 状态迁移 | 已完成 | `src/domain/policies/state_transition.py` |
| 发布前置策略 | 已完成 | `src/domain/policies/release_policy.py` |
| Manifest 原子替换 | 已完成 | `ManifestStore.atomic_replace()`；含文件和目录 `fsync` |
| Manifest 耐久性不确定信号 | 已完成 | `ManifestDurabilityUncertainError`；异常发生时替换可能已经成功，不能当作旧 Manifest 未变 |
| 基线文件基础读写 | 部分完成 | `MarkdownStore` 已支持卡片结构化读写和哈希；尚无发布临时目录和 Diff 能力 |
| Manifest/SQLite 只读完整性检查 | 已完成 | `ManifestIntegrityChecker` |
| SQLite 仓储 | 部分完成 | Project、Baseline、Change、Event 仓储已存在；缺少发布镜像原子 UoW |
| 复核持久化入口 | 部分完成 | `SqliteChangeRepository.record_review()` 已存在；尚未与复核事件组成同一事务 |
| 事件审计 | 已完成基础能力 | `EventLogger` 支持 SQLite、JSONL 和 reconcile；发布事件不得包含敏感原文 |
| 容器和导航 | 部分完成 | release 路由已注册，T09 的“前往变更发布”已接通；容器尚无 review/publish/recovery 服务 |
| 发布页 | 占位 | `src/ui/pages/release.py` 当前只有标题和说明 |

T09 已明确完成以下边界，不要重做：

- 接受迭代会生成且只生成 `pending_approval` ChangeRequest。
- 目标卡、修改前原文、依据引用、影响对象和下一版本已在创建阶段做可信校验。
- 决定、Issue 状态和 ChangeRequest 使用 `BEGIN IMMEDIATE` 原子落库。
- T09 没有任何批准、发布、Manifest 替换或恢复逻辑。

## 4. T10 负责范围

### 4.1 必须完成

1. 四种人工复核：批准、驳回、暂缓、退回补充。
2. 复核幂等：同 key 同命令返回同一结果；同 key 不同命令必须 fail closed。
3. 未批准发布阻断。
4. 项目级文件锁和完整原子发布流程。
5. 新版本目录：`full.md`、`cards.json`、`diff.md`、`release.json`。
6. 候选 Manifest 构建、哈希校验和原子替换。
7. SQLite 镜像的单事务更新：旧基线 superseded、新基线 effective、ChangeRequest published、Project 当前基线更新、发布事件写入。
8. Manifest/SQLite 启动对账、幂等修复和发布保护。
9. 变更 Diff 页面、确认弹窗、成功结果和失败恢复状态。
10. 应用容器接线、Focused/全量测试、真实桌面和窄屏浏览器验收。

### 4.2 不属于 T10

- 不实现 T11 的完整追溯页、市场证据缺口或轻量成本计算。
- 不实现损益测算。
- 不修改 Dify Ingest、Query、Lint 工作流或 Lint v2 输入契约。
- 不把演示确认包装成生产电子会签。
- 不增加 AI 自动审批、自动发布或绕过人工确认。
- 不重构 T01—T09 的无关模块，不顺手处理已登记的非阻断小问题。

设计文档在发布详情中提到市场和成本结果，但对应计算属于 T11。T10 只展示当前 ChangeRequest 已有的依据和影响对象；不要制造假结果或提前实现 T11。

## 5. 代码和产物放置位置

正式计划要求的文件：

```text
src/application/dto/release.py
src/application/use_cases/review_change_request.py
src/application/use_cases/publish_baseline.py
src/infrastructure/recovery/__init__.py
src/infrastructure/recovery/reconciliation_service.py
src/infrastructure/recovery/release_guard.py
src/infrastructure/files/manifest_store.py
src/infrastructure/files/markdown_store.py
src/ui/components/change_diff.py
src/ui/pages/release.py

tests/integration/use_cases/test_review_change_request.py
tests/integration/use_cases/test_publish_baseline.py
tests/integration/recovery/test_reconciliation.py
tests/e2e/test_release_flow.py
```

为完成真实接线，允许按最小范围修改：

```text
src/application/container.py
src/application/ports/repositories.py
src/domain/errors.py
src/infrastructure/db/repositories.py
src/infrastructure/db/migrations.py        # 仅在确有 schema 缺口时
src/ui/navigation.py                       # release 路由已存在，通常无需改结构
tests/unit/test_config.py
tests/integration/db/test_repositories.py
tests/e2e/test_home_page.py
tests/e2e/test_query_flow.py
```

若需要新增复核/发布 SQLite UoW，放在 `src/application/ports/` 的窄端口和 `src/infrastructure/db/repositories.py` 的 SQLite 实现中；不要让 use case 直接执行 SQL。

实施报告写入：

```text
.superpowers/sdd/2026-07-29-product-intelligence-lightweight/task-10-implementer-report.md
```

进度追加到：

```text
.superpowers/sdd/2026-07-29-product-intelligence-lightweight/progress.md
```

测试数据必须使用 `tmp_path` 或独立临时根目录。不要把运行生成的数据库、Manifest、基线目录、锁文件或 QA 数据提交到项目 `data/`。

## 6. 推荐实施顺序

正式计划要求测试先行，按下面顺序执行；不要一次铺开全部实现。

### Step 1：DTO 和四种复核 RED

- 新增严格、冻结、`extra='forbid'` 的 `ReviewChangeRequestInput` 和 `PublishBaselineInput`。
- 复核输入：change ID、action、reviewed_by、10—200 字 comment、idempotency_key。
- 发布输入：project ID、change ID、approved_by、impact_reviewed、20—200 字 release_note。
- 先写四种状态映射、同 key 幂等、冲突 key、非 `pending_approval` 不可首次复核的测试。

### Step 2：实现 ReviewChangeRequest

- 状态映射固定为：approve → approved、reject → rejected、defer → deferred、request_info → needs_info。
- 先按幂等 key 查询，再校验“同 key 是否同命令”。
- 首次复核只允许 `pending_approval`。
- 状态更新和 `change_reviewed` 事件必须处于同一 `BEGIN IMMEDIATE` SQLite 事务，并在事务内重读状态以防并发复核。
- SQLite 提交后再追加 JSONL；追加失败保留 SQLite 事实并交给 `EventLogger.reconcile()`，不得撤销已完成复核。
- 时间和 ID 通过可注入 clock/ID factory 或稳定 helper 生成，测试不得依赖真实当前时间。

### Step 3：发布失败边界 RED

至少先覆盖：

- pending/rejected/deferred/needs_info 不能发布；
- `impact_reviewed=False` 不能发布；
- 发布说明长度非法；
- Manifest 哈希错误；
- 目标版本已存在；
- 文件生成、候选校验、目录提交和 Manifest 替换失败时旧 Manifest 保持；
- 锁已被占用时返回 `RELEASE_LOCKED`；
- 同一变更重复发布被阻断。

### Step 4：扩展 MarkdownStore 和 ManifestStore

`MarkdownStore` 应提供窄方法完成：创建同文件系统临时目录、结构化更新卡片、生成 full/diff/release 文件、提交最终目录、清理临时目录、隔离未被 Manifest 引用的最终目录。

实现约束：

- `cards.json` 必须用 JSON + `KnowledgeCard` 结构化解析，精确更新一个 `target_card_id`，不得做字符串替换。
- 必须再次校验目标卡内容等于 `before_content`，并把新卡版本更新为 `target_version`。
- `full.md` 只能替换目标章节或唯一精确原文；出现 0 次或多次均 fail closed，禁止全局盲替换。
- `diff.md` 只保存可审计差异，不包含密钥或不必要的敏感全文。
- `release.json` 至少记录父版本、目标版本、变更单、批准人、发布时间、发布说明和文件哈希。
- 所有相对路径必须解析后仍位于项目根目录和基线根目录内；版本号不能造成路径穿越。
- 最终目录提交必须为同文件系统原子 rename，并对必要文件/目录执行 `fsync`。

`ManifestStore` 补充 `build_candidate()` 和 `validate_candidate()`；保留现有 `atomic_replace()` 及其异常语义，不要另写低质量替代实现。

### Step 5：实现 PublishBaseline 和发布锁

顺序不可调整：

```text
取得项目发布锁
→ 读取并校验当前 Manifest 与文件哈希
→ 校验 approved ChangeRequest、影响复核、项目和正式/沙盘边界
→ 临时目录生成 full.md、cards.json、diff.md、release.json
→ 构建并校验候选 Manifest
→ 原子提交目标版本目录
→ 原子替换 Manifest
→ SQLite 单事务更新镜像与发布事件
→ 追加/对账 JSONL 审计
→ 返回 Manifest 对应的新 Baseline
```

关键规则：

- 使用项目依赖中的 `filelock`，锁路径必须按项目隔离。
- `approved_by` 取人工复核结果的操作员，不能由 AI 或材料内容提供。
- 发布前再次执行 `ReleasePolicy`；不要只依赖 UI 校验。
- 发布失败时 ChangeRequest 保持 `approved`，供重新校验后重试。
- Manifest 替换成功后，它立即成为权威；SQLite 失败不能把旧镜像写回 Manifest。
- `ManifestDurabilityUncertainError` 表示替换可能已完成。捕获后必须重新读取 Manifest 并判断旧/新版本，再按权威状态修复；不能直接删除候选目录或宣称旧版仍生效。
- 如果最终目录已提交但 Manifest 未引用它，应移入隔离区，不应静默删除审计证据。

### Step 6：SQLite 发布 UoW 和恢复 RED/GREEN

建议新增一个明确的 Release UoW，在同一连接/事务内完成：

1. 当前 Baseline 标记为 `superseded`；
2. 新 Baseline 插入为 `effective`；
3. ChangeRequest 从 `approved` 转为 `published`；
4. Project 的 `current_baseline_id` 更新；
5. `baseline_published` 事件插入 SQLite。

任何一步失败整笔 SQLite 事务回滚，然后调用 reconciliation；不要依次调用会各自提交的现有仓储方法来假装原子事务。

恢复测试至少覆盖：

- 镜像失败、按 Manifest 修复成功：发布返回成功；
- 镜像失败、修复失败：返回 `RELEASE_MIRROR_REPAIR_REQUIRED`，ReleaseGuard 阻断后续发布；
- 重复修复幂等；
- Manifest 无效时不得用 SQLite 反向覆盖；
- 修复后 Project、Baseline、ChangeRequest 与 Manifest 一致；
- JSONL 追加失败不否定已经提交的 SQLite/Manifest，后续 `EventLogger.reconcile()` 可补齐。

### Step 7：启动对账和容器接线

`build_container()` 在 Manifest 存在且数据库迁移完成后，先执行：

```text
validate_manifest_mirror
→ 不一致则 rebuild_current_from_manifest
→ 修复失败则 release_guard.block
→ 再组装 dashboard/query/lint/review/publish 服务
```

修复必须以 Manifest 为源，幂等重建 Project 当前基线、Baseline 镜像和 ChangeRequest 发布状态。修复未完成时：

- 首页继续显示 Manifest 指向的当前版本及完整性错误横幅；
- 当前查询仍按 Manifest 只读运行；
- 发布页禁用批准/发布动作；
- 不影响查看已有问题和变更详情。

延续 T07 已确认的边界：恢复服务只能更新已有 Project 的当前基线镜像，不得凭空合成 Project 名称、产品线或阶段；Project 行缺失时保持“读取失败 + 重试”，并阻断发布。

AppContainer 至少增加候选变更读取、复核和发布服务端口。页面不得直接构造 SQLite 仓储或文件服务。

### Step 8：完成发布页

页面保持现有白底、蓝色金融科技风格，并复用 theme token 和现有组件。建议 38/62 左右布局；窄屏堆叠。

页面必须显示：

```text
候选变更列表
目标卡片
修改前 / 修改后
变更依据
影响对象
正式应批准角色
当前演示确认人
目标版本
人工批准状态
发布说明
```

交互要求：

- 默认候选列表包含 `pending_approval` 和 `needs_info`；发布失败遗留的 `approved` 必须置顶供发布重试。`needs_info` 只读，不得绕过状态机再次批准。
- 四个操作：批准并发布、驳回、暂缓、退回补充。
- “批准并发布”是唯一主按钮；其余为次级操作。
- 发布前必须勾选“我已检查修改前后、依据和影响”，填写 20—200 字发布说明并通过预检。
- 必须出现人工确认弹窗，确认文案使用设计文档原文。
- pending 状态：先 `ReviewChangeRequest(approve)`，成功后再 `PublishBaseline`。
- approved 状态：发布重试只调用 `PublishBaseline`，不得重复写 Review。
- 成功页显示新版本、父版本、发布人、发布时间、变更单、差异摘要，并提供“查看新基线”和“查看完整追溯”入口。
- 失败页显示红色持续横幅、失败步骤、错误码、“原版本仍然生效”或 Manifest 已生效但镜像待修复的准确状态，以及“重新校验”。
- UI 只显示 `user_message` 和错误编号；详细异常写本地日志。

### Step 9：测试、浏览器验收和报告

Focused：

```bash
.venv/bin/python -m pytest \
  tests/integration/use_cases/test_review_change_request.py \
  tests/integration/use_cases/test_publish_baseline.py \
  tests/integration/recovery/test_reconciliation.py \
  tests/e2e/test_release_flow.py -v
```

关联回归：

```bash
.venv/bin/python -m pytest \
  tests/unit/domain/test_release_policy.py \
  tests/unit/domain/test_state_transition.py \
  tests/integration/files/test_manifest_store.py \
  tests/integration/db/test_repositories.py \
  tests/integration/use_cases/test_record_decision.py \
  tests/e2e/test_home_page.py \
  tests/e2e/test_lint_page.py \
  tests/e2e/test_query_flow.py -v
```

全量和质量门：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest --cov=src/domain --cov=src/application --cov-report=term --cov-fail-under=90 -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/python -m compileall -q src scripts streamlit_app.py
git diff --check
```

真实浏览器至少验证 1440×1024 和 390×844：

- 无横向溢出、遮挡或文本截断；
- 桌面双列、窄屏单列；
- 仅一个主按钮；
- Diff、批准角色和演示确认人首屏可理解；
- 人工确认弹窗完整；
- 成功后首页立即显示新版本和父版本；
- 发布前文件失败仍显示旧版；
- 镜像修复失败时发布动作禁用；
- 浏览器控制台无 error/warn。

浏览器 QA 必须使用隔离临时项目根目录和真实 T10 服务，不得修改仓库中的正式 `data/`。

## 7. 失败语义对照表

| 失败点 | 必须结果 |
|---|---|
| 锁获取失败 | `RELEASE_LOCKED`，不创建临时目录 |
| 变更/影响/完整性校验失败 | 不创建临时目录，Manifest 不变 |
| 文件或候选 Manifest 生成失败 | 清理临时目录，Manifest 不变 |
| 最终目录提交失败 | Manifest 不变 |
| Manifest 替换失败且确认未替换 | 旧 Manifest 生效，未引用目录隔离 |
| Manifest durability uncertain | 重新读取 Manifest 判定权威状态，禁止凭异常类型猜测 |
| SQLite 镜像失败、修复成功 | 新 Manifest 生效，镜像修复后返回成功 |
| SQLite 镜像失败、修复失败 | 新 Manifest 仍为权威，返回 `RELEASE_MIRROR_REPAIR_REQUIRED` 并阻断再次发布 |
| JSONL 审计追加失败 | SQLite 事件保留，标记审计待 reconcile，不回滚 Manifest |
| 发布失败后的再次点击 | 复用 approved 状态，只重试 Publish，不重复 Review |

需要在 `ErrorCode`/`ERROR_CATALOG` 中补齐稳定、用户可理解的缺口，例如 `CHANGE_NOT_REVIEWABLE`、复核幂等冲突和 `RELEASE_MIRROR_REPAIR_REQUIRED`。不要向 UI 泄漏原始 `OSError`、`sqlite3.Error` 或绝对路径。

## 8. 如何融入当前项目

1. 从 `30a2332` 建分支，先保留 471 项基线测试通过。
2. 领域层只补错误码或确有必要的状态约束；复用 `ReleasePolicy` 和 `state_transition`，不要复制规则。
3. 应用层通过 DTO、端口和 use case 编排，不直接依赖 Streamlit。
4. 基础设施层实现文件、锁、SQLite UoW、恢复和日志适配器。
5. 在 `build_container()` 完成启动对账并把 service 注入 `AppContainer`。
6. release 页面只依赖 AppContainer 端口；保持现有六页导航顺序不变。
7. 发布成功后，Home 继续通过 Manifest 展示当前版本，Query 默认读取新 Manifest，历史查询通过 superseded Baseline 读取旧版，Lint 默认使用新基线。
8. 完成 focused、关联回归、全量、覆盖率、静态检查和真实浏览器验收。
9. 把实现/审查/测试/浏览器证据写入 `task-10-implementer-report.md`，并更新 `progress.md`。
10. 提交建议：`feat: add atomic governed baseline release`。提交后报告 commit hash、文件列表、测试结果和残余风险。

若 T10 在独立分支完成，优先将其以 fast-forward 或普通 merge 融入 `feat/lightweight-t01`；若主线期间产生了新提交，先 rebase 到最新主线、运行全量回归，再合并。不要通过复制文件或只 cherry-pick 部分实现绕过测试和迁移接线。

## 9. 完成定义

只有同时满足以下条件，T10 才能声明完成：

- 四种人工复核均可审计、幂等且受状态机约束；
- 未批准绝对不能发布；
- 发布成功后新 Manifest 生效、父版本可见、旧版可历史查询；
- Manifest 替换前任何失败都保持旧版；
- SQLite 镜像异常可按 Manifest 自动修复，修复失败会禁用发布；
- 失败重试不会重复审批或产生重复版本；
- 发布页满足设计和桌面/窄屏验收；
- 全量测试和质量门通过；
- 所有产物位于本文件规定目录，报告和 commit hash 已回填；
- 未进入 T11。

完成后停止，向项目负责人简要汇报“做了什么、验证结果、残余风险、下一步 T11 是什么”，等待确认，不要自行继续下一节点。
