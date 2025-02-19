# pdf-craft

English | [中文](./README_zh-CN.md)

## Introduction

PDF craft can convert PDF files into various other formats (currently only supports MarkDown format). This project will focus on processing PDF files of scanned books. The project has just started. If you encounter any problems or have any suggestions, please submit them in [issues](https://github.com/oomol-lab/pdf-craft/issues).

This project can read PDF pages one by one, and use [DocLayout-YOLO](https://github.com/opendatalab/DocLayout-YOLO) mixed with an algorithm I wrote to extract the main text from the book page and filter out elements such as headers, footers, footnotes, page numbers, etc. In the process of crossing pages, the algorithm will be used to properly handle the problem of cross-page connection between the previous and next text, and finally generate semantically coherent text.

I will currently focus on directly scanning and generating Chinese PDF books. The book pages will use [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) for text recognition. And use [layoutreader](https://github.com/ppaanngggg/layoutreader) to determine the reading order that conforms to human habits.

## Installation

```shell
pip install pdf-craft
```

## Using CUDA

If you want to use GPU acceleration, you need to make sure your device is ready for the CUDA environment. Please refer to the introduction of [PyTorch](https://pytorch.org/get-started/locally/) and select the appropriate command installation according to your operating system installation.

## Quick Start

```python
from pdf_craft import PDFPageExtractor, MarkDownWriter

extractor = PDFPageExtractor(
  device="cpu", # If you want to use CUDA, please change to device="cuda:0" format.
  model_dir_path="/path/to/model/dir/path", # Folder address for downloading and installing AI models
)
with MarkDownWriter(markdown_path, "images", "utf-8") as md:
  for blocks in extractor.extract(pdf="/path/to/pdf/file", lang="ch"):
    for block in blocks:
      md.write(block)
```

## Effect

![Scanned page annotation effect](./docs/images/pages.png)
![Effect after conversion to Markdown](./docs/images/markdown.png)

## Acknowledgements

- [DocLayout-YOLO](https://github.com/opendatalab/DocLayout-YOLO)
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)
- [layoutreader](https://github.com/ppaanngggg/layoutreader)