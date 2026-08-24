# 安装指南

## 系统要求

- Python >= 3.11, < 3.14（推荐 3.11.16）
- Poppler（用于 PDF 解析和渲染）
- NVIDIA GPU，支持 CUDA 11.8 或更新版本，仅本地 OCR 需要
- 本地 OCR 需要 16 GB 以上显存（最大的 DeepSeek OCR 模型推荐 24 GB 或更高）

## 安装步骤

pdf-craft 使用 `doc-page-extractor` 进行文档识别。供应商 OCR 后端不需要本地 CUDA；本地 OCR 后端需要支持 CUDA 的 PyTorch 环境。

CPU 环境无法运行本地 OCR；在具备网络、有效凭据和 Poppler 时，仍可运行供应商 OCR。

### 供应商 OCR 安装

供应商 OCR 是默认安装路径，不会安装 PyTorch、Hugging Face Transformers 或 CUDA
运行时依赖：

```bash
pip install pdf-craft
```

按下文安装 Poppler，然后在应用中配置供应商 OCR 后端（使用仓库内
`pdf_craft_tool` 时在 `.env` 中配置）。

### CUDA 环境安装

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

#### 3. 安装 pdf-craft 本地运行时

```bash
pip install "pdf-craft[local]"
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

### CPU 环境安装

```bash
pip install pdf-craft
```

此安装可以运行供应商 OCR，但不能运行本地 OCR。如果需要处理 PDF，仍需按照上述步骤 4
安装 Poppler。使用仓库中的手动脚本时，请将 `.env.template` 复制为 `.env`，填写供应商
OCR 配置后再运行脚本。

## 常见问题

### Poppler 未找到错误

如果运行 pdf-craft 时遇到类似"Poppler not found in PATH"的错误，说明 Poppler 未正确安装或配置：

1. **未安装 Poppler** - 按照上述对应操作系统的 Poppler 安装步骤操作
2. **Poppler 不在 PATH 中**（Windows）- 将 Poppler 的 `bin/` 目录添加到系统 PATH 中，或使用 `pdf_handler` 参数指定路径（参见 [自定义 PDF 处理器](../README_zh-CN.md#自定义-pdf-处理器)）
3. **安装了错误的包**（Linux）- 确保安装的是 `poppler-utils`，而不仅仅是 `poppler`

### CUDA 不可用报错

当你使用本地 OCR config 时，如果看到类似以下的 RuntimeWarning：

```
CUDA is not available! This package requires CUDA to run,
but torch.cuda.is_available() returned False.
```

这说明 CUDA 环境未正确配置。可能的原因：

1. **安装了 CPU 版本的 PyTorch** - 需要重新按照上述 CUDA 环境安装步骤，安装支持 CUDA 的 PyTorch 版本
2. **NVIDIA 驱动过旧或未安装** - 访问 [NVIDIA 驱动下载页](https://www.nvidia.com/download/index.aspx) 更新驱动
3. **没有 CUDA 兼容的 GPU** - 本地 OCR 必须在 NVIDIA GPU 上运行

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
