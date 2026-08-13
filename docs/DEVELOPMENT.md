# Development Guide

This guide is for human contributors. Agent-facing project routing lives in `AGENTS.md` and `references/`.

## Requirements

- Python >= 3.11, < 3.14 (3.11.16 recommended)
- Poetry 2.x
- Poppler, only when running PDF rendering or conversion checks
- PyTorch, installed from the lock file through `doc-page-extractor`
- CUDA-capable PyTorch and an NVIDIA GPU, only when running real DeepSeek OCR conversion

The published `pdf-craft` package does not declare `torch` or `torchvision` directly, but the development lock file currently installs `torch` through `doc-page-extractor`. Override the PyTorch wheel only when you need a specific CPU or CUDA build.

## Setup For Ordinary Development

Create an in-project virtual environment and install project dependencies:

```shell
poetry config virtualenvs.in-project true
poetry install --with dev
```

For code reading, type checking, and the lightweight unit tests, this is usually enough.

If a task needs a specific CPU PyTorch wheel, reinstall it explicitly:

```shell
poetry run pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

## Setup For Real OCR Conversion

Real PDF conversion can run through either a local CUDA model or vendor OCR.

Local DeepSeek OCR requires CUDA-capable PyTorch. If the default locked wheel is not the CUDA build you need, reinstall the PyTorch build that matches your system before running conversion scripts.

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

Vendor OCR does not require local CUDA. Copy `.env.template` to `.env`, set `PDF_CRAFT_OCR_MODE` to `vendor-deepseek` or `vendor-unlimited`, and fill the matching credentials. Library code does not automatically read `.env`; the manual scripts load it before calling `create_ocr_config_from_env()`.

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

The scripts in `scripts/` are manual checks for conversion work. They require Poppler and an OCR configuration from `.env`. `local-deepseek` requires model downloads and CUDA; vendor modes require credentials:

```shell
poetry run python scripts/gen_md.py
poetry run python scripts/gen_epub.py
```

They write conversion output under `analysing/` and use `models-cache/` for local model storage when `PDF_CRAFT_OCR_MODE=local-deepseek`.

If `format.json` exists at the repository root, these scripts use it to configure optional LLM-enhanced TOC analysis. The template is `format.template.json`; do not commit local secrets.

## VGE Worktree Development

This repository includes `.conductor/settings.toml` for VGE worktrees. It defines setup only and creates `.env` from `.env.template` when missing. There is no long-lived development server, watcher, or app process, so no `run` script is configured. There is also no cleanup/archive script; VGE is expected to release the worktree itself.

Worktree-local generated files include `.venv/`, `analysing/`, `models-cache/`, test caches, and build artifacts. Do not commit them.

## Dependency Sync Helpers

`scripts/sync-doc-page-extractor.sh` and `scripts/sync-epub-generator.sh` copy sibling repository source code into `.venv`. Use them only for deliberate local integration testing with those repositories checked out next to this one. They are not part of normal setup, CI, or VGE worktree setup.
