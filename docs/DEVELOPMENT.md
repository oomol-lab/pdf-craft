# Development Guide

## Setup

### 0. System Dependencies (WSL/Linux)

If you're developing on WSL or Linux, install poppler-utils first:

```shell
sudo apt-get update
sudo apt-get install poppler-utils
```

Verify installation:
```shell
pdfinfo --version
```

### 1. Create Python Environment

Setup Python env
```shell
python -m venv .venv
. ./.venv/bin/activate
```

### 2. Install Dependencies

#### Option 1: Quick Start (CPU Environment)

For quick development setup on macOS or Linux without GPU:

```shell
poetry run pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

This will install:
- Main dependencies (PyMuPDF, doc-page-extractor, epub-generator)
- PyTorch CPU version (torch, torchvision)
- Dev dependencies (pylint)

#### Option 2: CUDA Environment (Manual Setup)

For CUDA environments, you need to install PyTorch manually first to ensure the correct CUDA version.

##### Step 1: Install PyTorch with CUDA

For CUDA 11.8:
```shell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
```

For CUDA 12.1:
```shell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

For CUDA 12.4:
```shell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
```

##### Step 2: Install Project Dependencies

```shell
poetry install
```

This will install:
- Main dependencies (PyMuPDF, doc-page-extractor, epub-generator)
- Dev dependencies (pylint)

**Why manual setup for CUDA?**
- Poetry cannot handle multiple PyTorch sources in one lock file
- Different CUDA versions require different PyTorch builds
- Installing PyTorch first ensures the correct CUDA version for your hardware

### 3. Verify Installation

Check if PyTorch is correctly installed:

```shell
python -c "import torch; print(f'PyTorch version: {torch.__version__}'); print(f'CUDA available: {torch.cuda.is_available()}')"
```

Expected output for CPU environment:
```
PyTorch version: 2.5.x+cpu
CUDA available: False
```

Expected output for CUDA environment:
```
PyTorch version: 2.5.x+cu121
CUDA available: True
```

## Development Workflow

### Run Tests

```shell
poetry run python test.py
```

### Run Lint

Check code quality with pylint:

```shell
python lint.py
```

Or directly:

```shell
poetry run pylint pdf_craft
```

### Build Package

Clean old builds and create distribution files:

```shell
python build.py
```

## Before Submitting PR

Make sure all checks pass:

```shell
poetry run python test.py
python lint.py
```

## Notes

- The published package does NOT include torch/torchvision as dependencies
- End users must install torch/torchvision separately based on their environment
- For development, always install PyTorch BEFORE running `poetry install`
