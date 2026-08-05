# T10、T11 当前版本修改建议 v2

## 1. 文档定位

本文档基于 2026-08-04 当前工作区重新制定，替代“从完成态反推整改”的处理方式，直接从现有半成品继续推进。

| 项目 | 当前事实 |
|---|---|
| 当前分支 | `codex/t10-remediation` |
| HEAD | `3b20512 feat: add atomic governed baseline release` |
| T10 状态 | 整改代码未提交，11 个已跟踪文件修改、2 个新增代码文件 |
| T11 状态 | WIP 保存在 `stash@{0}: t11-wip-before-remediation`，尚未合入当前分支 |
| T10 专项测试 | `30 passed, 28 failed` |
| 全量测试 | `464 passed, 65 failed` |
| 静态检查 | Ruff `F821` 1 个；format、compileall、diff check 通过 |
| 交付约束 | 8 月 30 日正式演示版；9 月实时演示；保留轻量成本联动；不做损益测算 |

当前版本不满足 T10、T11 验收，也不能进入 T12。后续必须先收口 T10，再恢复和重构 T11，不能并行修改共享文件。

## 2. 总体推进路线

```mermaid
flowchart LR
    A["T10-0 恢复测试可运行"] --> B["T10-1 完整发布快照"]
    B --> C["T10-2 新旧版本查询"]
    C --> D["T10-3 镜像与来源边界"]
    D --> E["T10-4 浏览器与提交"]
    E --> F["从 T10 完成提交建立 T11 分支"]
    F --> G["安全恢复 T11 stash"]
    G --> H["T11-1 Relation 审计链"]
    H --> I["T11-2 原文与证据"]
    I --> J["T11-3 轻量成本与页面"]
    J --> K["联合验收"]
    K --> L["T12 准入"]
```

### Git 处理规则

1. 当前 T10 工作区未干净前，不得应用 T11 stash。
2. 先完成并提交 T10 整改，建议提交信息：

```text
fix: complete governed release remediation
```

3. 从 T10 完成提交建立 `codex/t11-remediation`。
4. 使用 `git stash apply stash@{0}` 恢复 T11，不使用 `pop`；T11 完成验收前保留 stash 作为恢复点。
5. 以下四个文件会发生实质冲突，必须逐段合并，不允许整文件选择 ours/theirs：
   - `src/application/container.py`
   - `src/application/ports/repositories.py`
   - `src/domain/models.py`
   - `src/infrastructure/db/repositories.py`
6. T10 和 T11 必须形成两个独立提交，T12 不得夹入其中。

## 3. T10 当前版本修改建议

### T10-0：先恢复测试和静态检查

当前首要任务不是继续扩展功能，而是让已有测试重新具备执行能力。

#### 必须修改

1. 在 `src/application/use_cases/publish_baseline.py` 导入 `ChangeRequest`，关闭 Ruff `F821`。
2. 更新 `tests/integration/use_cases/test_publish_baseline.py` 的 `_use_case()`：
   - 注入 `sources=env.sources`；
   - 注入真实 `SqliteIssueRepository`；
   - 不用 Mock 绕过新增来源校验。
3. 更新 `tests/integration/recovery/test_reconciliation.py` 的 MarkdownStore 调用：
   - 传入 `parent_version=current.current_version`；
   - 接收 `build_release_cards()` 返回的完整卡片集合。
4. 更新所有 `RunQuery(...)` 构造位置，注入 `BaselineCardReader`：
   - 单元测试使用严格内存 fake，按 project/version/path/hash 返回对应卡片；
   - 集成测试使用 `LocalBaselineCardReader`；
   - Golden 测试不能退回 SQLite KnowledgeRepository 读取版本快照。
5. 更新所有 `ReleaseUnitOfWork.publish()` fake/Mock 签名，包含 `new_cards` 和 `relations`。
6. 旧测试断言必须同步为“新版本是完整快照”，不得继续断言未修改卡停留在父版本。

#### 节点通过条件

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/python -m pytest -q
```

期望：静态检查通过、全量测试恢复为全绿。只有恢复全绿后才能继续新增 T10-A 验收测试，避免在破损基线上叠加功能。

### T10-1：完成发布快照和历史资产元数据

当前 `MarkdownStore` 已开始实现以下正确方向，应保留：

- 更新 `full.md` 的唯一版本声明；
- 校验父快照不能混合版本；
- 新快照所有卡片统一目标版本；
- `release.json` 增加 `card_count`；
- 新 Baseline 保存 full/cards SHA-256。

仍需补齐以下问题。

#### 1. 保证父 Baseline 在被 supersede 前具有资产哈希

现有迁移只增加可空字段。对升级前已存在的当前 Baseline，两个字段仍可能为 NULL；发布后它成为历史版本，历史查询会返回 `HISTORICAL_ASSET_UNVERIFIABLE`。

指定做法：

1. `ReleaseUnitOfWork.publish()` 增加父版本两个哈希参数；
2. 在同一发布镜像事务内、将父 Baseline 置为 `superseded` 前，把当前 Manifest 的：
   - `full_document_sha256`；
   - `card_snapshot_sha256`
   写入父 Baseline；
3. `_mirror_matches()` 必须比较当前 Baseline 两个哈希与 Manifest 是否一致；
4. `rebuild_current_from_manifest()` 必须为当前 Baseline 回填两个哈希；
5. `bootstrap_demo.py` 遇到已有项目时不能只验证旧行，应通过 reconciliation 或窄更新方法补齐当前哈希。

对于更早的历史行：

- 只有 canonical 版本目录和 `release.json` 三个内容哈希均验证通过时才允许回填；
- 无法验证的旧历史版本继续 fail closed，不允许现场演示选择它；
- 8 月 30 日正式快照至少必须保证主故事父版本 `LLD-724_1` 可验证。

#### 2. 收紧卡片快照路径

`LocalBaselineCardReader` 不能只检查“位于 project_root”。必须精确要求：

```text
data/obsidian_vault/02_Current_Baseline/{version}/cards.json
```

同时检查：

- `version` 不能包含路径穿越；
- 文件名必须为 `cards.json`；
- 解析后的父目录必须恰好是对应版本目录；
- SHA-256、JSON 结构、卡片 ID 唯一、project_id 和 product_version 全部匹配。

#### 3. Reconciliation 复用同一个受控读取器

当前 `_assets_match()` 与 `_read_manifest_cards()` 分两次读取文件，并且后者不校验重复 ID、项目和版本。应改为：

1. 单次读取 bytes；
2. 同一份 bytes 完成 SHA-256 和 JSON 解析；
3. 复用 `LocalBaselineCardReader` 的校验逻辑或提取共享的纯校验函数；
4. 不允许先验哈希后再次读取不同内容；
5. 重建前验证所有 effective 卡均属于 Manifest 项目和版本。

#### 对应验收

- T10-A01～A05：完整快照和版本声明；
- T10-A07～A09：历史快照可验证且不串版本；
- T10-A10～A14：SQLite 当前镜像和恢复幂等。

### T10-2：完成发布后当前查询和历史查询

当前引入 `BaselineCardReader` 的方向正确，但必须用真实发布流程证明，而不是只验证 reader 本身。

#### 必须新增的集成测试

新建建议文件：

```text
tests/integration/use_cases/test_publish_then_query.py
```

同一个测试环境依次执行：

```text
RunQuery(current LLD-724_1)
-> ReviewChangeRequest(approve)
-> PublishBaseline(LLD-724_2)
-> RunQuery(current)
-> RunQuery(historical LLD-724_1)
-> RunQuery(current)
```

断言：

1. 发布前当前查询返回 `_1` 的旧规则；
2. 发布后当前查询返回 `_2` 的新规则和全部未变化卡；
3. 历史查询返回 `_1` 的旧规则；
4. 再切回当前仍返回 `_2`；
5. 每次引用、source version、baseline version 均与所选版本一致；
6. 篡改任一版本的 `cards.json` 后，不调用模型并返回完整性错误。

#### 来源版本继承规则

当前 `_eligible_source_versions()` 允许目标版本沿父链继承来源，这是合理方向，但需增加：

- 父链循环测试；
- 父 Baseline 缺失时 fail closed，而不是静默接受部分链；
- 同版本重复 Baseline 时 fail closed；
- 只能继承同项目父链；
- historical 查询不得继承其未来子版本的来源。

### T10-3：收紧正式来源和发布事务

当前 `PublishBaseline` 已增加 SourceRepository/IssueRepository，应保留，但还需关闭以下边界。

#### 1. source_ref 必须严格解析

当前代码遇到空 source ID 会跳过。应改为：

- 支持 `SOURCE_ID` 或 `SOURCE_ID:CITATION_ID`；
- source ID 为空、额外分隔格式非法、引用全为空时返回 `CITATION_INVALID`；
- 每张 effective 卡至少有一个成功解析且合格的正式来源；
- 不允许用“跳过非法引用后还剩一个合法引用”掩盖悬空引用，所有引用都必须有效。

#### 2. IssueEvidence 必须一一对应

- ChangeRequest 的每个 `evidence_ref` 必须在所属 Issue 中恰好匹配一个 IssueEvidence；
- 重复 citation ID、跨项目来源、未导入完成、沙箱或非正式权威均 fail closed；
- 校验发生在创建临时目录之前；
- 失败时 ChangeRequest 保持 `approved`，旧 Manifest 和 SQLite 不变。

#### 3. 发布 UoW 数据归属校验

`new_cards` 和 `relations` 进入 SQL 前必须再次校验：

- 全部属于 `project_id`；
- 卡片版本等于新 Baseline 版本；
- Relation 两端 ID 与本次发布上下文一致；
- 不使用 `INSERT OR IGNORE` 静默吞掉同 ID 不同内容的关系冲突；
- 同一稳定 Relation 重试允许幂等，不同事实冲突必须失败。

#### 对应验收

- T10-A15～A19 全部落为真实仓储集成测试；
- 额外增加非法空 source ID、重复 citation、Relation 幂等冲突测试。

### T10-4：测试、报告、浏览器和提交

T10 只有同时满足以下条件才可提交：

- [ ] T10-A01～A19 均有可执行测试；
- [ ] T10 专项测试全绿；
- [ ] 全量测试全绿；
- [ ] domain + application 覆盖率不低于 90%；
- [ ] Ruff、format、compileall、diff check 全部通过；
- [ ] 1440x1024 发布成功、失败、重试流程真实浏览器通过；
- [ ] 390x844 无横向溢出，确认弹窗和按钮可操作；
- [ ] `task-10-implementer-report.md` 更新本轮整改事实，不保留过期的“529 passed”作为当前结果；
- [ ] `progress.md` 追加 T10 remediation 轮次、提交和验收证据；
- [ ] 工作区只含 T10 文件并形成独立提交。

## 4. T11 当前版本修改建议

### T11-0：恢复 WIP，但不直接接受原实现

T11 stash 包含完整页面、DTO、use case、市场证据、成本函数和测试雏形，可以复用；但原实现已知存在以下问题：

- 追溯边由实体字段和“最新记录”推导，不来自 `relations` 表；
- Source 节点不能定位原文；
- 任意 source_refs 会被判断为市场证据充分；
- 任意 validation_note 会被当作验证计划；
- 任意卡片可作为成本来源；
- 沙箱属性由独立复选框决定。

恢复后先保留可用 UI/DTO 骨架，再重写数据来源和校验。旧测试只能作为回归参考，不能作为最终验收标准。

### T11-1：合并冲突时必须保留的 T10 能力

应用 stash 后，四个共享文件必须同时保留以下能力：

| 文件 | 必须保留的 T10 内容 | 需要加入的 T11 内容 |
|---|---|---|
| `container.py` | Source/Issue 发布依赖、BaselineCardReader、reconciliation | RelationRepository、BuildTrace、证据读取器 |
| `repositories.py` | 扩展后的 ReleaseUnitOfWork | RelationRepository、必要的 trace 查询端口 |
| `models.py` | Baseline 资产哈希字段 | Trace DTO、市场证据、成本结果、`resolved_by` Relation 类型 |
| `db/repositories.py` | 发布时卡片镜像和发布 Relation | 关系图读取、Ingest/Lint/Decision 生命周期 Relation 写入 |

合并后第一步先运行全量测试，确保 T10 没有因 T11 恢复而回退。

### T11-2：Relation 必须成为追溯唯一事实来源

#### 新增 RelationRepository

```python
class RelationRepository(Protocol):
    def load_connected(
        self,
        project_id: str,
        entity_id: str,
        *,
        max_depth: int = 6,
    ) -> list[Relation]: ...
```

SQLite 实现要求：

- recursive CTE；
- project_id 强隔离；
- 深度最多 6；
- 循环去重；
- 稳定排序；
- 同一边重复写入幂等，同 ID 不同事实冲突。

#### 补齐生命周期 Relation

| 阶段 | 关系 | 与什么同事务 |
|---|---|---|
| Ingest | Source -> Knowledge：`derived_from` | Source、Card、Issue |
| Lint | Knowledge -> Issue：真实冲突/影响关系 | Issue upsert |
| Decision | Issue -> Decision：`resolved_by` | Decision、Issue 状态 |
| Decision | Decision -> Change：`proposes_change_to` | ChangeRequest |
| Release | Change -> Baseline：`approved_as` | 发布镜像 |
| Release | New Baseline -> Parent：`supersedes` | 发布镜像 |

`BuildTrace` 只从上述图选择六节点主链。实体字段可用于校验 Relation 两端，不能用于在 Relation 缺失时自动补边。

### T11-3：入口卡片和原文追溯

1. 当前追溯入口卡片从 Manifest 指向的 `cards.json` 读取，复用 T10 的 `BaselineCardReader`；不得再次依赖可能滞后的 SQLite 版本筛选。
2. Source/Knowledge 节点引用使用 `LocalQueryMaterialReader` 验证：
   - archive 路径；
   - SHA-256 和文件大小；
   - citation/chunk ID；
   - locator 和脱敏 excerpt。
3. 详情只展示最小必要片段，不显示完整 L2 原文、本机绝对路径或密钥。
4. 发布页进入追溯时传递 `target_card_id`，页面自动定位本次变更。
5. 文件篡改、chunk 缺失时显示“引用不可验证”，不回退到其他文本。

### T11-4：市场证据分类

`classify_market_claim()` 的输入改为已解析证据，不再把字符串 ID 当作充分证据。

只有同时满足以下条件才能显示“有证据支持”：

- SourceRecord 存在且同项目；
- archive 哈希和 citation 定位通过；
- 来源不是沙箱；
- source_type 属于明确的客户/市场验证材料类型；
- excerpt 对判断有直接支持。

验证计划只允许来自同一目标卡的 `MKT-001` Issue，并要求结构化 `validation_note`。其他 Issue 的审计说明不能复用。

### T11-5：轻量成本联动

本期采用已确认的轻量沙箱方案：

1. 保留 Decimal、金额到分、旧成本、新成本、差额和固定免责声明；
2. 不输出收入、利润、ROI、回收期和损益；
3. 删除独立沙箱复选框；
4. 仅允许选择 `is_sandbox=True` 且 source_type 为成本参数/演示测算参数的 SourceRecord；
5. 沙箱标识由服务端从 SourceRecord 自动生成，前端不能取消；
6. 普通产品卡、任意正式文件或只提供字符串 ID 均不能作为成本来源；
7. 正式参数模式留到存在结构化参数记录和 citation 绑定后再开放。

### T11-6：T11 验收和提交

- [ ] T11-A01～A24 全部实现并通过；
- [ ] 用真实 Ingest -> Lint -> Decision -> Publish 生成六节点关系链；
- [ ] 删除一条 Relation 后页面明确缺环且不自动补边；
- [ ] 可定位验证过的原文片段；
- [ ] 无证据市场判断不会被写成事实；
- [ ] 成本结果自动标记模拟且无损益结论；
- [ ] 1440x1024 六节点一屏可读；
- [ ] 390x844 按顺序纵向堆叠，无溢出；
- [ ] 全量回归和静态检查通过；
- [ ] 新建 `task-11-implementer-report.md`；
- [ ] `progress.md` 记录实现、复核、浏览器证据和提交；
- [ ] 独立 reviewer 确认无 Critical/Important 未关闭问题；
- [ ] 形成独立提交，建议提交信息：`feat: add persisted traceability and governed value hints`。

## 5. 联合验收顺序

T10、T11 都完成后，在一个全新临时工程根目录连续执行：

```text
初始化 LLD-724_1
-> 导入两份正式材料和一份明确沙箱成本材料
-> 当前查询
-> Lint
-> 人工决定并生成变更单
-> 人工批准
-> 发布 LLD-724_2
-> 当前查询验证新规则
-> 历史查询验证旧规则
-> 追溯页验证六节点 Relation 链
-> 展开来源验证 locator 和 excerpt
-> 市场证据不足提示
-> 沙箱轻量成本联动
-> 重启应用验证 Manifest/SQLite 对账
```

联合验收必须使用真实文件存储、真实 SQLite 仓储和真实 Use Case，不得直接插入“已发布”“已追溯”的中间状态。

## 6. T12 准入门槛

只有以下条件全部满足，才能开始冻结 T12 initial/frozen 快照：

- [ ] T10 整改已提交且工作区干净；
- [ ] T11 已从 T10 完成提交继续开发并独立提交；
- [ ] T10-A01～A19、T11-A01～A24 全部通过；
- [ ] 全量测试、覆盖率和静态检查全部通过；
- [ ] 当前/历史查询均通过真实发布后的联合测试；
- [ ] 正式/沙箱边界不可从 UI 或服务端绕过；
- [ ] 六节点链完全来自持久化 Relation；
- [ ] 原文引用可验证且不泄露完整敏感内容；
- [ ] 轻量成本联动仅使用明确沙箱参数且无损益输出；
- [ ] 桌面和移动真实浏览器证据齐全；
- [ ] T10/T11 实施报告、进度台账和独立 review 完整。

未满足时，T12 只能阅读和设计脚本，不能生成正式演示快照或冻结缓存。

## 7. 建议执行批次

| 批次 | 内容 | 停点 |
|---|---|---|
| 批次 1 | T10-0：修复 65 个回归失败和 Ruff | 全量测试恢复全绿后确认 |
| 批次 2 | T10-1/T10-2：哈希、快照、新旧查询 | publish -> current -> historical 测试通过后确认 |
| 批次 3 | T10-3/T10-4：来源边界、浏览器、报告和提交 | T10 独立提交后确认 |
| 批次 4 | 建 T11 分支并安全应用 stash，解决共享文件冲突 | T10 全量回归仍全绿后确认 |
| 批次 5 | Relation 写入、图读取和原文定位 | 六节点真实链通过后确认 |
| 批次 6 | 市场证据、轻量成本、页面和浏览器 | T11-A01～A24 全部通过后确认 |
| 批次 7 | 联合验收和 T12 准入复核 | 出具准入结论 |

