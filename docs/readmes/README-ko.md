<!-- Content baseline: README-zh-CN.md. Preserve the Chinese README priorities in natural local copy; keep API examples accurate. -->
<div align="center">
  <img src="../images/pdf-craft-readme-banner-v1.png" alt="PDF Craft — 스캔한 책을 편집하고 읽을 수 있는 텍스트로." width="100%" />
  <p><a href="../../README.md">English</a> | <a href="README-zh-CN.md">简体中文</a> | <a href="README-zh-TW.md">繁體中文</a> | <a href="README-ja.md">日本語</a> | <strong>한국어</strong> | <a href="README-ru.md">Русский</a> | <a href="README-fr.md">Français</a> | <a href="README-es.md">Español</a> | <a href="README-de.md">Deutsch</a> | <a href="README-it.md">Italiano</a></p>
  <p>
    <a href="https://pypi.org/project/pdf-craft/"><img src="https://img.shields.io/pypi/v/pdf-craft.svg?color=AD493B" alt="PyPI" /></a>
    <a href="https://pypi.org/project/pdf-craft/"><img src="https://img.shields.io/pypi/pyversions/pdf-craft.svg" alt="Python" /></a>
    <a href="https://github.com/oomol-lab/pdf-craft/actions/workflows/merge-build.yml"><img src="https://img.shields.io/github/actions/workflow/status/oomol-lab/pdf-craft/merge-build.yml" alt="CI" /></a>
    <a href="../../LICENSE"><img src="https://img.shields.io/github/license/oomol-lab/pdf-craft" alt="MIT" /></a>
  </p>
  <p>
    <a href="https://trendshift.io/repositories/15538"><img src="https://trendshift.io/api/badge/repositories/15538" alt="PDF Craft | GitHub Trending on Trendshift" width="250" height="55" /></a>
  </p>
  <p>
    <a href="https://pdfcraft.ai/pdf-craft/"><strong>PDF 온라인 변환</strong></a> ·
    <a href="#quick-start"><strong>Python 빠른 시작</strong></a> ·
    <a href="#documentation"><strong>문서</strong></a>
  </p>
</div>

PDF Craft는 스캔한 책, 논문, 기술 문서를 위한 Python 라이브러리입니다. 스캔 PDF를 편집하기 좋은 Markdown이나 읽기 편한 EPUB으로 변환합니다. PDF와 EPUB의 내용을 다른 언어로 번역해 번역문만 담은 EPUB 또는 원문과 번역문을 함께 담은 이중 언어 EPUB을 만들 수 있으며, 번역문을 원본 PDF 페이지에 넣을 수도 있습니다. 본문, 장, 목차, 각주, 표, 수식, 이미지를 인식해 스캔 이미지에 갇혀 있던 내용을 검색하고 편집하며 읽을 수 있게 합니다.

## 설치 없이 브라우저에서 바로 변환하세요

스캔 PDF를 편집 가능한 Markdown이나 전자책으로 읽기 좋은 EPUB으로 바꾸고 싶으신가요? **[PDF Craft Online · pdfcraft.ai](https://pdfcraft.ai/pdf-craft/)**에서 브라우저로 파일을 업로드하고 변환 작업을 제출한 뒤, 완료되면 결과를 다운로드하세요. Python 설치나 CUDA 설정이 필요 없고, OCR 서비스를 따로 찾거나 API 키를 입력할 필요도 없습니다.

PDF Craft Online은 이 오픈 소스 프로젝트의 공식 온라인 서비스입니다. 파일을 바로 변환하려면 온라인 버전을, 직접 만드는 앱에 통합하거나 내 컴퓨터에서 OCR을 실행하려면 아래의 Python 라이브러리를 사용하세요.

**[PDF를 업로드하고 온라인으로 변환하기 →](https://pdfcraft.ai/pdf-craft/)**

[![PDF Craft 온라인 변환 화면 — 영어, 클릭하여 PDF 업로드](../images/website-en.png)](https://pdfcraft.ai/pdf-craft/)

## 변환 결과 예시

**Markdown: 편집, 검색, 후속 콘텐츠 처리에 사용합니다.**

![PDF에서 Markdown으로 변환한 예시 — 영어](../images/pdf2md-en.png)

**EPUB: 전자책 리더에서 읽을 수 있습니다.**

![PDF에서 EPUB으로 변환한 예시 — 영어](../images/pdf2epub-en.png)

결과는 스캔 품질, 페이지 배치, OCR 모델에 따라 달라집니다. 대량 처리 전에 대표적인 문서로 결과를 확인하세요.

**내 파일의 변환 결과가 궁금하신가요? [PDF를 업로드하고 Markdown 또는 EPUB으로 변환하세요 →](https://pdfcraft.ai/pdf-craft/)**

## 주요 기능

| 목적 | 제공 기능 |
| --- | --- |
| 스캔한 책 편집 | PDF → Markdown, 텍스트와 이미지 리소스 출력 |
| 전자책 리더에서 읽기 | PDF → EPUB, 책 정보와 목차 지원 |
| 다른 언어로 책 읽기 | 변환 중 번역 또는 기존 EPUB 번역, 번역문 전용 및 이중 언어 출력 |
| 번역된 PDF 만들기 | 추출한 텍스트를 번역하여 원본 페이지에 기록 |
| 앱에 통합 | Python API와 렌더링·번역에 재사용할 수 있는 추출 파일 |

## 사용 방식 선택

| 방식 | 대상 | 준비 사항 |
| --- | --- | --- |
| **[온라인 변환](https://pdfcraft.ai/pdf-craft/)** | 파일을 업로드하고 Markdown 또는 EPUB 다운로드 | 브라우저만 있으면 됩니다. Python 설치나 OCR 서비스 설정 불필요 |
| **Python + 원격 OCR** | 모델을 로컬에서 실행하지 않는 개발자 | Python, Poppler, 호환 서비스 URL과 인증 정보 |
| **Python + 로컬 OCR** | NVIDIA GPU를 사용하는 개발자 | Python, Poppler, CUDA, 충분한 VRAM, 모델 파일 |

원격 OCR은 설정한 서비스에 페이지를 전송하며 로컬 CUDA가 필요하지 않습니다. 로컬 OCR은 사용자 컴퓨터에서 실행되지만, 번역이나 목차 분석에 원격 LLM을 사용하면 해당 콘텐츠는 서비스로 전송됩니다.

<a id="quick-start"></a>

## Python 빠른 시작

원격 OCR로 PDF를 Markdown으로 변환하는 예시입니다. **Python 3.11–3.13, Poppler, 사용 가능한 DeepSeek OCR 호환 서비스 설정**을 준비하세요. [설치 안내](../en/INSTALLATION.md)를 참고하세요. 이 페이지에서 연결하는 상세 문서는 영어로 제공됩니다.

> 개발 환경이나 OCR 서비스를 설정하지 않고 파일만 변환하고 싶다면 [PDF Craft Online](https://pdfcraft.ai/pdf-craft/)을 이용하세요. 브라우저에서 PDF를 업로드하면 됩니다.

### 1. 설치

```bash
python -m pip install pdf-craft
```

### 2. PDF 변환

스크립트를 실행하는 디렉터리에 `input.pdf`를 넣고, URL과 API 키, 모델 이름을 바꿔 실행하세요.

```python
from pdf_craft import DeepSeekOCRVendorConfig, PDFCraft, PDFOptions

craft = PDFCraft(
    pdf=PDFOptions(
        ocr=DeepSeekOCRVendorConfig(
            base_url="https://example.com/v1",
            api_key="your-api-key",
            model="deepseek-ocr",
        ),
    ),
)

craft.convert_pdf_to_markdown("input.pdf", "output.md")
```

`https://example.com/v1`은 예시 주소입니다. 해당 OCR 모델을 실제로 제공하는 호환 서비스를 사용해야 합니다. 다른 모델은 [OCR 설정](../en/OCR_BACKENDS.md)을 참고하세요. 아직 OCR 서비스가 없다면 [PDF Craft Online](https://pdfcraft.ai/pdf-craft/)에서 파일을 변환할 수 있습니다.

완료 후 `output.md`를 여세요. 이미지가 있는 문서는 리소스 파일도 생성합니다. Markdown을 이동하거나 공유할 때 함께 보관하세요.

### 3. EPUB 생성

위에서 설정한 `craft` 인스턴스를 그대로 사용하고 마지막 줄을 다음으로 바꾸세요.

```python
craft.convert_pdf_to_epub("input.pdf", "output.epub")
```

EPUB 리더에서 `output.epub`를 여세요. 제목·저자·출력 옵션은 [PDF 변환과 번역](../en/PDF_TRANSLATION.md), 설치 및 실행 문제는 [문제 해결](../en/TROUBLESHOOTING.md)을 참고하세요.

EPUB에 서지 정보가 필요하면 앞부분 페이지의 OCR에서 메타데이터를 추출하는 기능을 켜고 별도의 metadata LLM을 설정하세요. PDF Craft는 OCR 내용으로 확인할 수 있는 값만 사용합니다. PDF 파일 속성은 빠진 항목을 보완하는 데만 쓰며, 책에 인쇄된 정보를 덮어쓰지 않습니다.

## 번역과 추출 결과 재사용

**책 번역.** PDF를 Markdown이나 EPUB으로 변환할 때 `XMLTranslator`를 제공하거나 기존 EPUB을 직접 번역할 수 있습니다. 번역은 별도의 텍스트 LLM을 사용하며 OCR과 독립적으로 설정합니다. EPUB 번역은 원문을 대체하거나 번역문을 덧붙여 두 언어로 읽을 수 있습니다.

**번역 PDF 생성.** 추출한 내용을 번역하여 원본 페이지에 기록합니다. Ghostscript와 적절한 로컬 글꼴도 필요합니다. 원본과 번역문을 기준으로 출력 배치를 확인하세요.

**한 번 추출하여 재사용.** `.pcex` 파일을 저장하면 이후 렌더링, 번역, 다른 컴퓨터에서의 처리에 활용할 수 있습니다. 설정된 `craft` 인스턴스를 사용하세요.

```python
craft.convert_pdf_to_markdown(
    "input.pdf",
    "output.md",
    extraction_path="book.pcex",
)
```

[PDF 변환과 번역](../en/PDF_TRANSLATION.md), [EPUB 번역](../en/EPUB_TRANSLATION.md), [`.pcex` 형식](../en/PCEX_FORMAT.md)을 참고하세요.

## OCR 및 실행 요구 사항

**DeepSeek OCR, DeepSeek OCR 2, Unlimited OCR**을 지원하며 각 모델에 로컬 및 원격 설정이 있습니다. 원격 OCR은 기본 설치를 사용합니다. 로컬 OCR용 추가 의존성은 다음과 같이 설치하세요.

```bash
python -m pip install "pdf-craft[local]"
```

로컬 실행에는 호환되는 CUDA 버전 PyTorch, 충분한 VRAM과 모델 파일이 필요합니다. 기본적으로 Hugging Face에서 모델을 다운로드하며, 미리 다운로드한 파일을 로컬에서 불러올 수도 있습니다. 모델별 프리셋과 요구 사항은 [OCR 설정](../en/OCR_BACKENDS.md)에 있습니다.

**언어 지원은 처리 단계에 따라 다릅니다.** README 언어는 문서 제공 언어를 뜻합니다. 인식은 OCR 모델에, 번역은 번역기와 텍스트 LLM에 따라 달라집니다. EPUB의 `lan` 매개변수는 현재 `zh` / `en`을 제공합니다. [API 문서](../en/API_REFERENCE.md)를 참고하세요.

<a id="documentation"></a>

## 문서 안내

아래 상세 문서는 영어로 제공됩니다.

| 작업 | 문서 |
| --- | --- |
| 시스템 의존성과 GPU 설정 | [설치](../en/INSTALLATION.md) |
| 모델, 원격 서비스, 캐시 설정 | [OCR 설정](../en/OCR_BACKENDS.md) |
| PDF 변환, EPUB 생성, 번역 PDF 출력 | [PDF 변환과 번역](../en/PDF_TRANSLATION.md) |
| 기존 EPUB 번역과 이중 언어 출력 | [EPUB 번역](../en/EPUB_TRANSLATION.md) |
| 매개변수, 타입, 메서드 | [API 문서](../en/API_REFERENCE.md) |
| 추출 결과 저장과 교환 | [`.pcex` 형식](../en/PCEX_FORMAT.md) |
| 설치 및 변환 문제 | [문제 해결](../en/TROUBLESHOOTING.md) |

## 피드백과 기여

문제와 제안은 [Issues](https://github.com/oomol-lab/pdf-craft/issues)에 남겨 주세요. 변환 문제에는 패키지 버전, OCR 설정 유형, 오류 로그, 공개 가능한 최소 재현 파일을 포함하고 인증 정보와 비공개 콘텐츠를 제거해 주세요.

코드, 문서, 번역을 개선하는 [Pull Request](https://github.com/oomol-lab/pdf-craft/pulls)를 환영합니다. 각 언어의 README는 중국어 간체 버전을 기준으로 삼아 온라인 변환과 번역 기능의 핵심을 해당 언어에 자연스럽게 표현하고, API 예시는 정확하게 유지해 주세요. 도움이 되었다면 Star로 프로젝트를 알려 주세요.

## 관련 프로젝트

[Wiki Graph](https://github.com/oomol-lab/wiki-graph)는 변환된 EPUB이나 Markdown 책에서 구조화된 요약, 장 구조, 지식 그래프를 생성합니다.

## 라이선스 및 감사

PDF Craft는 [MIT 라이선스](../../LICENSE)를 사용합니다. 타사 의존성과 선택한 OCR 모델에는 각각의 라이선스가 적용됩니다.

[DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR), [DeepSeek OCR 2](https://github.com/deepseek-ai/DeepSeek-OCR-2), [Unlimited OCR](https://github.com/baidu/Unlimited-OCR), [doc-page-extractor](https://github.com/Moskize91/doc-page-extractor), [pyahocorasick](https://github.com/WojciechMula/pyahocorasick) 등 오픈 소스 프로젝트에 감사드립니다.

<!-- community-footer:start -->

## 기여자

PDF Craft에 기여해 주신 모든 분께 감사드립니다. 코드, 문서, 번역 개선에 참여해 주세요.

[![PDF Craft 기여자](https://contrib.rocks/image?repo=oomol-lab/pdf-craft)](https://github.com/oomol-lab/pdf-craft/graphs/contributors)

## Star History

<!-- star-history:start -->
<a href="https://www.star-history.com/#oomol-lab/pdf-craft&amp;Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date&amp;theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date" />
    <img alt="PDF Craft 스타 수 변화" src="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date" />
  </picture>
</a>
<!-- star-history:end -->
<!-- community-footer:end -->
