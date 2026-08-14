# 发版流程

**约束范围：** 发版准备、版本号对齐、变更日志要求，以及手动 GitHub Actions 发版工作流所需的仓库状态。**不约束：** 转换内部逻辑、与发版无关的依赖升级，以及手动发布 PyPI。

## 发版模型

发版由手动触发的 GitHub Actions 发版工作流执行。在运行该工作流之前，仓库必须先完成准备。该工作流是发版关口：它会检查请求发版的版本号是否与仓库状态一致，发布包到 PyPI，推送版本标签，并使用变更日志文件作为发布说明创建 GitHub Release。

Agent 的工作应聚焦于准备仓库内的发版前置条件。发版准备过程中，不要手动发布 PyPI，不要手动创建 release tag，也不要手动创建 GitHub Release。

## 准备发版

对于目标版本 `X.Y.Z`，准备仓库时要确保以下事实成立：

- [pyproject.toml](../pyproject.toml) 中有 `tool.poetry.version = "X.Y.Z"`。
- `docs/changelog/vX.Y.Z.md` 存在，并包含发布说明。
- 变更日志应包含简洁摘要；在有帮助时按类别组织变更；在可取得时链接相关 PR 或 issue；并包含完整变更日志的 compare 链接。
- 发版准备变更应先通过普通 PR 流程审查，再从 `main` 运行手动发版工作流。

发版准备 PR 应保持聚焦。不要把发版准备和无关重构或依赖变更混在一起，除非该版本本身就是围绕这些变更发布。

## 验证

发版准备使用常规轻量验证面：

```bash
poetry run pyright pdf_craft tests
poetry run pylint pdf_craft tests
poetry run python test.py
poetry build
```

如果某个验证步骤无法在本地运行，应在交接说明中明确写出。

## 变更日志说明

发版工作流会直接读取 `docs/changelog/vX.Y.Z.md` 作为 GitHub Release 正文。变更日志文件应写成面向读者的发布说明，而不是内部实现说明。

使用 `docs/changelog/` 中已有变更日志文件作为风格参考。发布标题和版本标签使用 `vX.Y.Z` 形式。
