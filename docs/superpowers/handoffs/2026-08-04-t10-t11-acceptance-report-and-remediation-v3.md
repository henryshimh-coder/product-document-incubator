# T10、T11 当前版本验收报告及修改建议 v3

## 1. 文档信息

| 项目 | 内容 |
|---|---|
| 项目 | 产品智策轻量交付版，路线 A |
| 验收对象 | T10 原子发布与恢复、T11 追溯与价值提示 |
| 验收日期 | 2026-08-04 |
| 当前分支 | `feat/lightweight-t01` |
| T10 提交 | `b1193a3 fix: complete governed release remediation` |
| T11 提交 | `c56678c feat: add persisted traceability and governed value hints` |
| 交付目标 | 2026-08-30 正式演示版就绪，9 月现场实时演示完整流程 |
| 范围约束 | 保留代表性能力和轻量成本联动；不做损益测算 |
| 验收依据 | T10/T11 v2 修改建议、统一验收标准、技术开发文档、产品与界面设计文档 |

本文档基于当前主线代码、独立自动化复验、联合流程复跑和真实浏览器检查重新出具。本文档替代此前“仅根据实施报告判断完成度”的结论，作为本轮整改和再次申请 T12 准入的直接依据。

## 2. 验收结论

### 2.1 总体结论

**本轮验收不通过，T12 暂不准入。**

T10、T11 的主体功能、自动化质量和浏览器交互已经基本完成，但仍存在三个 Important 级可信性问题：

1. 联合验收把免责声明当作“目标客群”业务规则，导致 14 步流程在错误业务语义上通过。
2. 正式来源归档被篡改后仍可发布。
3. 无 citation 的裸来源引用可能被追溯服务标记为 `verified`。

此外，真实浏览器行为本轮已独立验证通过，但截图、测量结果和实施报告尚未在仓库内完成归档，交付证据门槛仍未闭合。

### 2.2 分项结论

| 验收域 | 结论 | 说明 |
|---|---|---|
| T10 完整快照、当前/历史查询 | 通过 | 发布后新旧版本内容、版本号和引用保持隔离 |
| T10 Manifest/SQLite 镜像与恢复 | 通过 | 原子替换、失败回滚、启动对账和幂等恢复均有覆盖 |
| T10 正式/沙箱发布边界 | **不通过** | 元数据边界已实现，但发布前未复验来源归档哈希和 citation 定位 |
| T10 发布页面 | 通过 | 桌面和移动端的确认、失败、恢复和重试流程可操作 |
| T11 Relation 六节点追溯 | 通过 | 五条主链边来自持久化 `relations`，缺边不自动补链 |
| T11 回到原文 | **不通过** | 合法 citation 可验证，但裸 `source_id` 会被错误标为已验证 |
| T11 市场证据分类 | 通过 | 普通规则、无效引用和沙箱材料不会被描述为正式充分证据 |
| T11 轻量成本联动 | 通过 | 只接受明确沙箱成本参数来源，自动标记模拟，无损益输出 |
| 联合 14 步流程 | **有条件失败** | 技术步骤全部执行成功，但初始业务规则和历史答案语义错误 |
| 工程质量 | 通过 | 全量测试、覆盖率、静态检查和工作区状态均达标 |
| 浏览器交互 | 通过 | 两个目标视口均无主内容横向溢出，关键操作可用 |
| 浏览器证据归档 | **未完成** | 仓库报告仍记录旧的 WebBridge 阻塞状态，缺少本轮截图和测量记录 |
| T12 准入 | **不通过** | 存在 Important 未关闭问题，且证据归档不完整 |

## 3. 已完成验收证据

### 3.1 自动化和工程质量

本轮在当前主线独立复跑结果如下：

| 检查项 | 结果 |
|---|---|
| T10 文档指定专项测试 | `72 passed` |
| T11 文档指定专项测试 | `62 passed` |
| 全量测试 | `647 passed` |
| domain + application 覆盖率 | `95%`，2420 行缺 116 行，门槛 90% |
| Ruff check | 通过 |
| Ruff format check | 通过，142 个文件已格式化 |
| Python compileall | 通过 |
| `git diff --check` | 通过 |
| `skip` / `xfail` 规避检查 | 未发现 |
| Git 状态 | 验收开始时工作区干净，T10/T11 为两个独立提交；本报告是当前唯一新增文件 |

### 3.2 联合流程复跑

联合验收脚本在全新临时工程根目录复跑，真实文件存储、真实 SQLite 仓储和真实 Use Case 均参与，14 个步骤全部返回 PASS：

```text
初始化 LLD-724_1
-> 导入两份正式材料和一份沙箱成本材料
-> 当前查询
-> Lint
-> 人工决定并生成 ChangeRequest
-> 决定幂等重放
-> 人工批准
-> 发布 LLD-724_2
-> 当前查询新规则
-> 历史查询父版本
-> 六节点 Relation 链
-> 来源定位与脱敏片段
-> 市场证据不足
-> 沙箱轻量成本联动
-> 重启对账
```

但步骤 3 和步骤 11 使用了错误的目标客群正文，因此“全部 PASS”不能直接转换为业务验收通过，详见 `ACC-P1-01`。

### 3.3 真实浏览器复验

浏览器使用隔离临时数据，不修改仓库正式 `data/`。

| 场景 | 视口 | 结果 |
|---|---:|---|
| 完整六节点追溯 | 1440x1024 | 六节点一屏可见，五条关系顺序正确，无横向溢出 |
| 完整六节点追溯 | 390x844 | 六节点按来源到基线纵向堆叠，卡片、文字和详情按钮未越界 |
| 发布页 | 1440x1024 | 候选列表和详情双列可读，确认弹窗、成功页和追溯入口可用 |
| 发布页 | 390x844 | 内容纵向排列，无正向横向溢出，主按钮可操作 |
| 移动确认弹窗 | 390x844 | 弹窗约 358x392，完整位于视口内，确认和取消均可操作 |
| 发布失败 | 390x844 | 返回错误码、旧版本继续生效、变更保持已批准 |
| 恢复后重试 | 390x844 | 无需重复审批，重试发布成功，目标版本变为 `LLD-724_2` |
| 浏览器控制台 | 桌面/移动 | 未发现 error、warn |

页面行为已经达到浏览器验收要求；当前缺口是将上述结果正式留存为仓库证据。

## 4. 未通过问题

### ACC-P1-01：代表性业务规则被免责声明替代

**级别：Important，阻断正式演示快照和 T12。**

#### 当前事实

`scripts/bootstrap_demo.py` 当前定义：

```python
RULE_CARD_CONTENT = "仅作为脱敏演示基线使用。"
```

该字符串被同时写入：

- 标题为“目标客群”的正式规则卡；
- 初始基线 `full.md` 的“目标客群”章节；
- 基线来源文件的“目标客群”章节；
- 联合验收当前查询和历史查询的期望值；
- 追溯页的已验证原文片段。

因此联合验收询问“当前目标客群是什么？”时，把免责声明当作正确答案。现有 Golden、查询 E2E 和发布集成夹具使用的正确业务语义是：

```text
当前目标客群是符合准入要求的存量客户。
```

免责声明在集成夹具中原本位于附录，不属于目标客群正文。

#### 影响

1. 14 步联合验收的业务断言失真，存在同源常量自证问题。
2. 比赛现场第一轮查询会返回明显不符合问题语义的答案。
3. 发布前后差异会表现为“免责声明变为业务规则”，降低方案可信度。
4. 追溯虽然能定位原文，但定位到的不是代表性业务事实。

#### 通过条件

- 初始当前查询明确返回“当前目标客群是符合准入要求的存量客户”。
- 发布后返回收紧后的新规则。
- 历史查询重新返回初始业务规则，而不是免责声明。
- 追溯 excerpt 定位到真实目标客群规则。
- 免责声明保留在附录或固定安全说明中，不作为业务卡正文。

### ACC-P1-02：正式来源归档完整性未进入发布闸

**级别：Important，阻断 T10 正式来源边界和 T12。**

#### 当前事实

`PublishBaseline._validate_formal_sources()` 当前验证：

- SourceRecord 存在；
- 来源属于当前项目；
- `ingest_status == completed`；
- 来源不是沙箱且权威级别合格。

但发布前没有重新验证：

- `archive_path` 是否仍位于受控来源目录；
- 归档文件 SHA-256 是否等于 SourceRecord；
- 文件大小是否一致；
- `SOURCE_ID:CITATION_ID` 中的 citation/chunk 是否真实存在；
- IssueEvidence 的 locator/excerpt 是否仍对应已验证片段。

独立复验中，基线来源登记哈希为：

```text
b70002c41cd142d87e038a6574212f39989a57dfc32ab1d39c1b3cf9c719016e
```

修改后的实际文件哈希为：

```text
bfb85bd6914624924b09358af7ba84e48cab422fd773f6c5cff5d9d4b400e7e1
```

两者不一致时，系统仍成功发布 `LLD-724_2`。这说明正式来源记录存在并不等于发布依据仍然可验证。

#### 影响

1. 已被替换或污染的来源仍可支持正式基线发布。
2. 发布成功后追溯页才显示不可验证，发现时间过晚。
3. “人工批准 + 正式来源”无法形成完整的可信发布闭环。

#### 通过条件

- 来源归档哈希、大小、路径或 citation 任一异常时，发布在创建临时发布目录前失败。
- ChangeRequest 保持 `approved`，旧 Manifest、SQLite 和版本目录不变。
- 恢复合法来源后，可以直接重试发布，不重复人工审批。
- 失败返回稳定、可审计的错误码，不向 UI 暴露绝对路径或敏感正文。

### ACC-P1-03：无 citation 的来源被错误标记为已验证

**级别：Important，阻断 T11“回到原文”验收。**

#### 当前事实

`BuildTrace._verify_source_excerpt()` 在归档文件本身校验通过、但卡片没有该来源的 citation 时返回：

```python
return "verified", None
```

发布端又明确允许 `SOURCE_ID` 或 `SOURCE_ID:CITATION_ID` 两种形式，因此可以形成以下状态：

```text
来源记录存在且文件完整
-> 卡片只写 SOURCE_ID，没有 chunk
-> 追溯节点显示 verified
-> 页面没有 locator 和 excerpt
```

这不满足“正式结论必须解析到引用位置”和“回到原文”的要求。

#### 通过条件

- 无 citation 时保留来源节点，但 `verification=unverifiable`。
- 页面明确显示“未提供可定位引用”，不显示已验证徽标。
- 只有归档完整且 citation/chunk 定位成功时才能返回 `verified`。
- 不回退到首段、任意片段或同来源其他 citation。

### ACC-P2-01：浏览器验收材料仍停留在旧阻塞状态

**级别：Process Important，阻断交付证据闭环。**

当前 `task-10-implementer-report.md`、`task-11-implementer-report.md`、`progress.md` 和 `browser-acceptance-blocked.md` 仍记录“WebBridge 扩展未连接，真实浏览器验收未完成”。

本轮已经通过应用内浏览器完成真实检查，但仓库内尚无：

- 1440x1024 发布页截图；
- 1440x1024 六节点追溯截图；
- 390x844 发布页和确认弹窗截图；
- 390x844 六节点纵向堆叠截图；
- scrollWidth/clientWidth、节点顺序、弹窗尺寸和控制台结果记录；
- 对旧阻塞记录的“已解除”说明。

页面行为可以判为通过，但验收材料仍不能判为齐备。

## 5. 修改建议

### M1：修复演示基线语义和独立验收预期

#### 修改文件

- `scripts/bootstrap_demo.py`
- `.superpowers/sdd/2026-07-29-product-intelligence-lightweight/evidence/t10-t11/joint_acceptance.py`
- 必要的 bootstrap、查询和联合流程测试

#### 指定修改

1. 将初始规则正文改为：

```text
当前目标客群是符合准入要求的存量客户。
```

2. 单独定义免责声明，并只放在基线说明或附录：

```text
仅作为脱敏演示基线使用。
```

3. 保留当前发布后规则：

```text
目标客群收紧为符合准入要求且通过风险评估的存量客户。
```

4. 联合验收脚本不得直接用被测模块导出的 `RULE_CARD_CONTENT` 作为唯一正确答案。应在验收侧定义独立、固定的业务预期，避免实现和验收共享同一个错误常量。
5. 对当前、发布后和历史三次查询分别断言正文、版本号和引用片段。

#### 新增测试

| 用例 ID | 场景 | 通过条件 |
|---|---|---|
| V3-A01 | bootstrap 初始目标客群 | 规则卡正文为真实客群规则，免责声明不在正文 |
| V3-A02 | 联合流程发布前查询 | 返回初始客群规则和 `_1` |
| V3-A03 | 联合流程发布后查询 | 返回收紧规则和 `_2` |
| V3-A04 | 联合流程历史查询 | 返回初始客群规则和 `_1` |
| V3-A05 | 来源追溯 | excerpt 包含初始客群规则，不是免责声明 |

### M2：把来源归档和 citation 验证前移到发布闸

#### 修改文件

- `src/application/use_cases/publish_baseline.py`
- `src/application/container.py`
- `src/application/ports/` 下现有材料读取端口或新增窄验证端口
- `tests/integration/use_cases/test_publish_baseline.py`

#### 推荐实现

1. 让 `_parse_source_ref()` 返回 `(source_id, citation_id)`，不要在解析后丢弃 citation。
2. 在 `PublishBaseline` 中注入现有受控材料读取能力，复用其路径、SHA-256、大小和 fragment 校验；不要在用例中另写一套不一致的文件读取逻辑。
3. 每张 effective 卡至少要有一个“合格正式来源 + 可定位 citation”作为正式证据。裸 `SOURCE_ID` 可以保留为补充来源关联，但不能单独满足正式证据门槛。
4. ChangeRequest 的每个 `evidence_ref` 在与 IssueEvidence 一一对应后，还必须验证其 SourceRecord 和 citation/chunk。
5. 验证必须发生在创建临时发布目录之前。
6. 建议增加稳定错误码：

```text
PUBLISH_SOURCE_INTEGRITY_FAILED
PUBLISH_CITATION_UNVERIFIABLE
```

7. 所有失败路径统一断言：ChangeRequest 仍为 `approved`，旧 Manifest、SQLite 和版本目录不变。

#### 新增测试

| 用例 ID | 场景 | 通过条件 |
|---|---|---|
| V3-A06 | 篡改基线卡来源归档 | 发布失败，旧版本继续生效 |
| V3-A07 | 篡改变更证据来源归档 | 发布失败，批准状态保留 |
| V3-A08 | citation/chunk 不存在 | 发布失败，不回退到其他片段 |
| V3-A09 | archive 路径越界 | 发布失败，不读取越界文件 |
| V3-A10 | 恢复合法来源后重试 | 不重复审批，发布成功 |
| V3-A11 | 裸 source ID + 有另一条合法 citation | 合法 citation 满足门槛，裸 ID 只作补充关联 |
| V3-A12 | 只有裸 source ID | 不满足正式证据门槛，发布失败 |

### M3：收紧追溯验证状态

#### 修改文件

- `src/application/use_cases/build_trace.py`
- `src/ui/components/trace_chain.py`
- `tests/integration/use_cases/test_build_trace.py`
- `tests/e2e/test_trace_page.py`

#### 指定修改

1. `_verify_source_excerpt()` 没有匹配 citation 时返回 `("unverifiable", None)`。
2. 页面区分以下状态：
   - 归档及 citation 均通过：已验证；
   - 来源存在但没有 citation：未提供可定位引用；
   - 归档哈希或 citation 失败：引用不可验证。
3. 不展示绝对路径、完整 L2 文档或未验证片段。
4. 可选增强：在 Issue 详情中展示“当前基线依据”和“挑战来源依据”两个已验证片段，让比赛现场能直接说明变更为什么发生。该增强不应改变六节点主链，也不能由 UI 推导 Relation。

#### 新增测试

| 用例 ID | 场景 | 通过条件 |
|---|---|---|
| V3-A13 | 只有裸 source ID | `verification=unverifiable`，无 excerpt |
| V3-A14 | 合法 citation | `verified`，locator 和脱敏 excerpt 正确 |
| V3-A15 | 同来源其他 chunk 存在 | 目标 chunk 缺失时仍不可验证，不回退 |
| V3-A16 | 页面展示裸引用 | 显示“未提供可定位引用”，不显示“已验证” |

### M4：补齐浏览器证据和报告台账

#### 建议交付位置

```text
.superpowers/sdd/2026-07-29-product-intelligence-lightweight/evidence/t10-t11/browser/
```

#### 必须保存

```text
release-desktop-1440x1024.png
release-failure-mobile-390x844.png
release-confirm-mobile-390x844.png
release-success-mobile-390x844.png
trace-six-node-desktop-1440x1024.png
trace-six-node-mobile-390x844.png
browser-acceptance.md
```

`browser-acceptance.md` 至少记录：

- 使用的隔离工程根目录和初始化方式；
- 两个视口尺寸；
- `scrollWidth == clientWidth` 或等价的无正向横向溢出结果；
- 六节点顺序和桌面首屏可见性；
- 移动弹窗边界和按钮可操作性；
- 发布失败错误码、旧版本状态和重试结果；
- 浏览器控制台结果；
- 验收提交 SHA。

更新以下文档：

- `task-10-implementer-report.md`；
- `task-11-implementer-report.md`；
- `progress.md`；
- `browser-acceptance-blocked.md` 追加“阻塞已解除”和新证据入口，不删除历史记录。

## 6. 修改执行顺序和停点

| 批次 | 工作内容 | 停点和确认条件 |
|---|---|---|
| 批次 1 | M1 演示基线语义修复 | V3-A01～A05 通过，联合流程三次查询正文正确后停下确认 |
| 批次 2 | M2 发布来源完整性和 citation 闸 | V3-A06～A12 通过，篡改来源发布失败、恢复后重试成功后停下确认 |
| 批次 3 | M3 追溯 fail-closed | V3-A13～A16 通过，裸引用不再显示已验证后停下确认 |
| 批次 4 | 全量回归和联合 14 步复验 | 全量、覆盖率、静态检查和正确业务语义全部通过后停下确认 |
| 批次 5 | M4 浏览器截图与报告归档 | 桌面/移动证据齐全、报告和 progress 更新后申请独立复核 |
| 批次 6 | 独立 reviewer 终验 | 无 Critical/Important 未关闭问题后批准进入 T12 |

共享文件会同时涉及 T10 和 T11。建议本轮使用一个独立整改分支连续处理，不要并行修改 `container.py`、`publish_baseline.py` 和 `build_trace.py`，避免依赖接线被覆盖。

## 7. 最终复验命令

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
```

质量门槛保持不变：

- 所有专项、V3 新增测试、联合流程和全量测试通过；
- domain + application 覆盖率不低于 90%；
- 不使用 `skip`、`xfail`、放宽断言或 Mock 主流程规避验收；
- 联合流程使用真实文件存储、真实 SQLite 仓储和真实 Use Case；
- 浏览器使用隔离临时数据，仓库正式 `data/` 零污染；
- 最终工作区只包含预期整改和证据文件。

## 8. T12 重新申请准入标准

以下条件全部满足后，才能重新申请进入 T12：

- [ ] ACC-P1-01 已关闭，当前/发布后/历史查询均返回正确业务规则；
- [ ] ACC-P1-02 已关闭，篡改来源无法发布；
- [ ] ACC-P1-03 已关闭，无 citation 不再标记为已验证；
- [ ] V3-A01～A16 全部通过；
- [ ] 联合 14 步在全新临时环境以正确业务语义通过；
- [ ] T10-A01～A19、T11-A01～A24 无回归；
- [ ] 1440x1024 和 390x844 浏览器截图、测量记录齐全；
- [ ] 全量测试、覆盖率、Ruff、format、compileall、diff check 全部通过；
- [ ] T10/T11 实施报告、浏览器证据和 `progress.md` 已更新；
- [ ] T10、T11 与本轮整改提交边界清楚，工作区干净；
- [ ] 独立 reviewer 确认无 Critical/Important 未关闭问题。

在这些条件完成前：

- 可以准备 T12 脚本结构和冻结流程设计；
- 不得生成或签认正式 `initial/frozen` 演示快照；
- 不得把当前 bootstrap 数据作为 8 月 30 日正式演示基线；
- 不得对外宣称 T10/T11 已完成最终验收。
