# 安装指南

## 系统要求

- Python >= 3.10, < 3.14（推荐 3.11.16）
- Poppler（用于 PDF 解析和渲染）
- NVIDIA GPU，支持 CUDA 11.8 或 12.1
- 显存 16 GB 以上（推荐 24 GB 或更高，详见 [DeepSeek OCR 硬件需求讨论](https://huggingface.co/deepseek-ai/DeepSeek-OCR/discussions/31)）

## 安装步骤

本项目使用 DeepSeek OCR 进行文档识别，**必须在 CUDA 环境下运行**。如果你需要实际使用 pdf-craft 进行 PDF 转换，请按照下方 CUDA 环境安装步骤操作。

如果你仅需进行代码开发、IDE 类型提示或阅读源码，可以选择 CPU 环境安装作为替代方案，但无法执行实际的 OCR 识别。

### CUDA 环境安装（推荐）

#### 1. 配置 CUDA 环境

确保已安装 NVIDIA 驱动和 CUDA。检查 CUDA 版本：

```bash
nvidia-smi
```

#### 2. 安装 PyTorch

根据你的操作系统和 CUDA 版本选择合适的安装命令。

请访问 [PyTorch 官方安装页面](https://pytorch.org/get-started/locally/) 选择对应的配置并安装 PyTorch。

**示例**（CUDA 12.1）：

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

#### 3. 安装 pdf-craft

```bash
pip install pdf-craft
```

#### 4. 安装 Poppler

pdf-craft 使用 Poppler（通过 `pdf2image`）进行 PDF 解析和渲染。你需要单独安装 Poppler：

**Ubuntu/Debian：**
```bash
sudo apt-get install poppler-utils
```

**macOS：**
```bash
brew install poppler
```

**Windows：**

从 [oschwartz10612/poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases/) 下载最新的 Poppler 二进制文件，并将 `bin/` 目录添加到系统 PATH 中。或者，你可以在使用 pdf-craft 时指定 Poppler 路径（参见 [自定义 PDF 处理器](../README_zh-CN.md#自定义-pdf-处理器)）。

#### 5. 验证安装

验证 CUDA：
```bash
python -c "import torch; print('CUDA 可用:', torch.cuda.is_available())"
```

应输出 `CUDA 可用: True`

验证 Poppler：
```bash
pdfinfo -v
```

应输出 Poppler 版本信息。如果命令未找到，请检查上述 Poppler 安装步骤。

### CPU 环境安装（仅开发）

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install pdf-craft
```

**注意：** 即使是仅用于开发的环境，如果你想测试与 PDF 相关的功能，仍需按照上述步骤 4 安装 Poppler。

## 常见问题

### Poppler 未找到错误

如果运行 pdf-craft 时遇到类似"Poppler not found in PATH"的错误，说明 Poppler 未正确安装或配置：

1. **未安装 Poppler** - 按照上述对应操作系统的 Poppler 安装步骤操作
2. **Poppler 不在 PATH 中**（Windows）- 将 Poppler 的 `bin/` 目录添加到系统 PATH 中，或使用 `pdf_handler` 参数指定路径（参见 [自定义 PDF 处理器](../README_zh-CN.md#自定义-pdf-处理器)）
3. **安装了错误的包**（Linux）- 确保安装的是 `poppler-utils`，而不仅仅是 `poppler`

### CUDA 不可用报错

当你尝试使用 pdf-craft 时，如果看到类似以下的 RuntimeWarning：

```
CUDA is not available! This package requires CUDA to run,
but torch.cuda.is_available() returned False.
```

这说明 CUDA 环境未正确配置。可能的原因：

1. **安装了 CPU 版本的 PyTorch** - 需要重新按照上述 CUDA 环境安装步骤，安装支持 CUDA 的 PyTorch 版本
2. **NVIDIA 驱动过旧或未安装** - 访问 [NVIDIA 驱动下载页](https://www.nvidia.com/download/index.aspx) 更新驱动
3. **没有 CUDA 兼容的 GPU** - 本项目必须在 NVIDIA GPU 上运行

你可以运行 `nvidia-smi` 命令来检查系统的 GPU 和驱动状态。

### 如何选择 CUDA 版本

1. 运行 `nvidia-smi` 查看右上角的 CUDA Version
2. 访问 [PyTorch 官网](https://pytorch.org/get-started/locally/) 选择对应或更低的 CUDA 版本
3. 通常 CUDA 12.1 或 11.8 有最好的兼容性

### 依赖冲突

如果遇到依赖版本冲突，建议使用虚拟环境：

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows

# 然后按照上述 CUDA 环境安装步骤操作
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
pip install pdf-craft
```
