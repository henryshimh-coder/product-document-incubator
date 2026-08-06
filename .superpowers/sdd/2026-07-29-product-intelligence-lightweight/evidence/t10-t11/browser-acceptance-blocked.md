# T10 浏览器验收环境阻塞记录（2026-08-04）

- 阻塞项：v2 修改建议 T10-4 清单中的「1440x1024 发布成功/失败/重试真实浏览器通过」与「390x844 无横向溢出」。
- 现象：WebBridge 守护进程在线（v1.11.3，`kimi-webbridge start` 正常响应），但浏览器扩展未连接。
- 证据命令与返回：
  - `curl -X POST http://127.0.0.1:10086/command -d '{"action":"list_tabs",...}'` → `{"ok":false,"error":{"code":"tool_error","message":"no extension connected"}}`（2026-08-03 与 2026-08-04 各两次，结果一致）。
  - `~/.kimi-webbridge/bin/kimi-webbridge start` 正常返回（守护进程无异常）。
- 结论：阻塞只能由用户侧恢复（打开浏览器并启用/连接 Kimi WebBridge 扩展，https://www.kimi.com/zh-cn/features/webbridge ）。未伪造任何浏览器证据。
- 期间替代覆盖：发布成功/失败/重试、双列与窄屏布局、唯一主按钮、确认弹窗由 tests/e2e/test_release_flow.py AppTest 在真实渲染管线覆盖（含在全量 569 passed 内）。
- 待扩展恢复后执行：1440x1024 发布成功/失败/重试 + 390x844 无横向溢出与弹窗可操作性，证据截图存入本目录。

# T11 浏览器验收环境阻塞记录（2026-08-04 追加）

- 阻塞项：v2 修改建议 T11-6 清单中的「1440x1024 六节点一屏可读」与「390x844 按顺序纵向堆叠，无溢出」。
- 现象：与 T10 相同。WebBridge 守护进程在线，浏览器扩展仍未连接。
- 证据命令与返回：
  - `curl -X POST http://127.0.0.1:10086/command -H 'Content-Type: application/json' -d '{"action":"list_tabs","args":{},"session":"t11-acceptance"}'` → `{"ok":false,"error":{"code":"tool_error","message":"no extension connected"}}`（2026-08-04，T11 验收前再次尝试）。
- 结论：仍为用户侧环境阻塞，未伪造任何浏览器证据。
- 期间替代覆盖：六节点链渲染、引用不可验证徽标、脱敏原文片段 caption、市场证据缺口提示、沙箱成本表单与免责声明、发布页到追溯页 target_card_id 预定位，均由 tests/e2e/test_trace_page.py 与 tests/e2e/test_release_flow.py AppTest 真实渲染管线覆盖（含在全量 647 passed 内）；六节点关系链本身由联合验收 14 步脚本在真实 Use Case 上验证（joint-acceptance.log）。
- 待扩展恢复后执行：1440x1024 追溯页六节点一屏可读 + 390x844 纵向堆叠无溢出，证据截图存入本目录。

---

## 2026-08-05 阻塞已解除（历史记录保留在上方，不删除）

- 解除方式：官方 Kimi WebBridge 扩展 v1.11.5 经 Chrome External Extensions 机制从官方商店自动安装进隔离 profile（`--user-data-dir=/tmp/wb-profile --remote-debugging-port=9223`，不触碰日常 Chrome），与本机守护进程 `127.0.0.1:10086` 建立连接；navigate/evaluate/click/cdp 全链路实测可用。
- 正式证据入口：`browser/` 目录（双视口截图 6 张 + `browser/browser-acceptance.md` 测量与结论）。
- v3 轮验收提交：`b3845d5`（fix: close publish source integrity and trace verification gaps），2026-08-05 完成双视口验收。
- v4 轮最终换证提交：`35ea4cf`（v4 最终代码提交；含 `2b02b56` 证据元数据绑定与 `35ea4cf` 任务 ID 抖动修复），2026-08-05 以最终代码重新完成双视口验收，`browser/browser-acceptance.md` 中的验收 SHA 已更新为该提交。
- 最新结论：1440x1024 与 390x844 均无横向溢出；发布确认弹窗完整在移动视口内；篡改归档触发 PUBLISH_SOURCE_INTEGRITY_FAILED 且可安全回退重试；追溯六节点双视口有序无溢出；控制台无 error/warn。原阻塞项全部关闭。
