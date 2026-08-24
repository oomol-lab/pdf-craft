# Development Guide

This guide is for human contributors. Agent-facing project routing lives in `AGENTS.md` and `references/`.

## Requirements

- Python >= 3.11, < 3.14 (3.11.16 recommended)
- Poetry 2.x
- Poppler, only when running PDF rendering or conversion checks
- CUDA-capable PyTorch and an NVIDIA GPU, only when running real local OCR conversion

Ordinary installs use the vendor-capable base `doc-page-extractor` runtime. The
optional `local` extra adds the upstream Hugging Face local OCR runtime.
pdf-craft does not declare `torch` or `torchvision` directly; install or
override the PyTorch wheel for the CUDA build you need before enabling local OCR.

## Setup For Ordinary Development

Create an in-project virtual environment and install project dependencies:

```shell
PYTHON_BIN="$(command -v python3.11 || pyenv which python3 2>/dev/null || command -v python3)"
"$PYTHON_BIN" - <<'PY'
import sys
if not ((3, 11) <= sys.version_info < (3, 14)):
    raise SystemExit(f"Python >=3.11,<3.14 is required, got {sys.version.split()[0]}")
PY

"$PYTHON_BIN" -m venv .venv
export VIRTUAL_ENV="$PWD/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
poetry config virtualenvs.in-project true
poetry install --with dev
```

The project supports Python 3.11 through 3.13. When reusing an existing
`.venv`, verify its interpreter first; an environment created with Python 3.14
is not valid for this project and must be recreated with a supported Python.

For code reading, type checking, and the lightweight unit tests, this is usually enough.

## Setup For Real OCR Conversion

Real PDF conversion can run through either a local CUDA model or vendor OCR.

Local OCR requires the optional runtime and CUDA-capable PyTorch. Install the
project extra, then install or reinstall the PyTorch build that matches your system.

Examples:

```shell
poetry install --with dev --extras local
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

Verify a local OCR environment:

```shell
poetry run python -c "import torch; print(f'PyTorch version: {torch.__version__}'); print(f'CUDA available: {torch.cuda.is_available()}')"
pdfinfo -v
```

Vendor OCR does not require local CUDA. Copy `.env.template` to `.env` and fill every backend's configuration group once. `PDF_CRAFT_OCR_MODE` selects only the default backend; CLI and smoke can select any of the six modes per run without editing `.env`. The three local modes each have independent `*_MODELS_CACHE_PATH`, `*_LOCAL_ONLY`, and optional `*_ENABLE_DEVICES_NUMBERS` settings; vendor modes each have their own credentials and endpoint settings. Library code does not automatically read `.env`; only the manual scripts load it.

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

Each invocation creates an isolated run directory under the Git-ignored `pdf-craft-output/manual/`, with a date-and-sequence suffix, containing its `package/` and rendered output. `--work-dir` and the smoke runner's `--output-root` override those defaults. `--pages` always uses 1-based PDF page indexes. Text LLM profiles are separate from OCR configuration; the default profile retrieves the local OOMOL connection at runtime without persisting its credential. See [`pdf_craft_tool/README.md`](../pdf_craft_tool/README.md) for the complete command and smoke-matrix reference.

## VGE Worktree Development

This repository includes `.conductor/settings.toml` for VGE worktrees. It defines setup only. There is no long-lived development server, watcher, or app process, so no `run` script is configured. There is also no cleanup/archive script; VGE is expected to release the worktree itself.

`.env` is worktree-private runtime configuration and is ignored by Git. When the current worktree does not have `.env`, VGE setup first copies the existing `.env` from the source workspace so vendor OCR credentials and local development settings remain available inside the worktree. If the source workspace has no `.env`, setup falls back to creating one from `.env.template`.

Worktree-local generated files include `.venv/`, `analysing/`, `pdf-craft-output/`, `models-cache/`, test caches, and build artifacts. Do not commit them.

## Dependency Sync Helpers

`scripts/sync-doc-page-extractor.sh` and `scripts/sync-epub-generator.sh` copy sibling repository source code into `.venv`. Use them only for deliberate local integration testing with those repositories checked out next to this one. They are not part of normal setup, CI, or VGE worktree setup.
