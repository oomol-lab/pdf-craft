# Agent 工作流

pdf-craft 是一个把扫描书籍 PDF 转换为 Markdown 或 EPUB 的 Python 库。本仓库使用 `~/.agents/skills/vibecoding` 作为通用维护工作法。本文件只记录 pdf-craft 特有的边界和按需阅读路由。

## 工作区边界

- `pdf_craft/` 是包源码。公共导入从 `pdf_craft/__init__.py` 暴露，便利入口在 `pdf_craft/functions.py`。
- `tests/` 包含轻量单元测试和小型 PDF fixture。普通代码改动默认以这些测试作为验证面。
- `docs/`、`README.md` 和 `README_zh-CN.md` 是读者/贡献者阅读的文档。不要把这些说明复制到 Agent 文档里。
- `references/` 是 Agent 面向的引用文档。只阅读当前任务需要的引用文档。
- `pdf_craft_tool/` 是未发布的本地 CLI，承载手动转换、翻译和冒烟矩阵；`scripts/` 只保留依赖源码同步辅助脚本。不要把它们当作默认开发流程。
- `analysing/`、`pdf-craft-output/`、`models-cache/`、`.venv/`、`dist/`、`build/` 和 `*.egg-info` 是生成产物或本地运行产物。

## 仅在需要时阅读

- 当需要判断模块归属、公共 API 边界或新代码应放在哪里时，阅读[架构与模块边界](references/architecture.md)。
- 当修改 PDF 提取、OCR 归一化、缓存 XML 产物、目录生成、章节生成、Markdown 渲染或 EPUB 渲染时，阅读[转换流水线](references/conversion-pipeline.md)。
- 当选择 setup、验证、worktree 行为、发布或外部依赖处理方式时，阅读[开发与 Worktree](references/development-and-worktrees.md)。
- 当准备发版、更新版本号、编写 changelog 或调整发布流程时，阅读[发版流程](references/release-workflow.md)。

## 项目特有默认规则

- 这是库项目。除非未来任务引入长期服务，否则不要启动常驻开发服务器。
- 普通验证应避免 CUDA、模型下载、网络请求和完整 PDF 转换，除非任务明确触及这些行为。
- 模型缓存和转换输出不得进入提交内容。`pdf_craft_tool` 的产物默认写入每个 worktree 自己的 `pdf-craft-output/`；只有任务明确需要 OCR 时，才考虑共享外部模型缓存。
- 纯文档任务不得修改包代码或依赖版本。
