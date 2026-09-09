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
- Poppler for PDF conversion, OCR extraction, and PDF patching. Patching renders
  source pages only to sample local erasure colors; it does not rasterize them into
  the output.
- Ghostscript for PDF patching. It compiles the source page into a visual-only
  layer, so the original text cannot be selected beneath translated text.
- Network and valid credentials for vendor OCR
- A CUDA-capable NVIDIA GPU, matching PyTorch, model storage, and adequate VRAM for local OCR

PySide6/Qt is installed as a Python dependency. PDF patching also needs suitable
local fonts. Vector inline-formula rendering is optional: when enabled it needs
Matplotlib plus a TeX installation whose `latex` command is available. Without either
runtime, or when one formula cannot render, patching continues with readable plain
text for that formula.

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

## Install Ghostscript for PDF patching

PDF patching requires the Ghostscript command-line executable:

```bash
# macOS
brew install ghostscript

# Debian / Ubuntu
sudo apt-get update && sudo apt-get install ghostscript
```

On Windows, install Ghostscript and add `gswin64c.exe` to `PATH`. Confirm that
`gs --version` (or `gswin64c --version`) works in the same environment that
runs Python.

## Optional: vector inline formulas in patched PDFs

`PatchTextOptions(render_inline_formulas=True)` is the default. When Matplotlib and
TeX are available, inline LaTeX is written back as a vector PDF fragment; otherwise
pdf-craft falls back to readable plain text rather than failing the PDF.

Install Matplotlib in the environment running pdf-craft and install a TeX
distribution that exposes `latex` on `PATH`. This is independent of OCR and is not
required when plain-text formula fallback is acceptable.

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
