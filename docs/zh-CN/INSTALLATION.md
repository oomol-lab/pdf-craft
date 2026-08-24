# 安装指南

这份指南面向使用 pdf-craft Python 库的用户。它只解决一个问题：根据你的设备条件，
把 pdf-craft 和处理 PDF 所需的系统依赖安装好。

## 先决定安装哪一种

大多数用户直接安装标准版本即可：

```bash
python -m pip install pdf-craft
```

标准版本包含 vendor OCR、Markdown/EPUB 渲染和 PDF 翻译所需的依赖。vendor OCR 使用
远程服务的计算资源，本机不需要 CUDA；使用时在代码的 OCR 配置对象中填写服务地址、
模型名和访问密钥即可。

只有在你明确希望让 OCR 模型运行在自己的 NVIDIA GPU 上时，才安装 local extra：

```bash
python -m pip install "pdf-craft[local]"
```

local extra 提供本地 OCR 所需的 Hugging Face、Transformers 等运行时。它不会替你选择
适合所有设备的 PyTorch CUDA wheel；你仍需要根据操作系统、Python 版本、NVIDIA 驱动和
CUDA 环境安装匹配的 PyTorch。没有可用 CUDA 设备时，使用标准版本和 vendor OCR。

如果你不确定自己应该选哪一种，使用标准版本。

## 系统要求

- Python `>=3.11,<3.14`。
- Poppler：所有需要读取或渲染 PDF 的流程都需要它。
- vendor OCR：网络连接、有效的服务配置和 Poppler；本机不需要 CUDA。
- local OCR：支持 CUDA 的 NVIDIA GPU、匹配的 PyTorch、模型缓存、足够的显存和 Poppler。

local OCR 的显存需求取决于所选模型、`ocr_size` 和输入页面。不要把某个模型的显存
经验值当作所有 backend 的硬性要求；如果设备资源不足，优先使用 vendor OCR。

## 建议使用虚拟环境

虚拟环境可以避免 pdf-craft 与系统中其他 Python 项目互相影响。以 Python 3.11 为例：

```bash
python3.11 -m venv .venv
source .venv/bin/activate       # macOS / Linux
# Windows PowerShell:
# .venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install pdf-craft
```

如果要使用 local OCR，在安装标准版本的位置改为：

```bash
python -m pip install "pdf-craft[local]"
```

安装完成后，后续命令都应在这个虚拟环境中执行。

## 安装 Poppler

Poppler 是 pdf2image 用来读取和渲染 PDF 页面的系统工具。安装后，`pdfinfo` 应该能在
终端中直接执行。

### macOS

使用 Homebrew：

```bash
brew install poppler
```

### Debian / Ubuntu

```bash
sudo apt-get update
sudo apt-get install poppler-utils
```

### Windows

下载适用于 Windows 的 Poppler 二进制包，将其中的 `bin` 目录加入系统 `PATH`，然后重新
打开终端。也可以在应用层通过自定义 PDF handler 指定 Poppler 路径；这属于高级 API，
不影响标准安装。

### 验证 Poppler

```bash
pdfinfo -v
```

如果出现 `command not found` 或 Windows 找不到命令，请先修复 PATH，再运行 pdf-craft。

## local OCR 的 CUDA 环境

只有选择 local OCR 时才需要这部分准备。vendor OCR 用户可以跳过。

### 1. 检查 NVIDIA 驱动和 GPU

```bash
nvidia-smi
```

如果命令不存在、没有显示 NVIDIA GPU，或驱动状态异常，当前设备无法运行 local OCR。

### 2. 安装匹配的 PyTorch

PyTorch 的安装命令取决于操作系统、Python 版本和 CUDA wheel。请在 PyTorch 官方安装
页面选择与你的环境匹配的命令，不要盲目复制其他机器的 CUDA 版本：

<https://pytorch.org/get-started/locally/>

pdf-craft 不直接固定 `torch` 或 `torchvision` 版本，原因是 CPU、CUDA 和不同平台需要
不同的 wheel。安装后可以检查 CUDA 是否可用：

```bash
python -c "import torch; print(torch.__version__); print('CUDA available:', torch.cuda.is_available())"
```

local OCR 需要输出 `CUDA available: True`。如果输出为 `False`，请使用 vendor OCR，
或先修复 PyTorch、驱动和 CUDA 环境，再继续配置 local OCR。

### 3. 安装 pdf-craft local extra

在确认 PyTorch CUDA 环境可用后安装：

```bash
python -m pip install "pdf-craft[local]"
```

该 extra 通过 `doc-page-extractor[local]` 提供本地 OCR 运行时。模型首次使用时可能会
从模型仓库下载较大的文件；模型缓存位置和离线运行方式请参考 OCR backend 配置指南。

## 验证 Python 包安装

无论选择 vendor OCR 还是 local OCR，都可以先验证 Python 包是否能够导入：

```bash
python -c "import pdf_craft; print(pdf_craft.__file__)"
```

这一步只验证包安装，不会下载模型、调用 OCR 服务或处理 PDF。真正开始转换时，再按
README 的快速开始示例创建对应的 OCR 配置对象。

## 运行前的配置边界

- 库 API 不会自动读取 `.env`；请在 Python 代码中显式传入 OCR 配置和翻译 LLM 配置。
- vendor OCR 需要服务端点、模型名、访问密钥和网络连接。
- local OCR 需要本机 CUDA、PyTorch、模型文件和显存。
- OCR 只负责页面识别；翻译需要单独的文本 LLM 配置。

当安装本身没有问题、但运行时出现 Poppler、CUDA、模型下载或远程请求错误时，请转到
故障排查指南继续处理。
