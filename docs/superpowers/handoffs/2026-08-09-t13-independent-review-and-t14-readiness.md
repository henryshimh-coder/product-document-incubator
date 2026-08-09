# T13 独立验收与 T14 准入报告

> 评审日期：2026-08-09  
> 评审角色：独立 reviewer  
> 评审分支：`feat/lightweight-t01`  
> 评审 HEAD：`8ff01c6`（`docs: record T13 acceptance evidence and test report`）  
> T13 起点：`fa5cc39`（T12 已验收候选）  
> 结论：**暂不签署无条件进入 T14；核心功能门禁通过，但 2 项验收证据缺口必须关闭。**

## 1. 结论摘要

T13 的核心代码、自动测试、安全边界、黄金指标和可重复全流程均已达到计划数值门槛：本轮独立复跑 `725 passed`，领域与应用覆盖率 `95.40%`，黄金测试、E2E 与安全专项 `78 passed`，三次“有效重置 + 完整成功流”均通过。

当前不直接签署 T14 准入，原因不是主功能失败，而是 T13 报告中的两项“已通过”证据还不能独立复核：

1. 查询页“冻结缓存”状态没有浏览器实演，也没有查询页缓存态 UI 回归测试；现有证据只覆盖实时查询和导入缓存。
2. 完整成功 E2E 中用于证明“发布内容与变更单一致”的断言是恒真断言，没有检查发布产物内容。

两项均属于验收证据有效性问题。修复范围小，不需要扩展一期功能，也不需要修改架构；关闭后可直接复核 T14 准入。

## 2. 本轮独立验证结果

### 2.1 提交与工作区

| 项目 | 结果 |
| --- | --- |
| 分支 | `feat/lightweight-t01` |
| HEAD | `8ff01c6` |
| T13 代码提交 | `c8d658a`、`9a7e6b9` |
| T13 报告提交 | `8ff01c6` |
| 工作区 | 评审开始时干净；当前仅新增本报告，未修改生产代码 |
| T13 与 T12 边界 | 清晰；T13 基于 `fa5cc39` |

### 2.2 自动测试与覆盖率

独立执行：

```bash
.venv/bin/python -m pytest tests/golden tests/e2e tests/security -q
# 78 passed

.venv/bin/python -m pytest -q \
  --cov=src/domain \
  --cov=src/application \
  --cov-report=term-missing \
  --cov-fail-under=85
# 725 passed
# Total coverage: 95.40%（2587 statements，119 missed）
```

判定：

- 失败数：`0`。
- 领域与应用覆盖率：`95.40%`，高于 `85%` 门槛。
- 黄金测试、E2E、安全专项全部通过。
- 报告中的 `725 passed` 和 `95.40%` 可复现。

### 2.3 三次重置与完整流程

本轮先关闭持有共享状态锁的 Streamlit 进程，再使用 fail-fast 命令复跑：

```bash
for run_index in 1 2 3
do
  .venv/bin/python scripts/reset_demo.py --snapshot initial || exit 1
  .venv/bin/python -m pytest tests/e2e/test_full_success.py -q || exit 1
done
```

三轮结果均为：

```text
RESET_OK snapshot=initial
VALIDATION_OK baseline=LLD-724_1
1 passed
```

补充发现：计划原示例只对 `pytest` 使用 `|| exit 1`，若应用仍持有共享锁，`reset_demo.py` 会正确返回非零并输出 `RESET_LOCKED`，但外层循环仍可能继续跑测试。T14 runbook 和后续验收命令必须对“重置”和“测试”分别 fail-fast，不能只看循环最终退出码。

### 2.4 静态质量门禁

```bash
.venv/bin/ruff check src scripts tests streamlit_app.py
# All checks passed!

.venv/bin/ruff format --check src scripts tests streamlit_app.py
# 163 files already formatted

.venv/bin/python -m compileall -q src scripts tests streamlit_app.py
git diff --check
git status --short --branch
```

上述范围全部通过，工作区干净。

说明：仓库级 `.venv/bin/ruff check .` 会命中 T10/T11 留存证据脚本 `.superpowers/.../joint_acceptance.py` 的既有格式问题。因此 T13 报告如继续表述“ruff clean”，必须写明实际检查范围；该既有证据脚本不属于 T13 生产代码门禁。

### 2.5 本轮 1440×1024 浏览器复核

本轮按当前 HEAD 重新启动 Streamlit，并以 1440×1024 检查六个导航页。当前初始快照下确认：

- 六个导航顺序正确。
- 首页当前版本视觉层级明确，主操作与次操作层级可辨。
- 六页没有发现横向溢出。
- 状态信息均带文字，不只依赖颜色。
- 追溯页明确区分“已入库”“生效中”“缺失环节”和“未验证判断”。
- 首页“当前项目”眉题仍被 Streamlit 顶部工具栏裁切；属于可见但不阻断功能的 Minor 缺陷。
- 未提供运行配置时，导入、查询、自检页会明确显示“服务尚未就绪”，这是 T14 全新环境配置与 runbook 的工作范围，不据此否决 T13。

本轮浏览器证据保存在 reviewer 临时目录：

```text
/private/tmp/t13-independent-review-2026-08-09/01-home.png
/private/tmp/t13-independent-review-2026-08-09/02-ingest.png
/private/tmp/t13-independent-review-2026-08-09/03-query.png
/private/tmp/t13-independent-review-2026-08-09/04-lint.png
/private/tmp/t13-independent-review-2026-08-09/05-release.png
/private/tmp/t13-independent-review-2026-08-09/06-trace.png
```

## 3. 必须整改项

### T13-R01（Important）：补齐查询页冻结缓存态的真实 UI 证据

**现状**

- `docs/qa/ui-acceptance-1440x1024.md` 将“实时／缓存标识”判为通过。
- 同一文档的证据实际只有查询页“实时生成”。
- `docs/qa/test-report-2026-08-24.md` 又明确写明“查询页冻结缓存标识未在浏览器会话中实演”。
- `tests/e2e/test_query_flow.py` 只断言了“实时生成”；缓存回退 E2E 覆盖的是 Ingest，不是查询页渲染。

因此，当前只能证明后端 Query cache 路径存在、Ingest cache UI 存在，不能证明查询页在真实缓存响应下正确展示“冻结缓存”。

**修改建议**

1. 在 `tests/e2e/test_query_flow.py` 新增查询页缓存态测试，构造 `result_mode=cache` 且带 `cache_generated_at` 的响应。
2. 断言页面同时显示：
   - `冻结缓存`；
   - 当前基线版本；
   - 缓存生成时间或等价可审计时间；
   - 不显示“实时生成”。
3. 从 frozen 快照或等价确定性 fixture 启动 UI，实际执行一次 Query cache，在 1440×1024 保存浏览器截图。
4. 更新 `docs/qa/ui-acceptance-1440x1024.md` 和 `docs/qa/test-report-2026-08-24.md`，把原“未实演”限制改为真实执行记录；不得用 Ingest 缓存证据替代 Query 缓存 UI 证据。

**通过标准**

- 新增测试先能在移除缓存标签映射时失败，恢复实现后通过。
- 真实浏览器截图同时可见问题、回答、基线版本和“冻结缓存”标签。
- 截图尺寸为 1440×1024，无横向滚动，无标签截断。
- UI 验收报告中的第 7 项与“已知限制”不再互相矛盾。

### T13-R02（Important）：把完整成功 E2E 的恒真断言改为发布产物断言

**现状**

`tests/e2e/test_full_success.py` 的结尾为：

```python
# 发布内容与变更单 after_content 一致（追溯链完整）。
assert released.id in manifest_store.read_and_validate().current_baseline_id
assert PUBLISHED_RULE_CONTENT
```

`assert PUBLISHED_RULE_CONTENT` 只检查常量非空，即使发布产物仍是旧内容也会通过。较低层集成测试已经覆盖发布内容，但这不能替代 T13“完整成功 E2E”自身的端到端证明。

**修改建议**

在发布完成后，从 Manifest 指向的可信产物中读取并验证，而不是检查常量本身。至少同时覆盖：

1. `full.md` 包含 `PUBLISHED_RULE_CONTENT`，且不再包含旧规则文本。
2. `cards.json` 中目标规则卡 `RULE-LLD-001.content == PUBLISHED_RULE_CONTENT`。
3. 可选但推荐：发布后再执行一次当前查询，回答和版本分别等于 `PUBLISHED_RULE_CONTENT`、`LLD-724_2`。
4. 保留旧基线为 `superseded`、新基线为 `effective`、Manifest 指向新版本的既有断言。

**通过标准**

- 人为把发布产物内容改回旧规则时，该 E2E 必须失败。
- 正常完整流程通过，且产物文件、SQLite 生效卡片和 Manifest 三方版本/内容一致。
- 删除恒真断言，不得用 `assert <非空常量>` 充当状态验证。

## 4. 非阻断改进项

### T13-O01（Minor）：修复首页眉题裁切

当前“当前项目”眉题被 Streamlit 顶部工具栏遮挡。建议调整主内容顶部留白或框架 toolbar 的层叠/显隐策略。通过标准：1440×1024 首页截图中眉题完整可读，不影响标题和首屏主操作。

### T13-O02（Process）：明确静态检查范围

报告应记录实际使用的命令：`ruff check src scripts tests streamlit_app.py` 与对应 format check。若要求仓库级 `ruff check .`，需另行修复或排除 `.superpowers` 中的历史证据脚本；不得把 scoped pass 写成 repo-wide pass。

### T13-O03（Process）：所有恢复演练必须 fail-fast

T14 runbook 中应使用：

```bash
.venv/bin/python scripts/reset_demo.py --snapshot initial || exit 1
.venv/bin/python scripts/validate_data.py || exit 1
```

并明确“先停止持有共享状态锁的应用进程”。验收日志必须出现 `RESET_OK` 和 `VALIDATION_OK`，不能只以最后一个 pytest 的退出码代替恢复成功证据。

## 5. T14 准入通过标准

只有以下项目全部满足，才签署“允许进入 T14”：

| # | 准入项 | 通过条件 |
| --- | --- | --- |
| 1 | T13-R01 | Query 缓存态 UI 自动测试通过，并有 1440×1024 真实浏览器截图 |
| 2 | T13-R02 | 完整成功 E2E 直接验证发布产物内容，破坏性反证可使测试失败 |
| 3 | 黄金指标 | Query 准确率 ≥90%，范围隔离 100%，关键引用 100%；Lint 召回 ≥80%，重大问题双引用 100% |
| 4 | 专项测试 | `tests/golden tests/e2e tests/security` 全部通过 |
| 5 | 全量测试 | 0 failed；领域与应用覆盖率 ≥85% |
| 6 | 连续演练 | 三次均出现 `RESET_OK`、`VALIDATION_OK` 和完整成功流通过 |
| 7 | UI 十项 | 六导航、版本层级、唯一主操作、文字状态、无嵌套、无横向滚动、实时/缓存标识、双引用、Diff、追溯主链全部有直接证据 |
| 8 | 报告一致性 | 测试报告、UI 验收报告与实际证据无矛盾，不把 mock 结果描述为真实 Dify 结果 |
| 9 | 代码质量 | scoped ruff/format、compileall、`git diff --check` 全部通过 |
| 10 | 提交状态 | 整改提交边界清晰，工作区干净，记录最终 SHA |
| 11 | 独立复核 | 无 Critical / Important 未关闭，reviewer 明确签署 T14 准入 |

## 6. 最终复核命令

```bash
.venv/bin/python -m pytest tests/golden tests/e2e tests/security -q

.venv/bin/python -m pytest -q \
  --cov=src/domain \
  --cov=src/application \
  --cov-report=term-missing \
  --cov-fail-under=85

for run_index in 1 2 3
do
  .venv/bin/python scripts/reset_demo.py --snapshot initial || exit 1
  .venv/bin/python -m pytest tests/e2e/test_full_success.py -q || exit 1
done

.venv/bin/ruff check src scripts tests streamlit_app.py
.venv/bin/ruff format --check src scripts tests streamlit_app.py
.venv/bin/python -m compileall -q src scripts tests streamlit_app.py
git diff --check
git status --short --branch
```

## 7. 准入判定

当前判定：

```text
T13 核心功能：通过
T13 数值门禁：通过
T13 安全门禁：通过
T13 可重复性：通过
T13 UI/验收证据：2 项待整改
T14 准入：暂缓
```

关闭 T13-R01、T13-R02，并按第 5、6 节完成独立复核后，可将结论更新为“准许进入 T14”。
