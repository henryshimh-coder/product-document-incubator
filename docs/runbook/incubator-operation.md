# 产品文档孵化器 2.0 操作手册

## 首次设置

启动应用后进入“项目中心”，填写 Owner 姓名和本地项目库路径。默认项目库为 `~/Documents/产品文档孵化器项目库/`。设置会保存 Owner、当前项目和中央 SQLite 状态，不需要登录。

```bash
.venv/bin/streamlit run streamlit_app.py
```

## 日常流程

1. 在“项目中心”新建项目；系统自动生成 `raw/`、`wiki/`、`schema/`、`exports/` 和 `.incubator/`。
2. 进入项目后，在“原始材料”填写本地文件路径并归档。原始材料会复制到 `raw/{年}/{来源ID}/`，并校验 SHA-256。
3. 在“文档孵化”选择已脱敏、允许外部模型调用的材料，生成候选 Markdown；可编辑并提交 Owner 审核。
4. Owner 确认发布后，候选成为 `wiki/current/当前产品方案.md`，不可变版本保存在 `wiki/versions/`。
5. 在“当前产品”阅读、查询并下载唯一的当前 `.md` 附件。
6. 在“检查与建议”可运行基础自检，并勾选其他项目生成标题结构建议。采纳建议只在下一次孵化时加入候选章节，不直接改写当前版本。

## 迁移 1.x LLD

先演练，确认不写入任何文件：

```bash
.venv/bin/python scripts/migrate_lld_to_v2.py \
  --source-root . \
  --library-root "$HOME/Documents/产品文档孵化器项目库" \
  --dry-run
```

正式迁移只复制 LLD 的当前生效 Markdown、规则卡、来源归档和必要数据库镜像；1.x 快照、缓存、问题、变更和模型日志不改写也不迁移。重复执行且哈希一致会输出 `ALREADY_MIGRATED`。

## 校验与恢复

```bash
.venv/bin/python scripts/validate_incubator.py \
  --library-root "$HOME/Documents/产品文档孵化器项目库"
```

成功时输出 `INCUBATOR_VALIDATION_OK`。若失败，不要手动修改 `raw/` 或 Manifest：先保留项目目录，依据错误码恢复当前版本镜像或从备份重新迁移。

## 备份

关闭应用后，备份整个项目库（包含 `.incubator/product_incubator.db` 和每个项目目录）。`raw/` 为不可变材料，应与数据库和 `source-index.json` 一起备份。
