# Development Guide

## Setup

### 1. Create Python Environment

Setup Conda (recommended):
```shell
conda create -p ./.conda python=3.11
conda activate ./.conda
```

Or use your preferred Python environment manager (venv, pyenv, etc.)

### 2. Install Dependencies

#### Standard Installation (macOS / Linux without GPU)

Simply run:
```shell
poetry install
```

This installs:
- ✅ Main dependencies (PyMuPDF, doc-page-extractor, epub-generator)
- ✅ PyTorch CPU version (torch, torchvision)
- ✅ Dev dependencies (pylint)
- ❌ GPU-only dependencies (flash-attn is NOT installed)

#### GPU Development Setup (Linux with CUDA)

If you have a CUDA-enabled GPU and want to test GPU features:

**CUDA 12.1 (recommended):**
```shell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
poetry install --extras gpu
```

**CUDA 11.8:**
```shell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
poetry install --extras gpu
```

**CUDA 12.4:**
```shell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
poetry install --extras gpu
```

This installs everything including flash-attn for GPU acceleration.

### 3. Verify Installation

Check if PyTorch is correctly installed:
```shell
python -c "import torch; print(f'PyTorch version: {torch.__version__}'); print(f'CUDA available: {torch.cuda.is_available()}')"
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
poetry run pylint doc_page_extractor
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
