# 开发指南

本文档面向人类贡献者。Agent 专用的项目路由在 `AGENTS.md` 和 `references/` 中维护。

## 环境要求

- Python >= 3.11, < 3.14（推荐 3.11.16）
- Poetry 2.x
- Poppler，仅在运行 PDF 渲染或转换检查时需要
- PyTorch，通过 `doc-page-extractor[local]` 安装，用于本地 OCR 转换
- 支持 CUDA 的 PyTorch 和 NVIDIA GPU，仅在运行真实本地 OCR 转换时需要

pdf-craft 依赖 `doc-page-extractor[local]`，因此安装时会包含上游本地 OCR 运行时栈。pdf-craft 仍不会直接声明 `torch` 或 `torchvision`；当你需要指定 CPU 或 CUDA 构建时，再重装或覆盖 PyTorch wheel。

## 普通开发环境

创建项目内虚拟环境并安装依赖：

```shell
poetry config virtualenvs.in-project true
poetry install --with dev
```

对于阅读代码、类型检查和轻量单元测试，通常这就足够了。

如果任务需要指定 CPU 版 PyTorch wheel，可以显式重装：

```shell
poetry run pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

## 真实 OCR 转换环境

真实 PDF 转换可以使用本地 CUDA 模型，也可以使用供应商 OCR。

本地 OCR 需要支持 CUDA 的 PyTorch。如果已安装的 wheel 与当前 CUDA 环境不匹配，运行转换脚本前请重装与系统匹配的 PyTorch 版本。

示例：

```shell
poetry run pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu118
poetry run pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu121
poetry run pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu124
```

运行 PDF 渲染或转换时还需要安装 Poppler：

```shell
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install poppler-utils

# macOS
brew install poppler
```

验证环境：

```shell
poetry run python -c "import torch; print(f'PyTorch version: {torch.__version__}'); print(f'CUDA available: {torch.cuda.is_available()}')"
pdfinfo -v
```

供应商 OCR 不需要本地 CUDA。复制 `.env.template` 为 `.env`，把 `PDF_CRAFT_OCR_MODE` 设为 `deepseek-ocr-vendor`、`deepseek-ocr2-vendor` 或 `unlimited-ocr-vendor`，再填写对应的 `PDF_CRAFT_*` 凭据。本地模式使用 `PDF_CRAFT_DEEPSEEK_*` 和 `PDF_CRAFT_UNLIMITED_*` 中的模型路径配置。库代码不会自动读取 `.env`；只有手动脚本会加载它。

## 验证

CI 检查是默认验证契约：

```shell
poetry run pyright pdf_craft tests
poetry run pylint pdf_craft tests
poetry run python test.py
```

可以传入文件 stem 或文件名，只运行一个测试模块：

```shell
poetry run python test.py test_parser
poetry run python test.py test_parser.py
```

构建发布包：

```shell
poetry build
```

## 手动转换检查

仓库内的 `pdf_craft_tool` CLI 是手动转换和冒烟测试入口。它需要 Poppler，并从 `.env` 读取 OCR 配置。本地模式需要模型下载和 CUDA；供应商模式需要对应密钥：

```shell
poetry run python -m pdf_craft_tool pdf convert tests/assets/citation.pdf --format markdown --pages 1,2,3
poetry run python -m pdf_craft_tool pdf convert tests/assets/citation.pdf --format epub
poetry run python -m pdf_craft_tool pdf translate tests/assets/citation.pdf zh --pages 1,2,3
```

每次运行都会在 `analysing/manual/` 下创建独立目录，其中包含 `package/` 和渲染结果。`--pages` 始终使用从 1 开始的 PDF 页码。文本 LLM profile 与 OCR 配置独立；默认 profile 在运行时获取本机 OOMOL 连接，不会把凭据写入文件。完整的命令和冒烟矩阵说明见 [`pdf_craft_tool/README.md`](../pdf_craft_tool/README.md)。

## VGE Worktree 开发

本仓库包含 `.conductor/settings.toml` 供 VGE worktree 使用。它只定义 setup。项目没有长期运行的开发服务、watcher 或应用进程，因此没有配置 `run` 脚本；也没有配置 cleanup/archive 脚本，由 VGE 自行释放 worktree。

`.env` 是 worktree 私有运行配置，不提交到 Git。VGE setup 在当前 worktree 缺少 `.env` 时，会优先从源工作区复制现有 `.env`，让供应商 OCR 密钥和本机开发配置在 worktree 中可用；如果源工作区没有 `.env`，才从 `.env.template` 创建空配置。

Worktree 本地产物包括 `.venv/`、`analysing/`、`models-cache/`、测试缓存和构建产物。不要提交这些文件。

## 依赖同步辅助脚本

`scripts/sync-doc-page-extractor.sh` 和 `scripts/sync-epub-generator.sh` 会把相邻仓库的源码复制进 `.venv`。只有在有意联调这些相邻仓库时才使用它们。它们不是普通 setup、CI 或 VGE worktree setup 的一部分。
