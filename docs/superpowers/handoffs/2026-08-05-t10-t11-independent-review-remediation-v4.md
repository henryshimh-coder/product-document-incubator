# T10、T11 独立复核整改文件 v4

## 1. 文档信息

| 项目 | 内容 |
|---|---|
| 项目 | 产品智策轻量交付版，路线 A |
| 整改对象 | T10 原子发布与恢复、T11 追溯与价值提示 |
| 出具日期 | 2026-08-05 |
| 当前分支 | `feat/lightweight-t01` |
| 当前 HEAD | `4629f73 docs: archive t10/t11 v3 acceptance and remediation handoff` |
| 当前整改实现 | `b3845d5 fix: close publish source integrity and trace verification gaps` |
| 文档用途 | 交给开发 Agent 关闭独立 reviewer 发现的问题，并作为再次申请 T12 准入的直接验收依据 |

本文档只定义当前版本仍需完成的修改，不重复已经通过的 T10/T11 功能，也不授权开始 T12。开发 Agent 应在独立分支完成本文件要求，并在全部门槛通过后重新申请独立复核。

## 2. 独立复核结论

### 2.1 总体结论

**T10、T11 本轮终验不通过，T12 暂不准入。**

- T10：原子发布、恢复、来源归档完整性和页面主体已经完成，但发布闸仍接受伪造的 IssueEvidence 版本、定位和部分 citation 元数据，存在 1 个 Important 功能问题。
- T11：六节点 Relation 链、原文追溯、市场证据分类和轻量成本联动功能复验通过，当前没有发现新的 T11 功能阻断。
- 交付闭环：联合验收脚本、日志、浏览器截图、实施报告和进度台账均被 `.superpowers/sdd/.gitignore` 排除，无法随提交交付；旧浏览器阻塞记录也未按 v3 要求追加解除说明，存在 1 个 Process Important 问题。

### 2.2 已通过的独立复验

| 检查项 | 独立结果 |
|---|---|
| T10 专项 | `85 passed` |
| T11 专项 | `64 passed` |
| 全量测试 | `659 passed` |
| domain + application 覆盖率 | `95%`，2457 行缺 117 行，门槛 90% |
| 联合验收 | 全新临时环境 14 步全部 PASS，当前/发布后/历史业务语义正确 |
| Ruff check | 通过 |
| Ruff format check | 通过，142 个文件已格式化 |
| compileall | 通过 |
| `git diff --check` | 通过 |
| skip/xfail 扫描 | 未发现规避验收的标记 |
| 浏览器 | 1440x1024、390x844 无横向溢出；六节点有序；控制台无 error/warn |
| Git 状态 | 独立复核结束时工作区干净 |

## 3. 未关闭问题

### V4-P1-01：发布闸未绑定证据版本、定位和 citation 身份

**级别：Important。阻断 T10 完成和 T12。**

#### 当前事实

`src/application/use_cases/publish_baseline.py` 的正式来源证据分支当前只验证：

- `evidence.citation_id` 能找到同 ID 的 fragment；
- `evidence.excerpt` 出现在 fragment 正文中。

它没有验证：

- `evidence.document_version == material.document_version`；
- `evidence.page_or_section == fragment.locator`。

当前基线证据分支只按 `page_or_section` 和 `excerpt` 查找材料片段，没有验证：

- `evidence.document_version == current.current_version`；
- `evidence.citation_id` 是否是当前基线真实生成的 citation 身份。

#### 独立攻击复验

正式风险证据保持合法 citation ID 和 excerpt，只把元数据改成：

```text
document_version = FORGED-V999
page_or_section = heading:伪造章节; line:999
```

系统仍成功发布：

```text
SOURCE_METADATA: published=LLD-724_2 forged_version_accepted=true forged_locator_accepted=true
```

另新增一条当前基线证据，使用任意 citation ID、正确 locator/excerpt，但把版本改为 `FORGED-V999`，系统仍成功发布：

```text
BASELINE_METADATA: published=LLD-724_2 expected_version=LLD-724_1 forged_version_accepted=true
```

#### 影响

1. 发布记录声称的证据版本和章节可能不是系统实际验证的版本和章节。
2. 人工批准页面展示的 IssueEvidence 与发布闸读取的真实材料不能证明是一组相同证据。
3. 当前基线 evidence 可以使用任意 citation ID，只要 locator/excerpt 恰好可匹配。
4. 不满足“正式结论必须回到正确版本、正确位置、正确片段”的可信发布要求。

### V4-P1-02：正式验收材料被 Git 排除

**级别：Process Important。阻断 T10/T11 正式交付闭环和 T12。**

`.superpowers/sdd/.gitignore` 当前内容只有：

```gitignore
*
```

因此以下材料虽然存在于当前机器，但 `git ls-files` 结果为 0：

- `task-10-implementer-report.md`；
- `task-11-implementer-report.md`；
- `progress.md`；
- `joint_acceptance.py`；
- `joint-acceptance.log`；
- 六张浏览器截图；
- `browser-acceptance.md`；
- `browser-acceptance-blocked.md`。

此外，`browser-acceptance-blocked.md` 仍只记录旧的“扩展未连接”状态，没有追加“阻塞已解除”和正式证据入口。

#### 影响

1. 切换分支、重新 clone 或交给其他 Agent 后，验收证据不会出现。
2. 无法从提交 SHA 独立复现 14 步联合验收和浏览器结论。
3. 当前 HEAD 只提交了 v3 整改要求，没有提交 v3 整改后的实施证据。

## 4. 修改方案

### M1：严格绑定正式来源 IssueEvidence

#### 修改文件

- `src/application/use_cases/publish_baseline.py`
- `tests/integration/use_cases/test_publish_baseline.py`

#### 指定修改

正式来源证据按 citation ID 找到 fragment 后，必须同时满足：

```python
evidence.document_version == material.document_version
evidence.page_or_section == fragment.locator
evidence.excerpt in fragment.text
```

任一条件不满足，统一返回：

```text
PUBLISH_CITATION_UNVERIFIABLE
PUBLISH_EVIDENCE_CITATION_UNVERIFIABLE:<citation_id>
```

不得自动改写 evidence、不得回退到同来源其他 fragment、不得只按 excerpt 搜索任意章节。

### M2：严格绑定当前基线 IssueEvidence

#### 修改文件

- `src/application/use_cases/run_lint.py`
- `src/application/use_cases/publish_baseline.py`
- 必要时新增一个共享的窄 citation 身份 helper
- `tests/integration/use_cases/test_publish_baseline.py`
- `tests/integration/use_cases/test_run_lint.py`

#### 指定修改

1. 当前基线证据必须满足：

```python
evidence.source_id == current.current_baseline_id
evidence.document_version == current.current_version
evidence.page_or_section == verified_fragment.locator
evidence.excerpt in verified_fragment.text
```

2. `evidence.citation_id` 必须能映射到当前基线真实生成的 citation 身份，不得接受任意字符串。
3. `run_lint.py` 和 `publish_baseline.py` 必须复用同一个 citation ID 生成规则，避免一端生成、另一端猜测。
4. 推荐保留现有 `CIT-BASE-{index:03d}` 兼容格式，但把生成逻辑提取为共享纯函数；发布侧根据 Manifest 指向的完整卡片快照重建合法 citation 映射。
5. citation 映射至少包含：citation ID、卡片 ID、基线版本、locator、excerpt。发布时进行全字段一致性校验。
6. 不修改 IssueEvidence 数据结构，不引入数据库迁移，除非现有模型无法表达上述映射。

### M3：补齐负向测试

新增以下测试，全部使用真实文件存储、真实 SQLite 仓储和真实 `LocalQueryMaterialReader`：

| 用例 ID | 场景 | 通过条件 |
|---|---|---|
| V4-A01 | 正式来源 citation/excerpt 正确，document_version 伪造 | 发布失败，错误码稳定 |
| V4-A02 | 正式来源 citation/excerpt 正确，page_or_section 伪造 | 发布失败，不回退其他位置 |
| V4-A03 | 正式来源四项元数据全部正确 | 正常发布 |
| V4-A04 | 当前基线 locator/excerpt 正确，document_version 伪造 | 发布失败 |
| V4-A05 | 当前基线版本/locator/excerpt 正确，citation_id 伪造 | 发布失败 |
| V4-A06 | 当前基线 citation/version 正确，locator 伪造 | 发布失败 |
| V4-A07 | 当前基线全部元数据正确 | 正常发布 |

所有失败用例必须同时断言：

- ChangeRequest 保持 `approved`；
- Manifest 仍指向父版本；
- SQLite 当前基线和卡片镜像不变；
- 目标版本目录和临时发布目录没有生效；
- 恢复合法 evidence 后可直接重试，不重复人工审批。

现有 `tests/integration/release_env.py` 中手写的 `page_or_section="目标客群"`、`"客群限制"` 若与真实 extractor locator 不一致，应改为从真实已验证 fragment 获取，不得通过放宽生产校验保留旧夹具。

### M4：让验收证据可随 Git 交付

#### 修改文件

- `.superpowers/sdd/.gitignore`
- `.superpowers/sdd/2026-07-29-product-intelligence-lightweight/progress.md`
- `.superpowers/sdd/2026-07-29-product-intelligence-lightweight/task-10-implementer-report.md`
- `.superpowers/sdd/2026-07-29-product-intelligence-lightweight/task-11-implementer-report.md`
- `.superpowers/sdd/2026-07-29-product-intelligence-lightweight/evidence/t10-t11/browser-acceptance-blocked.md`
- `.superpowers/sdd/2026-07-29-product-intelligence-lightweight/evidence/t10-t11/` 下正式验收材料

#### 指定修改

1. 保留 SDD 目录默认忽略策略，但对本次正式交付材料设置最小白名单；不要直接取消整个目录的忽略。
2. 只纳入以下内容：

```text
progress.md
task-10-implementer-report.md
task-11-implementer-report.md
evidence/t10-t11/joint_acceptance.py
evidence/t10-t11/joint-acceptance.log
evidence/t10-t11/browser-acceptance-blocked.md
evidence/t10-t11/browser/browser-acceptance.md
evidence/t10-t11/browser/release-desktop-1440x1024.png
evidence/t10-t11/browser/release-failure-mobile-390x844.png
evidence/t10-t11/browser/release-confirm-mobile-390x844.png
evidence/t10-t11/browser/release-success-mobile-390x844.png
evidence/t10-t11/browser/trace-six-node-desktop-1440x1024.png
evidence/t10-t11/browser/trace-six-node-mobile-390x844.png
```

3. `__pycache__`、临时数据库、隔离工程目录、浏览器 profile 和其他运行产物继续忽略。
4. 在 `browser-acceptance-blocked.md` 末尾追加“2026-08-05 阻塞已解除”，记录新证据目录、最终验收提交 SHA 和最新结论；不得删除历史阻塞记录。
5. M1-M3 合入后，以最终代码提交重新运行浏览器验收，并把 `browser-acceptance.md` 中的验收 SHA 更新为最终整改 SHA。旧 `b3845d5` 截图不能单独作为新代码的最终签认证据。
6. 使用 `git check-ignore -v` 和 `git ls-files` 逐项确认上述材料已经进入版本控制。

## 5. 执行顺序和停点

| 批次 | 工作内容 | 停点和确认条件 |
|---|---|---|
| 批次 1 | M1-M3 证据元数据绑定和负向测试 | V4-A01～A07 全部通过，独立攻击脚本不能再发布后停下确认 |
| 批次 2 | 全量回归和联合流程 | 专项、全量、覆盖率、静态检查、14 步联合验收全部通过后停下确认 |
| 批次 3 | M4 证据跟踪和浏览器换证 | 最终 SHA 的桌面/移动证据齐全、所有文件可由 `git ls-files` 查到后停下确认 |
| 批次 4 | 独立 reviewer 终验 | 无 Critical/Important 未关闭问题后批准进入 T12 |

建议提交边界：

```text
fix: bind publish evidence metadata to verified material
docs: track t10 t11 final acceptance evidence
```

不得把 T12 快照、T12 演示数据或其他功能改动混入上述提交。

## 6. 最终复验命令

```bash
.venv/bin/python -m pytest -q \
  tests/integration/use_cases/test_review_change_request.py \
  tests/integration/use_cases/test_publish_baseline.py \
  tests/integration/use_cases/test_publish_then_query.py \
  tests/integration/recovery/test_reconciliation.py \
  tests/e2e/test_release_flow.py

.venv/bin/python -m pytest -q \
  tests/unit/domain/test_market_evidence.py \
  tests/unit/domain/test_cost_impact.py \
  tests/integration/use_cases/test_build_trace.py \
  tests/e2e/test_trace_page.py

.venv/bin/python \
  .superpowers/sdd/2026-07-29-product-intelligence-lightweight/evidence/t10-t11/joint_acceptance.py

.venv/bin/python -m pytest -q
.venv/bin/coverage run --source=src/domain,src/application -m pytest -q
.venv/bin/coverage report --include='src/domain/*,src/application/*'
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/python -m compileall -q src tests scripts
git diff --check
git status --short --branch
git check-ignore -v \
  .superpowers/sdd/2026-07-29-product-intelligence-lightweight/evidence/t10-t11/browser/browser-acceptance.md
git ls-files \
  .superpowers/sdd/2026-07-29-product-intelligence-lightweight/evidence/t10-t11
```

注意：对白名单文件执行 `git check-ignore -v` 应无匹配输出并返回非零；`git ls-files` 必须列出全部正式验收材料。

## 7. 再次验收标准

只有同时满足以下条件，才能再次申请 T12 准入：

- [ ] V4-P1-01 已关闭，正式来源和当前基线证据均绑定正确 citation、版本、定位和 excerpt；
- [ ] V4-A01～A07 全部通过；
- [ ] 原有 V3-A01～A16、T10-A01～A19、T11-A01～A24 无回归；
- [ ] 独立攻击复验中，伪造 source/baseline 版本、定位或 citation 均无法发布；
- [ ] 联合 14 步在全新临时环境全部通过；
- [ ] 全量测试、覆盖率、Ruff、format、compileall、diff check 全部通过；
- [ ] 最终整改 SHA 的 1440x1024 和 390x844 浏览器证据通过；
- [ ] 实施报告、progress、联合验收和浏览器证据全部进入 Git；
- [ ] `browser-acceptance-blocked.md` 已追加阻塞解除记录；
- [ ] T10/T11 整改提交边界清晰，工作区干净；
- [ ] 独立 reviewer 确认没有 Critical/Important 未关闭问题。

在以上条件完成前：

- T11 功能可以继续保持冻结，只做防回归；
- 不得签认 T10/T11 最终完成；
- 不得生成或签认 T12 正式 `initial/frozen` 演示快照；
- 不得对外宣称当前版本已经通过独立终验。
