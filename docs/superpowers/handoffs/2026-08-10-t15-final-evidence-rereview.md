# T15 最终补证复核报告

> 复核日期：2026-08-10（America/Los_Angeles）  
> 复核提交：`8da23991d4ae8c2c980250cf0ac28ba6811de756`  
> 当前冻结标签：`v0.2.1-live-demo` → `8aa5d45b8a894dd5b62779c481ce4148d795056c`  
> 复核范围：T15-R04-E01 远端 run ID 证据、T15-O01 备用视频证据、最终门禁

## 1. 结论

**T15-R04-E01 已通过；T15-O01 尚未补齐，正式冻结签署仍暂缓。**

本轮新增的 Dify 证据已满足上一轮要求：Ingest、Query 和十次 Lint 均有远端 `workflow_run_id`，应用侧 `CALL-...` 已正确区分，十次 Lint 的时间与耗时和原性能样本逐条完全一致。

但是 `docs/demo/backup-video-evidence.md` 仍明确标记“待现场负责人补证”，文件路径、SHA-256、时长/分辨率、录制基线、试播时间、负责人和三项试播结论全部为空。因此这次提交只是创建补证模板，不是完成视频补证。

此外，`v0.2.1-live-demo` 仍指向补证前的 `8aa5d45`，不包含当前 `8da2399` 的 run ID 证据和视频模板。现有标签不应强制移动；待视频真正补齐后应创建新的不可变补丁标签。

```text
T15-R01：通过
T15-R02：通过
T15-R03：通过
T15-R04-E01：通过
T15-O01 备用视频：不通过（仍为待填模板）
正式标签包含最终证据：不通过
T15 正式冻结签署：暂缓
```

## 2. T15-R04-E01 复核证据

`docs/qa/evidence/t15-live-smoke-2026-08-10.json` 包含：

- Ingest：1 条远端 UUID，另有明确标记的应用侧 `CALL-...` ID；
- Query：1 条远端 UUID；
- Lint：10 条互异远端 UUID；
- reviewer 独立 Lint：1 条远端 UUID，绑定 `8aa5d45`；
- 每条调用只保留 Workflow、时间、耗时、成功状态、公开错误码和两类 ID，不含 Key、请求正文或未脱敏材料。

独立交叉核对：

```text
smoke Lint 样本数：10
performance remediation 样本数：10
called_at + seconds 逐条完全匹配：true
smoke_only：[]
performance_only：[]
```

文档已修正：

- Ingest 的 `dbee74a8-...` 标为远端 `workflow_run_id`；
- `CALL-39B...` 标为应用模型调用 ID，不再混写；
- Query 远端 ID已归档；
- 十次 Lint 远端 ID通过独立 JSON 引用归档。

T15-R04-E01 可以关闭，无需再次执行真实 Dify 采样。

## 3. 新鲜门禁结果

### 3.1 证据专项

```text
pytest tests/unit/test_t15_live_smoke_evidence.py \
       tests/unit/test_t15_performance_evidence.py -q
→ 7 passed
```

### 3.2 全量与覆盖率

```text
coverage run -m pytest
→ 781 passed in 25.73s

coverage report --include='src/domain/*,src/application/*'
→ TOTAL 2597 statements, 119 missed, 95%
```

### 3.3 静态门禁

```text
ruff check
→ All checks passed!

ruff format --check
→ 172 files already formatted

compileall
→ passed

git diff --check
→ passed

复核开始时工作区
→ clean
```

## 4. T15-O01 未通过证据

`docs/demo/backup-video-evidence.md` 当前内容：

```text
状态：待现场负责人补证
演示设备上的文件名/受控路径：空
文件 SHA-256：空
视频时长与分辨率：空
录制基线版本：空
最近一次完整试播时间：空
试播负责人：空
断网试播：待填
音画正常：待填
分辨率匹配：待填
```

预检清单也明确规定：记录完成前，第 8 项不得勾为通过。因此不能把“新增模板”等同于“已补齐视频证据”。

## 5. 最小剩余动作与通过标准

不需要修改业务代码，也不需要重复 Dify 十次采样。只需完成：

1. 在正式演示设备预置可离线播放的备用视频。
2. 完整断网试播一次。
3. 在 `docs/demo/backup-video-evidence.md` 填写：
   - 文件名或受控路径；
   - SHA-256；
   - 时长和分辨率；
   - 录制所对应的代码 SHA 和快照；
   - 含时区的试播时间；
   - 负责人；
   - 断网、音画、分辨率三项均为“通过”。
4. 提交填写后的记录，确保工作区 clean。
5. 不移动既有 `v0.2.1-live-demo`；在最终证据提交上创建新的 annotated tag，建议 `v0.2.2-live-demo`。
6. 复核视频记录字段、SHA 格式、标签指向和 `git diff --check`。

最终通过标准：

- [ ] 视频证据表无空字段、无“待填”。
- [ ] 文件 SHA-256 为 64 位小写十六进制，并与演示设备实际文件一致。
- [ ] 三项试播均通过，时间和负责人明确。
- [ ] 最终 tag 指向包含 run ID 和视频证据的提交。
- [ ] 工作区 clean，静态门禁保持通过。

满足以上五项后，可签署 T15 正式冻结；无需再次修改或复验核心代码。
