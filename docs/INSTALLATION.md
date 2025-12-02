# Installation Guide

## System Requirements

- Python >= 3.10, < 3.14 (3.11.16 recommended)
- NVIDIA GPU with CUDA 11.8 or 12.1 support
- 16 GB or more VRAM (24 GB or higher recommended, see [DeepSeek OCR Hardware Requirements Discussion](https://huggingface.co/deepseek-ai/DeepSeek-OCR/discussions/31))

## Installation Steps

This project uses DeepSeek OCR for document recognition, which **must run in a CUDA environment**. If you need to actually use pdf-craft for PDF conversion, please follow the CUDA environment installation steps below.

If you only need to develop code, get IDE type hints, or read the source code, you can choose the CPU environment installation as an alternative, but it will not be able to perform actual OCR recognition.

### CUDA Environment Installation (Recommended)

#### 1. Configure CUDA Environment

Ensure that NVIDIA drivers and CUDA are installed. Check the CUDA version:

```bash
nvidia-smi
```

#### 2. Install PyTorch

Choose the appropriate installation command based on your operating system and CUDA version.

Please visit the [PyTorch official installation page](https://pytorch.org/get-started/locally/) to select the corresponding configuration and install PyTorch.

**Example** (CUDA 12.1):

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

#### 3. Install pdf-craft

```bash
pip install pdf-craft
```

#### 4. Verify Installation

```bash
python -c "import torch; print('CUDA available:', torch.cuda.is_available())"
```

Should output `CUDA available: True`

### CPU Environment Installation (Development Only)

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install pdf-craft
```

## Troubleshooting

### CUDA Not Available Error

When you try to use pdf-craft, if you see a RuntimeWarning similar to the following:

```
CUDA is not available! This package requires CUDA to run,
but torch.cuda.is_available() returned False.
```

This indicates that the CUDA environment is not properly configured. Possible reasons:

1. **Installed the CPU version of PyTorch** - Need to reinstall PyTorch with CUDA support following the CUDA environment installation steps above
2. **NVIDIA driver is outdated or not installed** - Visit [NVIDIA Driver Download Page](https://www.nvidia.com/download/index.aspx) to update drivers
3. **No CUDA-compatible GPU** - This project must run on NVIDIA GPUs

You can run the `nvidia-smi` command to check your system's GPU and driver status.

### How to Choose CUDA Version

1. Run `nvidia-smi` and check the CUDA Version in the upper right corner
2. Visit the [PyTorch official website](https://pytorch.org/get-started/locally/) to select the corresponding or lower CUDA version
3. Usually CUDA 12.1 or 11.8 have the best compatibility

### Dependency Conflicts

If you encounter dependency version conflicts, it is recommended to use a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# Then follow the CUDA environment installation steps above
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
pip install pdf-craft
```
