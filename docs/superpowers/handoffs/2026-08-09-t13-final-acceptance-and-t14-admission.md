# T13 最终验收与 T14 准入签署报告

> 验收日期：2026-08-09  
> 验收角色：独立 reviewer  
> 分支：`feat/lightweight-t01`  
> 整改基线：`6329e56`  
> 本轮代码提交：`001894e7d95633cec1630b62becf55c81943c9ba`  
> 本轮证据提交 / HEAD：`e79700eaad7b911a75092d84392cccf9f7cf2f46`  
> 最终结论：**T13 通过验收，准许进入 T14。**

## 1. 准入结论

上一轮唯一未关闭的 Important 项 T13-R03 已完成整改并通过独立复核：

- 完整成功流恢复为“导入→查询→自检→决定→变更单→审批→发布→发布后查询”。
- Query 位于导入之后、自检之前，并校验当前基线版本及引用。
- 增加网关级调用顺序见证；错误顺序经过独立变异测试可被稳定捕获。
- 完整 E2E 直接读取 SQLite `knowledge_cards`，校验卡片 ID、产品版本、状态及内容。
- `content`、`product_version`、`status` 三类篡改均有常驻破坏性反证。
- T13-O04 的 Pydantic 枚举序列化警告已消除。

当前没有未关闭的 Critical 或 Important，满足 T14 准入规则。

## 2. T13-R03 逐项验收

| 验收项 | 独立证据 | 结论 |
| --- | --- | --- |
| Query 位于导入后、自检前 | `_run_flow_to_publish()` 中依次调用 `import_source()`、`query()`、`run_lint()` | 通过 |
| 基线查询保持有效 | 断言 `baseline_version == LLD-724_1` 且 citations 非空 | 通过 |
| 实际调用顺序可观测 | `gateway_calls[:3] == [ingest, query, lint]` | 通过 |
| 错误顺序必须失败 | 独立临时副本将 Query 移至导入前，测试以 `['query', 'ingest', 'lint'] != ['ingest', 'query', 'lint']` 失败 | 通过 |
| SQLite 生效卡片直接验证 | 直接查询 `knowledge_cards`，断言 `(RULE-LLD-001, LLD-724_2, effective, 新规则文本)` | 通过 |
| SQLite 三类篡改反证 | 参数化覆盖 `content`、`product_version`、`status` | 通过 |
| 发布三方一致 | `full.md` / `cards.json`、SQLite、Manifest 及发布后查询均保留断言 | 通过 |
| 测试报告与实际证据一致 | `docs/qa/test-report-2026-08-24.md` 已更新流程顺序、三方一致及零警告口径 | 通过 |

## 3. 本轮独立验证结果

### 3.1 完整成功流与变异验证

```bash
.venv/bin/python -m pytest tests/e2e/test_full_success.py -q
# 5 passed
```

独立 reviewer 在 `/private/tmp/t13-r03-mutation.F16oXM` 创建 HEAD 的临时副本，仅将 Query 移回导入前，然后执行：

```bash
/Users/shiminghao/Documents/产品智策Wiki/.venv/bin/python \
  -m pytest tests/e2e/test_full_success.py::test_complete_governed_product_change -q
# 1 failed
# actual:   ['query', 'ingest', 'lint']
# expected: ['ingest', 'query', 'lint']
```

判定：顺序断言不是装饰性断言，能够捕获上一轮实际缺陷。

### 3.2 专项测试

```bash
.venv/bin/python -m pytest tests/golden tests/e2e tests/security -q
# 83 passed in 5.93s
# 0 warning
```

### 3.3 全量测试与覆盖率

```bash
.venv/bin/python -m pytest -q \
  --cov=src/domain \
  --cov=src/application \
  --cov-report=term-missing \
  --cov-fail-under=85
# 730 passed in 24.48s
# 2587 statements, 119 missed
# Total coverage: 95.40%
# 0 warning
```

### 3.4 三次连续重置

三轮均执行 `reset_demo.py --snapshot initial`，再运行完整成功流测试；每轮结果均为：

```text
RESET_OK snapshot=initial
VALIDATION_OK baseline=LLD-724_1
5 passed
```

### 3.5 静态门禁与工作区

```text
ruff check：All checks passed
ruff format --check：163 files already formatted
compileall：通过
git diff --check：通过
复核开始与门禁执行完成时工作区：干净
签署后工作区：仅新增本报告，未修改生产代码或测试
```

## 4. 整改项最终状态

```text
T13-R01（冻结缓存态证据）：通过
T13-R02（发布产物恒真断言）：通过
T13-R03（流程顺序与 SQLite 一致性）：通过
T13-O04（Pydantic warning）：关闭
Critical 未关闭：0
Important 未关闭：0
T14 准入：通过
```

## 5. 非阻断观察项

- T13-O01：首页“当前项目”眉题仍可能被 Streamlit 顶部工具栏裁切。该项保持 Minor，不影响 T14 开工，但建议纳入 T14 正式演示加固清单。
- 本轮仅修改 E2E 测试与验收文档，没有生产 UI 代码变化；冻结缓存 1440×1024 浏览器证据沿用上一轮已经独立复核并关闭的 T13-R01 证据，不重复扩大验收范围。

## 6. 签署

基于 HEAD `e79700e` 的代码审查、错误顺序独立变异、专项与全量测试、覆盖率、三轮重置及静态门禁结果，独立 reviewer 签署：

> **T13 已达到通过要求，准许项目进入 T14。**
