# Development Guide

This guide is for human contributors. Agent-facing project routing lives in `AGENTS.md` and `references/`.

## Requirements

- Python >= 3.11, < 3.14 (3.11.16 recommended)
- Poetry 2.x
- Poppler, only when running PDF rendering or conversion checks
- PyTorch, installed through `doc-page-extractor[local]` for local OCR conversion
- CUDA-capable PyTorch and an NVIDIA GPU, only when running real local OCR conversion

pdf-craft depends on `doc-page-extractor[local]`, so installs include the upstream local OCR runtime stack. pdf-craft still does not declare `torch` or `torchvision` directly; reinstall or override the PyTorch wheel when you need a specific CPU or CUDA build.

## Setup For Ordinary Development

Create an in-project virtual environment and install project dependencies:

```shell
poetry config virtualenvs.in-project true
poetry install --with dev
```

For code reading, type checking, and the lightweight unit tests, this is usually enough.

If a task needs a specific CPU PyTorch wheel, reinstall it explicitly:

```shell
poetry run pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

## Setup For Real OCR Conversion

Real PDF conversion can run through either a local CUDA model or vendor OCR.

Local OCR requires CUDA-capable PyTorch. Reinstall the PyTorch build that matches your system before running conversion scripts if the installed wheel does not match your CUDA environment.

Examples:

```shell
poetry run pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu118
poetry run pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu121
poetry run pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu124
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

Vendor OCR does not require local CUDA. Copy `.env.template` to `.env`, set `PDF_CRAFT_OCR_MODE` to `deepseek-ocr-vendor`, `deepseek-ocr2-vendor`, or `unlimited-ocr-vendor`, and fill the matching `PDF_CRAFT_*` credentials. Local modes use the `PDF_CRAFT_DEEPSEEK_*` and `PDF_CRAFT_UNLIMITED_*` model-path settings. Library code does not automatically read `.env`; only the manual scripts load it.

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

The repository-local `pdf_craft_tool` CLI is the manual conversion and smoke-test entry point. It requires Poppler and an OCR configuration from `.env`. Local modes require model downloads and CUDA; vendor modes require credentials:

```shell
poetry run python -m pdf_craft_tool pdf convert tests/assets/citation.pdf --format markdown --pages 1,2,3
poetry run python -m pdf_craft_tool pdf convert tests/assets/citation.pdf --format epub
poetry run python -m pdf_craft_tool pdf translate tests/assets/citation.pdf zh --pages 1,2,3
```

Each invocation creates an isolated run directory under `analysing/manual/`, containing its `package/` and rendered output. `--pages` always uses 1-based PDF page indexes. Text LLM profiles are separate from OCR configuration; the default profile retrieves the local OOMOL connection at runtime without persisting its credential. See [`pdf_craft_tool/README.md`](../pdf_craft_tool/README.md) for the complete command and smoke-matrix reference.

## VGE Worktree Development

This repository includes `.conductor/settings.toml` for VGE worktrees. It defines setup only. There is no long-lived development server, watcher, or app process, so no `run` script is configured. There is also no cleanup/archive script; VGE is expected to release the worktree itself.

`.env` is worktree-private runtime configuration and is ignored by Git. When the current worktree does not have `.env`, VGE setup first copies the existing `.env` from the source workspace so vendor OCR credentials and local development settings remain available inside the worktree. If the source workspace has no `.env`, setup falls back to creating one from `.env.template`.

Worktree-local generated files include `.venv/`, `analysing/`, `models-cache/`, test caches, and build artifacts. Do not commit them.

## Dependency Sync Helpers

`scripts/sync-doc-page-extractor.sh` and `scripts/sync-epub-generator.sh` copy sibling repository source code into `.venv`. Use them only for deliberate local integration testing with those repositories checked out next to this one. They are not part of normal setup, CI, or VGE worktree setup.
