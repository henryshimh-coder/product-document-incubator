# T13 整改复核与 T14 准入报告（第二轮）

> 复核日期：2026-08-09  
> 复核角色：独立 reviewer  
> 分支：`feat/lightweight-t01`  
> 整改前 HEAD：`8ff01c6`  
> 本轮复核 HEAD：`6329e56`  
> 结论：**T13-R01 已关闭；T13-R02 仅部分关闭，并引入 1 项 Important 流程证据偏差。暂不签署 T14 准入。**

## 1. 结论摘要

本轮整改提交边界清晰，工作区在复核开始时干净。查询页冻结缓存能力已能在真实 frozen 快照上命中，并正确展示问题、回答、基线版本、冻结缓存标识和缓存生成时间，T13-R01 可以关闭。

T13-R02 已删除恒真断言，并补充 `full.md`、`cards.json`、发布后查询和破坏性反证；但完整成功流中的查询被移动到了资料导入之前，实际顺序变成“查询→导入→自检”，与 T13 计划和测试文件自身声明的“导入→查询→自检”不一致。此外，测试报告声称已验证 SQLite 生效卡片内容，但当前 E2E 只查询了 `baselines` 状态，没有直接检查 `knowledge_cards` 的版本、状态和内容。

因此，本轮自动门禁虽然全绿，仍不能以当前证据签署“无 Important 未关闭”。

## 2. 本轮独立验证结果

### 2.1 自动测试与覆盖率

```bash
.venv/bin/python -m pytest tests/golden tests/e2e tests/security -q
# 80 passed, 1 warning

.venv/bin/python -m pytest -q \
  --cov=src/domain \
  --cov=src/application \
  --cov-report=term-missing \
  --cov-fail-under=85
# 727 passed, 1 warning
# Total coverage: 95.40%
```

判定：测试失败数为 0，覆盖率高于 85% 门槛；报告中的 `727 passed` 和 `95.40%` 可复现。

### 2.2 三次有效重置与完整成功测试

```bash
for run_index in 1 2 3
do
  .venv/bin/python scripts/reset_demo.py --snapshot initial || exit 1
  .venv/bin/python -m pytest tests/e2e/test_full_success.py -q || exit 1
done
```

三轮均为：

```text
RESET_OK snapshot=initial
VALIDATION_OK baseline=LLD-724_1
2 passed
```

判定：重置和测试均正确 fail-fast，可重复性门禁通过。测试通过不等于流程顺序符合计划；顺序偏差见 T13-R03。

### 2.3 静态门禁

```bash
.venv/bin/ruff check src scripts tests streamlit_app.py
# All checks passed!

.venv/bin/ruff format --check src scripts tests streamlit_app.py
# 163 files already formatted

.venv/bin/python -m compileall -q src scripts tests streamlit_app.py
git diff --check
```

上述命令全部通过。

### 2.4 本轮 1440×1024 冻结缓存实演

独立 reviewer 本轮重新执行，而不是复用整改方截图：

1. 将 `data/demo_snapshots/frozen` 恢复到独立临时目录。
2. 配置本地 Dify 占位参数，启动该独立目录对应的 Streamlit 应用。
3. 在“当前查询”选择“冻结缓存”。
4. 输入“当前目标客群是什么？”并提交。
5. 真实命中 Query frozen cache，未发起实时网络调用。

本轮 DOM 与布局核验结果：

```text
viewport: 1440 × 1024
问题：当前目标客群是什么？
回答：当前目标客群是符合准入要求的存量客户。
产品版本：LLD-724_1
查询方式：冻结缓存
状态：冻结缓存 · 缓存生成时间 2026-08-07T04:20:46+00:00
回答可见：是
版本可见：是
缓存状态可见：是（bottom=1004.24 < viewport bottom=1024）
横向溢出：无
```

本轮证据：

```text
/private/tmp/t13-independent-rereview.CZgo1H/reviewer-evidence/01-query-cache-result-1440x1024.png
/private/tmp/t13-independent-rereview.CZgo1H/reviewer-evidence/06-query-cache-node-scroll-1440x1024.png
```

判定：T13-R01 关闭。截图只能支持可见布局；键盘、读屏和完整 WCAG 合规仍需专门测试，本报告不作超出证据的声明。

## 3. 整改项逐条判定

### T13-R01：关闭

已满足：

- Query 页面新增“实时查询／冻结缓存”选择。
- `preferred_mode=cache` 透传到应用命令。
- UI 回归测试断言冻结缓存、基线版本、缓存时间，并排除“实时生成”。
- 本轮真实 frozen 快照浏览器复核成功。
- UI 报告与已知限制之间的原矛盾已消除。

### T13-R02：部分关闭

已满足：

- 删除 `assert PUBLISHED_RULE_CONTENT` 恒真断言。
- 直接验证 Manifest 指向的 `full.md` 和 `cards.json`。
- 发布后查询验证新回答和 `LLD-724_2`。
- 增加破坏性反证用例。

尚未满足：

- 原验收标准要求发布文件、SQLite 生效卡片和 Manifest 三方一致；当前 E2E 未直接读取 `knowledge_cards`。
- 整改重构改变了完整成功流的规定顺序，导致“导入后、发布前查询”场景未被覆盖。

## 4. 必须整改项

### T13-R03（Important）：恢复完整成功流顺序，并补 SQLite 生效卡片断言

**现状 1：流程顺序与计划不一致**

- `tests/e2e/test_full_success.py:29-49` 的 `_run_flow_to_publish()` 实际执行“导入→自检→决定→审批→发布”，没有 Query。
- 同文件 `:69-73` 在调用 helper 前先 Query，完整测试实际执行“查询→导入→自检”。
- 文件 docstring、helper docstring 和 T13 计划均写明“导入→查询→自检”。

**为什么重要**

T13 的完整 E2E 应证明资料导入后仍能基于当前生效基线完成查询，再进入自检。把查询放在导入之前，会漏掉“导入对查询上下文、候选提示、引用或基线读取造成回归”的场景；测试名称与实际行为也不一致。

**修改要求**

1. 把基线 Query 移入 `_run_flow_to_publish()`，紧跟 `harness.import_source()` 之后、`harness.run_lint()` 之前。
2. 保留断言：`baseline_version == LLD-724_1`、存在 citations。
3. 删除 `test_complete_governed_product_change()` 开头的导入前 Query，避免重复和顺序混淆。
4. 破坏性反证测试也应通过同一 helper 走完整规定顺序。

**现状 2：报告声称的 SQLite 内容一致性没有直接证据**

- 当前 E2E 对 SQLite 只执行 `SELECT version, status FROM baselines`。
- 发布后 Query 从 Manifest 指向的 `cards.json` 读取生效卡片，不能替代对 SQLite `knowledge_cards` 镜像的直接断言。
- `docs/qa/test-report-2026-08-24.md:91` 声称“发布产物内容、SQLite 生效卡片与 Manifest 三方版本/内容一致”，证据超出测试实际覆盖。

**修改要求**

在同一个完整成功 E2E 中直接查询 SQLite：

```sql
SELECT id, product_version, status, content
FROM knowledge_cards
WHERE project_id = 'LLD' AND id = 'RULE-LLD-001'
```

并断言：

```text
id = RULE-LLD-001
product_version = LLD-724_2
status = effective
content = PUBLISHED_RULE_CONTENT
```

同时保留 `full.md`、`cards.json`、Manifest、旧/新 baseline 状态及发布后查询断言。

**通过标准**

- 完整成功流的实际调用顺序为“导入→查询→自检→决定→变更单→审批→发布→发布后查询”。
- 将 Query 移回导入前时，新增顺序敏感断言或测试必须失败；恢复正确顺序后通过。
- 篡改 SQLite `knowledge_cards.content`、`product_version` 或 `status` 任一项时，完整 E2E 必须失败。
- `docs/qa/test-report-2026-08-24.md` 的三方一致性描述与实际断言逐项一致。
- 专项、全量、三轮重置和静态门禁继续通过。

## 5. 非阻断项

### T13-O04（Minor）：消除缓存 UI 测试的 Pydantic 序列化 warning

`tests/e2e/test_query_flow.py:170` 在 `model_copy(update=...)` 中用字符串 `"cache"` 更新枚举字段，导致专项和全量测试各出现 1 条 `PydanticSerializationUnexpectedValue` warning。建议改用 `CallResultMode.CACHE`。通过标准：同一测试行为不变，专项和全量测试无该 warning。

### T13-O01（Minor，延续）：首页眉题裁切

首页“当前项目”眉题仍被 Streamlit 顶部工具栏裁切。该项不阻断 T14，但应在正式演示加固前修复。

## 6. T14 准入判定

```text
T13-R01：通过
T13-R02：部分通过
T13-R03：未通过（Important）
黄金 / E2E / 安全专项：80 passed，1 warning
全量：727 passed，1 warning
领域 + application 覆盖率：95.40%
三次重置：通过
静态门禁：通过
T14 准入：暂缓
```

关闭 T13-R03 后，重新执行本报告第 2 节全部门禁；若无 Critical / Important 未关闭，可签署进入 T14。
