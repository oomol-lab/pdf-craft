# 开发指南

本文档面向人类贡献者。Agent 专用的项目路由在 `AGENTS.md` 和 `references/` 中维护。

## 环境要求

- Python >= 3.11, < 3.14（推荐 3.11.16）
- Poetry 2.x
- Poppler，仅在运行 PDF 渲染或转换检查时需要
- 支持 CUDA 的 PyTorch 和 NVIDIA GPU，仅在运行真实本地 OCR 转换时需要

普通安装使用支持供应商 OCR 的基础 `doc-page-extractor` 运行时。可选的 `local`
extra 才会安装上游 Hugging Face 本地 OCR 运行时。pdf-craft 不会直接声明 `torch` 或
`torchvision`；启用本地 OCR 前，请安装或覆盖为所需的 CUDA PyTorch wheel。

## 普通开发环境

创建项目内虚拟环境并安装依赖：

```shell
poetry config virtualenvs.in-project true
poetry install --with dev
```

对于阅读代码、类型检查和轻量单元测试，通常这就足够了。

## 真实 OCR 转换环境

真实 PDF 转换可以使用本地 CUDA 模型，也可以使用供应商 OCR。

本地 OCR 需要可选运行时和支持 CUDA 的 PyTorch。先安装项目 extra，再安装或重装与系统
匹配的 PyTorch wheel。

示例：

```shell
poetry install --with dev --extras local
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

验证本地 OCR 环境：

```shell
poetry run python -c "import torch; print(f'PyTorch version: {torch.__version__}'); print(f'CUDA available: {torch.cuda.is_available()}')"
pdfinfo -v
```

供应商 OCR 不需要本地 CUDA。复制 `.env.template` 为 `.env` 后，一次性填写全部 backend 配置分组。`PDF_CRAFT_OCR_MODE` 只选择默认 backend；CLI 和 smoke 可以在每次运行时选择六种 mode 中的任意一种，无需改写 `.env`。三种本地 mode 分别使用独立的 `*_MODELS_CACHE_PATH`、`*_LOCAL_ONLY` 和可选的 `*_ENABLE_DEVICES_NUMBERS` 设置；供应商 mode 各自使用独立的凭据和 endpoint。库代码不会自动读取 `.env`；只有手动脚本会加载它。

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

每次运行都会在 Git 忽略的 `pdf-craft-output/manual/` 下创建带日期和序号后缀的独立目录，其中包含 `package/` 和渲染结果。`--work-dir` 和冒烟执行器的 `--output-root` 可覆盖默认目录。`--pages` 始终使用从 1 开始的 PDF 页码。文本 LLM profile 与 OCR 配置独立；默认 profile 在运行时获取本机 OOMOL 连接，不会把凭据写入文件。完整的命令和冒烟矩阵说明见 [`pdf_craft_tool/README.md`](../pdf_craft_tool/README.md)。

## VGE Worktree 开发

本仓库包含 `.conductor/settings.toml` 供 VGE worktree 使用。它只定义 setup。项目没有长期运行的开发服务、watcher 或应用进程，因此没有配置 `run` 脚本；也没有配置 cleanup/archive 脚本，由 VGE 自行释放 worktree。

`.env` 是 worktree 私有运行配置，不提交到 Git。VGE setup 在当前 worktree 缺少 `.env` 时，会优先从源工作区复制现有 `.env`，让供应商 OCR 密钥和本机开发配置在 worktree 中可用；如果源工作区没有 `.env`，才从 `.env.template` 创建空配置。

Worktree 本地产物包括 `.venv/`、`analysing/`、`pdf-craft-output/`、`models-cache/`、测试缓存和构建产物。不要提交这些文件。

## 依赖同步辅助脚本

`scripts/sync-doc-page-extractor.sh` 和 `scripts/sync-epub-generator.sh` 会把相邻仓库的源码复制进 `.venv`。只有在有意联调这些相邻仓库时才使用它们。它们不是普通 setup、CI 或 VGE worktree setup 的一部分。
