# T12 独立审计、整改建议及 T13 准入标准

## 1. 文档信息

| 项目 | 内容 |
|---|---|
| 项目 | 产品智策轻量交付版，路线 A |
| 审计对象 | T12 演示数据、冻结缓存、快照和一键重置 |
| 准入目标 | T13 黄金测试、E2E、安全测试和设计验收 |
| 审计日期 | 2026-08-06 |
| 当前分支 | `feat/lightweight-t01` |
| 当前 HEAD | `5c80ee5 docs: refresh joint acceptance log after task 12 verification` |
| T12 实现提交 | `159f6ff feat: add deterministic demo snapshots and reset` |
| T12 报告提交 | `0fafbe1 docs: record task 12 demo snapshots and reset` |
| 计划依据 | `docs/superpowers/plans/2026-07-29-product-intelligence-lightweight.md` Task 12、Task 13 |
| 文档用途 | 交给开发 Agent 关闭 T12 缺口，并作为独立 reviewer 判断是否允许开始 T13 的唯一准入清单 |

本文档只授权整改 T12 及其继承的交付证据问题。除用于证明 T12 可供 T13 消费的窄测试外，不得提前创建 `tests/e2e/harness.py`、T13 正式 E2E、黄金指标报告或 `docs/qa/` 正式验收材料。

## 2. 独立审计结论

### 2.1 总体结论

**T12 当前终验不通过，暂不允许进入 T13。**

T12 已形成有价值的主体工件：四份演示材料、initial/frozen 快照、三类缓存记录、恢复/校验 CLI、22 项脚本集成测试以及独立提交边界。当前全量测试和质量门禁也保持全绿。

但独立反例确认仍存在：

1. 1 个 Critical：快照输出目录与项目根重叠时，导出工具会删除待快照项目自身。
2. 4 个 Important：快照不能独立恢复到干净目录；Query/Lint 冻结缓存没有运行时消费者，断网完整流程无法成立；恢复不是事务式且清单兼容字段未参与准入判断；复验收尾时快照载荷目录残留 4 个未跟踪 SQLite 旁文件，工作区不干净。
3. 1 个继承的 Process Important：T10/T11 的失败态浏览器截图仍未显示报告所声明的错误告警和错误码。

在以上问题关闭前，T13 无法安全地把 initial snapshot 作为每个 E2E 的独立 fixture，也无法把 frozen snapshot 作为断网完整流程的可信后备。

### 2.2 当前进度

| T12 计划步骤 | 当前状态 | 独立结论 |
|---|---|---|
| Step 1 重置测试 | 部分完成 | 已覆盖“先 bootstrap、再损坏、再恢复”，未覆盖从 initial snapshot 创建干净独立目录 |
| Step 2 快照清单 | 部分完成 | 字段和四类载荷哈希存在，但 `app_version`、`schema_version` 不参与兼容性拒绝，模型也未严格拒绝额外字段 |
| Step 3 安全重置 | 部分完成 | 能保护已有 `source_archive` 不被删除；但缺少干净目录种子、跨目标原子替换和失败回滚 |
| Step 4 三类冻结缓存 | 部分完成 | Ingest/Query/Lint 三条工件和元数据存在；只有 Ingest 能在运行时消费缓存 |
| Step 5 快照测试和 CLI | 部分完成 | `tests/integration/scripts` 22 项通过；计划指定的干净环境重置形状和断网全流程未被证明，复验后仓库载荷目录还残留 `-wal/-shm` |
| Step 6 独立提交 | 通过 | 实现、报告、日志提交边界清晰，未混入 T13 功能 |

### 2.3 已通过门禁

| 检查项 | 独立结果 |
|---|---|
| T12 脚本专项 | `22 passed` |
| 全量测试 | `690 passed` |
| domain + application 覆盖率 | `95%`，门槛 90% 通过 |
| Ruff check | 通过 |
| Ruff format check | 通过，151 个文件 |
| compileall | 通过 |
| initial/frozen 当前载荷哈希 | `verify_snapshot_payload` 通过 |
| 审计基线 | 审计开始时工作区干净；收尾状态另见 T12-P1-03，不满足最终交付门禁 |

通过现有测试只说明已覆盖场景没有回归，不能抵消下面已经独立复现的缺口。

## 3. 未关闭问题

### T12-P0-01：快照输出路径可删除待快照项目

**级别：Critical。阻断 T12 完成和 T13。**

#### 当前事实

`scripts/export_snapshot.py` 允许调用方提供任意 `--output`。`capture_snapshot()` 在完成输出路径与项目路径的关系校验之前，对已存在的 `snapshot_dir` 直接执行：

```python
if snapshot_dir.exists():
    shutil.rmtree(snapshot_dir)
```

没有禁止以下危险组合：

- `snapshot_dir == project_root`；
- `snapshot_dir` 是 `project_root` 的祖先；
- `snapshot_dir` 与数据库、Manifest、缓存、Vault 或 `source_archive` 重叠；
- 解析符号链接后与受保护目录重叠。

#### 独立反例

在一次性临时目录中执行 `capture_snapshot(root, root)`：

```text
outcome=OperationalError:unable to open database file
project_root_exists=True
database_exists=False
source_archive_exists=False
```

项目根被重建成了半成品快照目录，原数据库和正式来源归档已经被删除。

#### 影响

1. 一次错误的 CLI 参数即可删除演示数据和正式资料。
2. 导出失败时旧快照也会先被删除，无法保证上一份已验证快照继续可用。
3. 不满足“安全重置/确定性快照工具”的最低安全边界。

### T12-P1-01：initial/frozen 快照不能独立恢复到干净目录

**级别：Important。阻断 T13 E2E Harness。**

#### 当前事实

快照数据库包含：

```text
SRC-LLD-BASE -> data/source_archive/LLD/SRC-LLD-BASE/当前产品方案.md
```

但 initial/frozen payload 都不包含该归档。现有所有成功恢复测试先调用 `bootstrap(root)`，因此来源文件由 bootstrap 预先创建，而不是由 snapshot 提供。

T13 计划要求：每个 E2E 从 initial snapshot 创建独立临时数据目录。当前快照无法单独完成该动作。

#### 独立反例

在空的临时 `project_root` 中直接恢复 initial snapshot：

```text
ok=False
errors=['ARCHIVE_MISSING:SRC-LLD-BASE']
baseline=LLD-724_1
```

#### 影响

1. T13 若照计划创建独立 fixture，会在容器构建前失败。
2. 若 T13 继续先 bootstrap 再恢复，测试实际依赖 bootstrap 当前实现，不再只依赖已冻结 snapshot，存在漂移和自证。
3. `reset_demo.py` 无法在一个只有代码、配置和快照的干净交付目录中完成恢复。

### T12-P1-02：三类缓存只有 Ingest 可消费，断网完整流程不成立

**级别：Important。阻断 T12 核心验收证据和 T13。**

#### 当前事实

`freeze_demo_caches()` 确实写入 Ingest、Query、Lint 三类缓存，但：

- `ImportSource` 注入并调用 `AiCache`，支持 `preferred_mode="cache"`；
- `RunQuery` 没有缓存依赖或缓存模式；
- `RunLint` 没有缓存依赖或缓存模式；
- container 只向 ImportSource 注入 `AiCache`；
- `RunLint` 把结果模式固定为 `REALTIME`。

实施报告第 16 行也明确承认 Query/Lint 缓存没有运行时消费者，但第 38 行又以“仅离线导入成功”作为“无网络走完整流程”的证据。两者不等价。

#### 独立反例

恢复 frozen snapshot、禁止所有 HTTP 请求后执行 Query：

```text
query=NETWORK_FORBIDDEN:https://dify.offline.local/workflows/run
```

Lint 同样没有缓存读取分支；当前反例在调用外部网关之前还会命中 outbound safety 校验，并不会读取已冻结的 Lint 缓存。

#### 影响

1. 无网络时只能完成缓存导入，不能完成 Query + Lint + 人工决定 + 发布主故事。
2. Query/Lint 缓存目前只是仓库工件，不是产品可用能力。
3. 不满足 T12 原文“无网络时可用完全匹配缓存走完整流程”。

### T12-P1-03：恢复非事务式，且版本兼容字段未生效

**级别：Important。阻断 T13 三次连续重置门禁。**

#### 当前事实

`restore_snapshot()` 按数据库、Manifest、缓存、Vault 顺序逐个删除正式目标并复制新内容：

```python
for relative in SNAPSHOT_TARGETS:
    delete(target)
    copy(source, target)
```

中途复制失败、数据库路径改写失败或最终 `validate_data` 失败时，没有备份回滚。最终校验失败只返回 `ok=False`，已经覆盖的目标不会恢复。

同时 `SnapshotManifest.app_version` 和 `schema_version` 只被解析和展示，不与当前应用版本、支持的 snapshot schema 比较。独立修改清单为以下值后，`verify_snapshot_payload()` 仍接受：

```text
accepted_app_version=999.0.0
accepted_schema_version=999.0
```

复验收尾时，Git 工作区还出现以下未跟踪文件：

```text
data/demo_snapshots/frozen/payload/data/local_state/product_intelligence.db-shm
data/demo_snapshots/frozen/payload/data/local_state/product_intelligence.db-wal
data/demo_snapshots/initial/payload/data/local_state/product_intelligence.db-shm
data/demo_snapshots/initial/payload/data/local_state/product_intelligence.db-wal
```

检查时没有进程继续占用这些文件；在临时副本中正常打开、查询并关闭同一数据库又没有稳定复现残留。因此暂不把根因归到单一函数，但当前交付状态已明确违反“仓库快照不可被测试污染”和“工作区干净”门禁。不得仅用 `.gitignore` 隐藏，必须让所有验证在临时副本上执行，并以全量测试后无旁文件为证据。

#### 影响

1. 重置失败可能把原本可运行的环境变成数据库/Manifest/Vault 混合状态。
2. T13 连续三次流程一旦某轮重置失败，后续结果不可再作为独立证据。
3. 旧版或未知 schema 快照可进入恢复流程，兼容字段形同虚设。
4. 测试或校验若直接打开 Git 内快照数据库，会污染权威 fixture 并使工作区无法作为可重复交付证据。

### T12-P1-04：继承的失败态浏览器证据仍不自洽

**级别：Process Important。阻断“无未关闭 Important”准入条件。**

`browser-acceptance.md` 声明 `release-failure-mobile-390x844.png` 展示 `PUBLISH_SOURCE_INTEGRITY_FAILED`，但当前提交图片只显示到“发布操作”标题，没有失败告警、错误码或安全重试提示。

T12 虽已由项目负责人指令继续实施，但进入 T13 前仍应关闭该继承门禁，否则 T13 的 UI/发布失败验收将建立在不完整证据上。

## 4. 修改方案

### M1：先关闭快照导出的破坏性路径

#### 修改文件

- `scripts/snapshot_common.py`
- `scripts/export_snapshot.py`
- `tests/integration/scripts/test_reset_demo.py`

#### 指定修改

1. 在任何删除、创建或复制之前，解析 `project_root`、`snapshot_dir` 及所有受保护目录的真实路径。
2. 至少拒绝：输出等于/包含项目根；输出与四个 snapshot target 或 `source_archive` 任意方向重叠；输出通过符号链接解析后重叠。
3. 稳定错误码使用：

```text
SNAPSHOT_OUTPUT_OVERLAP:<resolved_path>
SNAPSHOT_OUTPUT_UNSAFE:<resolved_path>
```

4. 不得直接删除上一份正式 snapshot。先在同文件系统 sibling staging 目录完成复制、路径规范化、清单生成和载荷复验，再原子替换目标目录。
5. 新 snapshot 构建失败时，旧 snapshot 必须逐字节保持不变，staging 可安全清理。

### M2：让 initial/frozen snapshot 能创建干净独立 fixture

#### 修改文件

- `scripts/snapshot_common.py`
- `scripts/reset_demo.py`
- `data/demo_snapshots/initial/`
- `data/demo_snapshots/frozen/`
- `tests/integration/scripts/test_reset_demo.py`
- `tests/integration/scripts/test_validate_data.py`

#### 指定修改

1. 在 snapshot 中加入当前数据库所引用的最小来源归档种子和确定性索引。推荐在 manifest 新增：

```text
source_archive_index_sha256
```

2. 干净目录恢复时，只补齐 snapshot 数据库明确引用且 payload 哈希匹配的来源文件。
3. 已存在目录恢复时：
   - 不删除额外来源文件；
   - 已有同路径同哈希文件直接复用；
   - 已有同路径不同哈希文件必须在覆盖四目标之前 fail closed；
   - 不允许静默覆盖正式来源。
4. 恢复后数据库归档绝对路径必须指向目标 root，且 `validate_data` 全部检查通过。
5. 不得用 `bootstrap(root)` 作为“snapshot 可恢复”的前置条件；bootstrap 可用于生成 snapshot，但不能用于 T13 每次测试的恢复夹具。

### M3：实现真正可消费的 Query/Lint 精确缓存

#### 修改文件

- `src/application/dto/query.py`
- `src/application/dto/lint.py`
- `src/application/use_cases/run_query.py`
- `src/application/use_cases/run_lint.py`
- `src/application/container.py`
- `src/infrastructure/cache/ai_cache.py`
- 必要时修改 `src/infrastructure/gateways/query_gateway.py`、`lint_gateway.py`，提取实时/缓存共用的输出验证器
- 必要时新增 cache identity 数据迁移
- `tests/integration/scripts/test_reset_demo.py`
- Query/Lint 对应集成测试

#### 指定修改

1. Query/Lint 必须支持显式 `realtime`/`cache` 模式；不得在用户未确认时把任意实时错误自动降级为缓存。
2. 缓存身份必须使用真实运行时输入重建，至少包含计划要求的五类元数据；Query 还必须绑定规范化 question。
3. 不同 source SHA、baseline、prompt、model、schema 或 question 任一不同时，必须 cache miss，不得近似匹配。
4. 缓存输出必须经过与实时输出相同的 schema、citation、版本、目标规则和安全边界校验；不得因为来自本地文件而绕过验证。
5. Query 缓存返回必须正确标记 `result_mode=CACHE`；LintReport 同样标记 CACHE，二者 `model_call_id=None`，并提供缓存生成时间。
6. 缓存路径不得创建或伪造 ModelCallLog 的 started 记录。
7. frozen snapshot 必须重新生成，使三类缓存与最终运行时代码的 prompt/model/schema/identity 完全一致。

### M4：把 reset 做成失败可回滚的受控操作

#### 修改文件

- `scripts/snapshot_common.py`
- `scripts/reset_demo.py`
- `tests/integration/scripts/test_reset_demo.py`

#### 指定修改

1. 增加项目级 reset lock，避免应用运行时同时写数据库、Manifest 或 Vault。
2. 在同文件系统 staging 中准备全部候选目标，先完成哈希、数据库路径改写、来源归档预检和兼容性检查。
3. 替换正式目标前保存可恢复 backup；任一替换或最终校验失败必须回滚四个目标。
4. 成功后再删除 backup；失败日志必须保留稳定错误码，且不得用清理异常覆盖原始原因。
5. `SnapshotManifest` 使用 `extra="forbid"`、冻结模型、严格 SHA-256 字段和 UTC 时间。
6. 在写目标前拒绝：

```text
SNAPSHOT_APP_VERSION_MISMATCH
SNAPSHOT_SCHEMA_VERSION_UNSUPPORTED
SNAPSHOT_MANIFEST_INVALID
```

7. `app_version()` 读取失败必须 fail closed，不得静默回退字面量版本。
8. 测试和校验不得以读写方式直接打开 Git 内 snapshot 数据库；必须先复制到独立临时 root。全量测试结束后，两个 payload 目录都不得出现 `-wal`、`-shm` 或临时文件，不能用忽略规则规避。

### M5：修正文档与继承证据

#### 修改文件

- `.superpowers/sdd/2026-07-29-product-intelligence-lightweight/task-12-implementer-report.md`
- `.superpowers/sdd/2026-07-29-product-intelligence-lightweight/progress.md`
- T10/T11 浏览器失败截图及对应报告

#### 指定修改

1. 在完整离线流程真正通过前，撤回“离线 ingest 等于离线完整流程”的表述。
2. 补拍失败态移动截图，画面中必须同时可见业务失败告警和 `PUBLISH_SOURCE_INTEGRITY_FAILED`；更新浏览器报告但保持所验代码 SHA 如实可追溯。
3. 记录 T12 整改提交 SHA、snapshot manifest 哈希、完整离线流程输出、连续重置结果和已知限制。
4. T12 整改提交不得混入 T13 Harness、T13 正式 E2E 或 `docs/qa` 结果。

## 5. 强制负向验收用例

| 用例 ID | 场景 | 通过条件 |
|---|---|---|
| T12-R01 | `snapshot_dir == project_root` | 在任何写入前以 `SNAPSHOT_OUTPUT_OVERLAP` 拒绝，项目根全部文件逐字节不变 |
| T12-R02 | snapshot 输出为项目根祖先、四目标内部或 source_archive 内部 | 全部拒绝，受保护目录不变 |
| T12-R03 | 新 snapshot 构建中注入 copy/normalize/manifest 写入失败 | 旧 snapshot 完整保留，不出现半成品正式目录 |
| T12-R04 | initial snapshot 恢复到空临时 root | `VALIDATION_OK baseline=LLD-724_1`，base archive 存在且 SHA/size 正确 |
| T12-R05 | frozen snapshot 恢复到空临时 root | 校验通过，三类缓存和来源种子齐全 |
| T12-R06 | 目标 root 有额外正式来源和标记文件 | 重置后额外文件逐字节保留 |
| T12-R07 | 目标 root 同路径来源与 snapshot 哈希不一致 | 覆盖四目标前拒绝；原数据库、Manifest、缓存、Vault、来源均不变 |
| T12-R08 | reset 在第 1～4 个目标替换、路径改写、最终 validate 各阶段失败 | 每个场景都完整回滚，旧环境仍可 `validate_data` |
| T12-R09 | app version/schema version 伪造或 manifest 有额外字段 | 恢复前拒绝，不产生目标变更 |
| T12-R10 | Query/Lint 使用完全匹配 frozen cache，HTTP factory 禁止任何请求 | 两者成功返回 CACHE，不发生网络请求，不产生 ModelCallLog started |
| T12-R11 | Query question、source SHA、baseline、prompt、model、schema 分别改变 | 每项均 cache miss，不能跨身份复用 |
| T12-R12 | 缓存 payload citation、版本、locator 或目标规则伪造 | 走与实时相同验证并 fail closed，不写 Issue/Relation/Decision |
| T12-R13 | frozen 断网主流程 | cache ingest → cache query → cache lint → 决定 → 批准 → 发布全部成功，Manifest 到 `LLD-724_2` |
| T12-R14 | 完整演示后 reset initial，连续三次 | 每次 reset 后都是 `LLD-724_1`，数据库/Vault/Manifest/cache 哈希一致，无残留 WAL/临时目录 |
| T12-R15 | 浏览器失败证据 | 提交图片可直接读到失败告警、稳定错误码和安全重试提示 |

所有失败用例必须额外断言：

- 不删除或覆盖正式来源归档；
- 不留下 `-wal`、`-shm`、staging、backup 半成品；
- 不用异常清理覆盖原始稳定错误；
- Git 仓库自身的本地 data 不被测试污染。

## 6. T13 准入通过标准

只有以下项目全部满足，独立 reviewer 才能签认进入 T13：

- [ ] T12-P0-01 已关闭，所有 snapshot 输出重叠路径在写入前拒绝；
- [ ] initial/frozen 均可从干净临时 root 独立恢复，不依赖预先 bootstrap；
- [ ] reset 保留现有正式来源，来源冲突时在覆盖目标前 fail closed；
- [ ] capture 和 restore 均具备 staging、受控替换和失败回滚；
- [ ] snapshot app/schema 兼容性、严格字段和全部载荷哈希参与恢复闸；
- [ ] Ingest、Query、Lint 三类 frozen cache 均有真实运行时消费者；
- [ ] 完全匹配缓存才可使用，六类身份字段不串用；
- [ ] 网络被完全禁止时，缓存主流程可继续执行人工决定和本地发布；
- [ ] T12-R01～T12-R15 全部通过，无 skip/xfail 规避；
- [ ] `tests/integration/scripts`、Query/Lint/缓存关联测试和全量测试全部通过；
- [ ] domain + application 覆盖率不低于 90%；
- [ ] Ruff、format、compileall、`git diff --check` 全部通过；
- [ ] initial/frozen 重新生成并进入 Git，manifest 哈希与报告一致；
- [ ] T10/T11 失败态浏览器证据已补齐，继承的 Process Important 关闭；
- [ ] T12 实施报告和 progress 按最终事实更新，不再以离线 ingest 代替完整流程；
- [ ] T12 整改提交边界清晰，工作区干净，未混入 T13 正式产物；
- [ ] 全量测试和三轮重置后，Git 内 initial/frozen payload 无 `-wal`、`-shm`、staging 或 backup 残留；
- [ ] 独立 reviewer 复验确认无 Critical/Important 未关闭问题。

在上述条件完成前：

- 可以设计 T13 Harness 接口和测试矩阵；
- 不得提交 T13 正式 Harness、E2E 结果或 UI 验收报告；
- 不得把当前 initial/frozen snapshot 作为 T13 权威 fixture；
- 不得对外宣称 T12 已完成或 T13 已准入。

## 7. 最终复验命令

```bash
.venv/bin/python -m pytest -q tests/integration/scripts

.venv/bin/python -m pytest -q \
  tests/unit/test_ai_cache.py \
  tests/unit/application/test_run_query.py \
  tests/integration/use_cases/test_run_lint.py \
  tests/integration/use_cases/test_import_source.py

.venv/bin/python -m pytest -q

.venv/bin/coverage run --source=src/domain,src/application -m pytest -q
.venv/bin/coverage report --include='src/domain/*,src/application/*' --fail-under=90

.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/python -m compileall -q src tests scripts
git diff --check
git status --short --branch
```

另需在三个完全不同的临时 root 中执行并归档结果：

```text
1. 干净 root -> restore initial -> validate_data
2. 干净 root -> restore frozen -> 禁网完整缓存主流程 -> 发布 LLD-724_2
3. 完整演示 root -> reset initial，连续重复三次
```

## 8. 执行顺序与确认点

| 批次 | 内容 | 停点 |
|---|---|---|
| 批次 1 | M1 输出路径保护和原子 snapshot capture | T12-R01～R03 通过后确认 |
| 批次 2 | M2 干净目录恢复、来源种子和归档保护 | T12-R04～R07 通过后确认 |
| 批次 3 | M4 原子 reset、兼容闸和回滚 | T12-R08～R09 通过后确认 |
| 批次 4 | M3 Query/Lint 缓存消费者与严格身份 | T12-R10～R13 通过后确认 |
| 批次 5 | 重新生成 initial/frozen、连续三次重置、补浏览器证据 | T12-R14～R15 通过后确认 |
| 批次 6 | 全量门禁、实施报告、progress、提交边界 | 开始独立 reviewer 终验 |
| 批次 7 | 独立 reviewer 终验 | 无 Critical/Important 后签认 T13 准入 |

建议严格按顺序执行。Critical 路径保护必须先修；否则后续重新生成 snapshot 的过程本身仍有删除项目数据的风险。
