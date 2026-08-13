# Development Guide

This guide is for human contributors. Agent-facing project routing lives in `AGENTS.md` and `references/`.

## Requirements

- Python >= 3.11, < 3.14 (3.11.16 recommended)
- Poetry 2.x
- Poppler, only when running PDF rendering or conversion checks
- PyTorch, only when importing or running OCR-related dependencies
- CUDA-capable PyTorch and an NVIDIA GPU, only when running real DeepSeek OCR conversion

The published package does not depend on `torch` or `torchvision`. Install them separately for your environment.

## Setup For Ordinary Development

Create an in-project virtual environment and install project dependencies:

```shell
poetry config virtualenvs.in-project true
poetry install --with dev
```

For code reading, type checking, and the lightweight unit tests, this is usually enough.

If a task needs PyTorch imports but not CUDA OCR, install CPU PyTorch:

```shell
poetry run pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

## Setup For Real OCR Conversion

Real PDF conversion uses DeepSeek OCR and requires CUDA-capable PyTorch. Install the PyTorch build that matches your system before running conversion scripts.

Examples:

```shell
poetry run pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
poetry run pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
poetry run pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
```

Install Poppler when running PDF rendering or conversion:

```shell
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install poppler-utils

# macOS
brew install poppler
```

Verify the environment:

```shell
poetry run python -c "import torch; print(f'PyTorch version: {torch.__version__}'); print(f'CUDA available: {torch.cuda.is_available()}')"
pdfinfo -v
```

## Validation

The CI checks are the default validation contract:

```shell
poetry run pyright pdf_craft tests
poetry run pylint pdf_craft tests
poetry run python test.py
```

You can run one test module by passing the file stem or file name:

```shell
poetry run python test.py test_parser
poetry run python test.py test_parser.py
```

Build the package with:

```shell
poetry build
```

## Manual Conversion Checks

The scripts in `scripts/` are manual checks for local conversion work. They may require Poppler, PyTorch, model downloads, and CUDA:

```shell
poetry run python scripts/gen_md.py
poetry run python scripts/gen_epub.py
```

They write conversion output under `analysing/` and use `models-cache/` for local model storage.

If `format.json` exists at the repository root, these scripts use it to configure optional LLM-enhanced TOC analysis. The template is `format.template.json`; do not commit local secrets.

## VGE Worktree Development

This repository includes `.conductor/settings.toml` for VGE worktrees. It defines setup and cleanup commands only. There is no long-lived development server, watcher, or app process, so no `run` script is configured.

Worktree-local generated files include `.venv/`, `analysing/`, `models-cache/`, test caches, and build artifacts. Do not commit them.

## Dependency Sync Helpers

`scripts/sync-doc-page-extractor.sh` and `scripts/sync-epub-generator.sh` copy sibling repository source code into `.venv`. Use them only for deliberate local integration testing with those repositories checked out next to this one. They are not part of normal setup, CI, or VGE worktree setup.
