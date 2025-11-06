# Development Guide

## Setup

Setup Conda
```shell
conda create -p ./env python=3.11.14
conda activate ./env
```

Install dependencies:

```shell
poetry install
```

### macOS Development Setup

For macOS developers, PyTorch CUDA version is not compatible. Use the following steps:

```shell
# Install only main dependencies first (skip dev group to avoid CUDA installation)
poetry install --without dev

# Install PyTorch CPU version
poetry run pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Install other dev dependencies (pylint, etc.)
poetry install --only dev
```

Or simply install everything and then override:

```shell
poetry install
poetry run pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
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
