# 开发指南

本文档面向人类贡献者。Agent 专用的项目路由在 `AGENTS.md` 和 `references/` 中维护。

## 环境要求

- Python >= 3.11, < 3.14（推荐 3.11.16）
- Poetry 2.x
- Poppler，仅在运行 PDF 渲染或转换检查时需要
- PyTorch，仅在需要导入或运行 OCR 相关依赖时需要
- 支持 CUDA 的 PyTorch 和 NVIDIA GPU，仅在运行真实 DeepSeek OCR 转换时需要

发布包不会依赖 `torch` 或 `torchvision`。请根据本机环境单独安装。

## 普通开发环境

创建项目内虚拟环境并安装依赖：

```shell
poetry config virtualenvs.in-project true
poetry install --with dev
```

对于阅读代码、类型检查和轻量单元测试，通常这就足够了。

如果任务需要 PyTorch import，但不需要 CUDA OCR，可以安装 CPU 版 PyTorch：

```shell
poetry run pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

## 真实 OCR 转换环境

真实 PDF 转换使用 DeepSeek OCR，需要支持 CUDA 的 PyTorch。运行转换脚本前，请安装与系统匹配的 PyTorch 版本。

示例：

```shell
poetry run pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
poetry run pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
poetry run pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
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

`scripts/` 中的脚本用于本地转换联调。它们可能需要 Poppler、PyTorch、模型下载和 CUDA：

```shell
poetry run python scripts/gen_md.py
poetry run python scripts/gen_epub.py
```

脚本会把转换结果写入 `analysing/`，并使用 `models-cache/` 存放本地模型。

如果仓库根目录存在 `format.json`，脚本会用它配置可选的 LLM 增强目录分析。模板是 `format.template.json`；不要提交本地密钥。

## VGE Worktree 开发

本仓库包含 `.conductor/settings.toml` 供 VGE worktree 使用。它只定义 setup。项目没有长期运行的开发服务、watcher 或应用进程，因此没有配置 `run` 脚本；也没有配置 cleanup/archive 脚本，由 VGE 自行释放 worktree。

Worktree 本地产物包括 `.venv/`、`analysing/`、`models-cache/`、测试缓存和构建产物。不要提交这些文件。

## 依赖同步辅助脚本

`scripts/sync-doc-page-extractor.sh` 和 `scripts/sync-epub-generator.sh` 会把相邻仓库的源码复制进 `.venv`。只有在有意联调这些相邻仓库时才使用它们。它们不是普通 setup、CI 或 VGE worktree setup 的一部分。
