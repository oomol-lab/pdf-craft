# Installation Guide

This guide is for users of the Python library. Start with the standard package unless you deliberately want OCR models to run on your own NVIDIA GPU.

## Choose an installation

For vendor OCR, install:

```bash
python -m pip install pdf-craft
```

Vendor OCR uses a remote service, so CUDA is not required locally. You will still need the provider URL, model name, and credentials in your Python configuration.

For local OCR on a CUDA-capable NVIDIA GPU, install the local extra instead:

```bash
python -m pip install "pdf-craft[local]"
```

The extra supplies the Python runtime for local models. It does not choose a PyTorch wheel for your platform. Install a CUDA-compatible PyTorch build that matches your Python version, driver, and operating system. If you are unsure, use vendor OCR.

## Requirements

- Python `>=3.11,<3.14`
- Poppler for PDF conversion and the standard PDF translation/patch workflow
- Network and valid credentials for vendor OCR
- A CUDA-capable NVIDIA GPU, matching PyTorch, model storage, and adequate VRAM for local OCR

Using a virtual environment is recommended:

```bash
python3.11 -m venv .venv
source .venv/bin/activate  # macOS / Linux
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install pdf-craft
```

## Install Poppler

```bash
# macOS
brew install poppler

# Debian / Ubuntu
sudo apt-get update && sudo apt-get install poppler-utils
```

On Windows, install a Poppler binary distribution and add its `bin` directory to `PATH`. Alternatively, configure `DefaultPDFHandler(poppler_path="C:/tools/poppler/bin")` through `PDFOptions`.

Check the installation with `pdfinfo -v`.

## Local OCR setup

Run `nvidia-smi` to confirm that the NVIDIA driver and GPU are visible. Then use the [PyTorch installation selector](https://pytorch.org/get-started/locally/) to install an appropriate CUDA build. Confirm it with:

```bash
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

Local OCR requires `True`. Models download on first use by default. To pre-download a model and later run without downloads:

```python
from pdf_craft import DeepSeekOCRLocalConfig, PDFCraft, PDFOptions, predownload_models

predownload_models(ocr=DeepSeekOCRLocalConfig(models_cache_path="models"))
craft = PDFCraft(pdf=PDFOptions(ocr=DeepSeekOCRLocalConfig(
    models_cache_path="models", local_only=True,
)))
```

## Verify the package

```bash
python -c "import pdf_craft; print(pdf_craft.__file__)"
```

This verifies installation only; it does not download a model or call an OCR service. See the [OCR Backend Guide](OCR_BACKENDS.md) for runtime configuration and [Troubleshooting](TROUBLESHOOTING.md) for common setup failures.
