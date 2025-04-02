<div align=center>
  <h1>PDF Craft</h1>
  <p>
    <a href="https://github.com/oomol-lab/pdf-craft/actions/workflows/build.yml" target="_blank"><img src="https://img.shields.io/github/actions/workflow/status/oomol-lab/pdf-craft/build.yml" alt"ci" /></a>
    <a href="https://pypi.org/project/pdf-craft/" target="_blank"><img src="https://img.shields.io/badge/pip_install-pdf--craft-blue" alt="pip install pdf-craft" /></a>
    <a href="https://pypi.org/project/pdf-craft/" target="_blank"><img src="https://img.shields.io/pypi/v/pdf-craft.svg" alt"pypi pdf-craft" /></a>
    <a href="https://pypi.org/project/pdf-craft/" target="_blank"><img src="https://img.shields.io/pypi/pyversions/pdf-craft.svg" alt="python versions" /></a>
    <a href="https://github.com/oomol-lab/pdf-craft/blob/main/LICENSE" target="_blank"><img src="https://img.shields.io/github/license/oomol-lab/pdf-craft" alt"license" /></a>
  </p>
  <p><a href="https://hub.oomol.com/package/pdf-craft-starter?open=true" target="_blank"><img src="https://static.oomol.com/assets/button.svg" alt="Open in OOMOL Studio" /></a></p>
  <p>English | <a href="./README_zh-CN.md">中文</a></p>
</div>


## Introduction

PDF Craft can convert PDF files into various other formats. This project will focus on processing PDF files of scanned books. The project has just started. If you encounter any problems or have any suggestions, please submit [issues](https://github.com/oomol-lab/pdf-craft/issues).

[![About PDF Craft](./docs/images/youtube.png)](https://www.youtube.com/watch?v=EpaLC71gPpM)

This project can read PDF pages one by one, and use [DocLayout-YOLO](https://github.com/opendatalab/DocLayout-YOLO) mixed with an algorithm I wrote to extract the text from the book pages and filter out elements such as headers, footers, footnotes, and page numbers. In the process of crossing pages, the algorithm will be used to properly handle the problem of the connection between the previous and next pages, and finally generate semantically coherent text. The book pages will use [OnnxOCR](https://github.com/jingsongliujing/OnnxOCR) for text recognition. And use [layoutreader](https://github.com/ppaanngggg/layoutreader) to determine the reading order that conforms to human habits.

With only these AI models that can be executed locally (using local graphics devices to accelerate), PDF files can be converted to Markdown format. This is suitable for papers or small books.

However, if you want to parse books (generally more than 100 pages), it is recommended to convert them to [EPUB](https://en.wikipedia.org/wiki/EPUB) format files. During the conversion process, this library will pass the data recognized by the local OCR to [LLM](https://en.wikipedia.org/wiki/Large_language_model), and build the structure of the book through specific information (such as the table of contents), and finally generate an EPUB file with a table of contents and chapters. During this parsing and construction process, the annotations and citations information of each page will be read through LLM, and then presented in the new format in the EPUB file. In addition, LLM can correct OCR errors to a certain extent. This step cannot be performed entirely locally. You need to configure the LLM service. It is recommended to use [DeepSeek](https://www.deepseek.com/). The prompt of this library is based on V3 model testing.

## Installation

You need python 3.10 or above (recommended 3.10.16).

```shell
pip install pdf-craft
```

```shell
pip install onnxruntime==1.21.0
```

## Using CUDA

If you want to use GPU acceleration, you need to ensure that your device is ready for the CUDA environment. Please refer to the introduction of [PyTorch](https://pytorch.org/get-started/locally/) and select the appropriate command installation according to your operating system installation.

In addition, replace the command to install `onnxruntime` in the previous article with the following:

```shell
pip install onnxruntime-gpu==1.21.0
```

## Function

### Convert PDF to MarkDown

This operation does not require calling a remote LLM, and can be completed with local computing power (CPU or graphics card). The required model will be downloaded online when it is called for the first time. When encountering illustrations, tables, and formulas in the document, screenshots will be directly inserted into the MarkDown file.

```python
from pdf_craft import PDFPageExtractor, MarkDownWriter

extractor = PDFPageExtractor(
  device="cpu", # If you want to use CUDA, please change to device="cuda:0" format.
  model_dir_path="/path/to/model/dir/path", # The folder address where the AI ​​model is downloaded and installed
)
with MarkDownWriter(markdown_path, "images", "utf-8") as md:
  for block in extractor.extract(pdf="/path/to/pdf/file"):
    md.write(block)
```

After the execution is completed, a `*.md` file will be generated at the specified path. If there are illustrations (or tables, formulas) in the original PDF, an `assets` directory will be created at the same level as `*.md` to save the images. The images in the `assets` directory will be referenced in the MarkDown file in the form of relative addresses.

The conversion effect is as follows.

![](docs/images/pdf2md-en.png)

### Convert PDF to EPUB

The first half of this operation is the same as Convert PDF to MarkDown (see the previous section). OCR will be used to scan and recognize text from PDF. Therefore, you also need to build a `PDFPageExtractor` object first.

```python
from pdf_craft import PDFPageExtractor

extractor = PDFPageExtractor(
  device="cpu", # If you want to use CUDA, please change to device="cuda:0" format.
  model_dir_path="/path/to/model/dir/path", # The folder address where the AI ​​model is downloaded and installed
)
```

After that, you need to configure the `LLM` object. It is recommended to use [DeepSeek](https://www.deepseek.com/). The prompt of this library is based on V3 model testing.

```python
from pdf_craft import LLM

llm = LLM(
  key="sk-XXXXX", # key provided by LLM vendor
  url="https://api.deepseek.com", # URL provided by LLM vendor
  model="deepseek-chat", # model provided by LLM vendor
  token_encoding="o200k_base", # local model name for tokens estimation (not related to LLM, if you don't care, keep "o200k_base")
)
```

After the above two objects are prepared, you can start scanning and analyzing PDF books.

```python
from pdf_craft import analyse

analyse(
  llm=llm, # LLM configuration prepared in the previous step
  pdf_page_extractor=pdf_page_extractor, # PDFPageExtractor object prepared in the previous step
  pdf_path="/path/to/pdf/file", # PDF file path
  analysing_dir_path="/path/to/analysing/dir", # analysing directory path
  output_dir_path="/path/to/output/files", # The analysis results will be written to this directory
)
```

Note the two directory paths in the above code. One is `output_dir_path`, which indicates the folder where the scan and analysis results (there will be multiple files) should be saved. The paths should point to an empty directory. If it does not exist, a directory will be created automatically.

The second is `analysing_dir_path`, which is used to store the intermediate status during the analysis process. After successful scanning and analysis, this directory and its files will become useless (you can delete them with code). The path should point to a directory. If it does not exist, a directory will be created automatically. This directory (and its files) can save the analysis progress. If an analysis is interrupted due to an accident, you can configure `analysing_dir_path` to the analysing folder generated by the last interruption, so as to resume and continue the analysis from the last interruption point. In particular, if you want to start a new task, please manually delete or empty the `analysing_dir_path` directory to avoid accidentally triggering the interruption recovery function.

After the analysis is completed, pass the `output_dir_path` to the following code as a parameter to finally generate the EPUB file.

```python
from pdf_craft import generate_epub_file

generate_epub_file(
  from_dir_path=output_dir_path, # from the folder generated by the previous step
  epub_file_path="/path/to/output/epub", # generated EPUB file save path
)
```

This step will divide the chapters in the EPUB according to the previously analyzed book structure and match the appropriate directory structure. In addition, the original annotations and citations at the bottom of the book page will be presented in the EPUB in an appropriate way.

![](docs/images/pdf2epub-en.png)
![](docs/images/epub-tox-en.png)
![](docs/images/epub-citations-en.png)

## Advanced Functions

### Multiple OCR

Improve recognition quality by performing multiple OCRs on the same page to avoid the problem of blurred text and missing text.

```python
from pdf_craft import OCRLevel, PDFPageExtractor

extractor = PDFPageExtractor(
  device="cpu",
  model_dir_path="/path/to/model/dir/path",
  ocr_level=OCRLevel.OncePerLayout,
)
```

### Advanced LLM

As mentioned above, the construction of `LLM` can add more parameters to it to achieve richer functions. To achieve disconnection and reconnection, or specify a specific timeout.

```python
llm = LLM(
  key="sk-XXXXX",
  url="https://api.deepseek.com",
  model="deepseek-chat",
  token_encoding="o200k_base",
  temperature=0.3, # Temperature (optional)
  timeout=360, # Timeout, in seconds (optional)
  retry_times=10, # The maximum number of retries that can be accepted for failed requests due to network reasons or incomplete formats (optional)
  retry_interval_seconds=6.0, # The time interval between retries, in seconds (optional)
)
```

In addition, `temperature` can be set to a range. In general, the leftmost value in the range is used as the temperature. Once LLM returns broken content, gradually increase the temperature when retrying (not exceeding the value on the right side of the range). This prevents LLM from falling into a loop that always returns broken content.

```python
llm = LLM(
  key="sk-XXXXX",
  url="https://api.deepseek.com",
  model="deepseek-chat",
  token_encoding="o200k_base",
  temperature=(0.3, 1.0), # Temperature (optional)
)
```

## Acknowledgements

- [DocLayout-YOLO](https://github.com/opendatalab/DocLayout-YOLO)
- [OnnxOCR](https://github.com/jingsongliujing/OnnxOCR)
- [layoutreader](https://github.com/ppaanngggg/layoutreader)
