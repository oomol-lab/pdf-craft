# 开发与 Worktree

**约束范围：** setup、验证、VGE worktree 行为和发布命令。**不约束：** 包架构或转换内部逻辑。**何时阅读：** 选择命令、编辑项目配置或修改开发文档时。

## 环境

使用 Python `>=3.11,<3.14`；默认开发版本是 Python 3.11。本仓库使用 Poetry 2.x；配置 `poetry config virtualenvs.in-project true` 时，虚拟环境应位于当前 worktree 内。

普通开发安装依赖：

```bash
poetry config virtualenvs.in-project true
poetry install --with dev
```

本包通过 `doc-page-extractor[local]` 获得上游本地 OCR 运行时栈，但不直接声明 `torch` 或 `torchvision`。只有当任务需要指定 CPU 或 CUDA wheel 时，才按当前环境覆盖安装 `torch` 和 `torchvision`。

真实 OCR 有六种配置入口：

- `DeepSeekOCRLocalConfig`：本地 DeepSeek OCR，真实转换需要 CUDA。
- `DeepSeekOCR2LocalConfig`：本地 DeepSeek OCR 2，真实转换需要 CUDA。
- `UnlimitedOCRLocalConfig`：本地 Unlimited OCR，真实转换需要 CUDA。
- `DeepSeekOCRVendorConfig`：DeepSeek OCR 供应商模式。
- `DeepSeekOCR2VendorConfig`：DeepSeek OCR 2 供应商模式。
- `UnlimitedOCRVendorConfig`：Unlimited OCR 供应商模式。

库代码不得自动读取 `.env`。手动脚本通过 `scripts/runtime.py` 加载工作区 `.env` 并创建显式配置对象。所有手动运行时变量使用 `PDF_CRAFT_` 前缀：OCR 使用 `PDF_CRAFT_OCR_MODE` 和对应的 `PDF_CRAFT_*_OCR_*` 配置；PDF 翻译使用独立的 `PDF_CRAFT_TRANSLATION_*` 文本 LLM 配置，不能复用 OCR-only endpoint。

## 默认验证

优先选择能跨越改动边界的最小命令集：

```bash
poetry run pyright pdf_craft tests
poetry run pylint pdf_craft tests
poetry run python test.py
```

`poetry build` 用于验证打包。`scripts/` 中的完整转换脚本是手动检查，可能需要 Poppler、PyTorch、模型缓存和 CUDA。

## VGE Worktree 行为

本项目没有长期运行的开发服务，因此 `.conductor/settings.toml` 不应定义 `scripts.run`，除非未来项目新增 watcher 或 server。VGE setup 同时负责安装依赖并初始化当前 worktree 的 `.env`，确保当前 worktree 中的 pdf-craft 及其 `doc-page-extractor` 依赖可以被导入和测试。

`.env` 是 worktree 私有运行配置，不提交到 Git。VGE setup 在当前 worktree 缺少 `.env` 时，应优先从源工作区的 `.env` 复制，以便继承供应商 OCR 密钥和本机开发配置；只有源工作区也没有 `.env` 时，才从 `.env.template` 创建空配置。

默认不配置 `scripts.archive`。VGE 会负责释放 worktree；项目级 Cleanup 只适合需要保留现场后执行额外归档或收尾动作的项目，本仓库当前没有这种需求。

生成的转换输出应留在当前 worktree 的 `analysing/` 下。模型缓存可能很大；只有在有意进行本地 OCR 工作时才使用 `models-cache/`，不要提交它，也不要假设其中已有内容。

## 人类阅读文档

面向人类贡献者的开发说明位于 `docs/DEVELOPMENT.md` 和 `docs/DEVELOPMENT_zh-CN.md`。README 文件应聚焦库使用者。Agent 面向的路由和项目约束保留在 `AGENTS.md` 和 `references/` 中。

## 发布

发布说明位于 `docs/RELEASE.md`。除非任务明确要求发布或升级依赖，否则普通 worktree 支持改造不应修改包版本、依赖 pin 或发布元数据。
