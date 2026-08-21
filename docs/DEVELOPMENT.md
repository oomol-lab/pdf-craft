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

Vendor OCR does not require local CUDA. Copy `.env.template` to `.env`, set `OCR_MODE` to `deepseek-ocr-vendor`, `deepseek-ocr2-vendor`, or `unlimited-ocr-vendor`, and fill the matching credentials. Local modes use `DEEPSEEK_LOCAL_MODEL_PATH` and `DEEPSEEK_LOCAL_ONLY` for DeepSeek OCR / DeepSeek OCR 2, and `UNLIMITED_LOCAL_MODEL_PATH` and `UNLIMITED_LOCAL_ONLY` for Unlimited OCR. Library code does not automatically read `.env`; only the manual scripts load it.

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

The scripts in `scripts/` are manual checks for conversion work. They require Poppler and an OCR configuration from `.env`. Local modes require model downloads and CUDA; vendor modes require credentials:

```shell
poetry run python scripts/gen_md.py
poetry run python scripts/gen_epub.py
```

They write conversion output under `analysing/` and use the configured local model path from `DEEPSEEK_LOCAL_MODEL_PATH` or `UNLIMITED_LOCAL_MODEL_PATH` when `OCR_MODE` is a local OCR mode.

If `format.json` exists at the repository root, these scripts use it to configure optional LLM-enhanced TOC analysis. The template is `format.template.json`; do not commit local secrets.

## Parameterized Smoke Matrix

The smoke runner records real conversion artifacts without treating OCR or translation quality as an automated assertion. List the available PDF and EPUB fixtures with:

```shell
poetry run python -m pdf_craft.smoke --list-assets
```

Pass a JSON file with explicit runs. `page_indexes` is forwarded to OCR, so it limits the pages that are recognized rather than trimming output later:

```json
{
  "defaults": {"page_indexes": [1], "max_ocr_tokens": 4000},
  "runs": [
    {
      "asset": "double_column.pdf",
      "route": "markdown",
      "backend": "deepseek-ocr-vendor",
      "ocr": {"base_url": "...", "api_key": "...", "model": "..."}
    },
    {"asset": "epub/Cambridge.epub", "route": "epub-check"}
  ]
}
```

Use `--dry-run` to validate and expand a matrix without network, models, or OCR. Actual results are isolated below `analysing/smoke/<run-id>/` with a manifest, package, output, logs, and structural checks. Explicit EPUB translation configuration is required for the `epub-translate` route; without it the route is recorded as skipped.

## VGE Worktree Development

This repository includes `.conductor/settings.toml` for VGE worktrees. It defines setup only. There is no long-lived development server, watcher, or app process, so no `run` script is configured. There is also no cleanup/archive script; VGE is expected to release the worktree itself.

`.env` is worktree-private runtime configuration and is ignored by Git. When the current worktree does not have `.env`, VGE setup first copies the existing `.env` from the source workspace so vendor OCR credentials and local development settings remain available inside the worktree. If the source workspace has no `.env`, setup falls back to creating one from `.env.template`.

Worktree-local generated files include `.venv/`, `analysing/`, `models-cache/`, test caches, and build artifacts. Do not commit them.

## Dependency Sync Helpers

`scripts/sync-doc-page-extractor.sh` and `scripts/sync-epub-generator.sh` copy sibling repository source code into `.venv`. Use them only for deliberate local integration testing with those repositories checked out next to this one. They are not part of normal setup, CI, or VGE worktree setup.
