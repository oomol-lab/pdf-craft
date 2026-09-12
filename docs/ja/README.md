<!-- Translation baseline: README.md. Keep capability descriptions and executable examples aligned. -->
<div align="center">
  <img src="../images/pdf-craft-readme-banner-v1.png" alt="PDF Craft — スキャンした本を、編集して読めるテキストに。" width="100%" />
  <p><a href="../../README.md">English</a> | <a href="../../README_zh-CN.md">简体中文</a> | <a href="../zh-TW/README.md">繁體中文</a> | <strong>日本語</strong> | <a href="../ko/README.md">한국어</a> | <a href="../ru/README.md">Русский</a> | <a href="../fr/README.md">Français</a> | <a href="../es/README.md">Español</a> | <a href="../de/README.md">Deutsch</a> | <a href="../it/README.md">Italiano</a></p>
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
    <a href="https://inkora.oomol.com/pdf-craft/"><strong>オンラインで試す</strong></a> ·
    <a href="#quick-start"><strong>Python クイックスタート</strong></a> ·
    <a href="#documentation"><strong>ドキュメント</strong></a>
  </p>
</div>

スキャン PDF を Markdown や EPUB に変換。翻訳や、訳文を PDF に書き戻す処理にも対応します。

## スキャンしたページを、使える文書に

PDF Craft は、スキャンした書籍や学術・技術文書を扱う Python ライブラリです。ページから内容を抽出し、本文、章、目次、脚注、表、数式、画像を整理して、編集や読書に使える形に変換します。

**Markdown：編集、検索、後続のコンテンツ処理に。**

![PDF から Markdown への変換例（英語）](../images/pdf2md-en.png)

**EPUB：電子書籍リーダーでの読書に。**

![PDF から EPUB への変換例（英語）](../images/pdf2epub-en.png)

結果はスキャン品質、レイアウト、OCR モデルによって変わります。大量のファイルを処理する前に、代表的な文書で出力を確認してください。

## できること

| 目的 | 機能 |
| --- | --- |
| スキャンした本を編集する | PDF → Markdown。テキストと画像ファイルを出力 |
| 電子書籍リーダーで読む | PDF → EPUB。書籍情報や目次に対応 |
| 別の言語で本を読む | 変換時の翻訳、既存 EPUB の翻訳。訳文のみ・対訳の出力に対応 |
| 翻訳済み PDF を作る | 抽出した文字を翻訳し、元のページに書き戻す |
| アプリに組み込む | Python API と、後から再利用できる抽出結果ファイル |

## 使い方を選ぶ

| 方法 | 対象 | 必要なもの |
| --- | --- | --- |
| **[オンライン](https://inkora.oomol.com/pdf-craft/)** | まず試したい方 | ブラウザー。機能と利用条件はオンラインアプリで確認 |
| **Python + リモート OCR** | ローカルでモデルを実行しない開発者 | Python、Poppler、対応サービスの URL と認証情報 |
| **Python + ローカル OCR** | NVIDIA GPU を使う開発者 | Python、Poppler、CUDA、十分な VRAM、モデルファイル |

リモート OCR は設定したサービスにページを送信し、ローカルの CUDA は不要です。ローカル OCR は手元のマシンで動きますが、翻訳や目次解析にリモート LLM を使う場合、その内容はサービスに送信されます。

**オンラインアプリの画面を見る（英語）**

[![PDF Craft オンライン版](../images/website-en.png)](https://inkora.oomol.com/pdf-craft/)

<a id="quick-start"></a>

## クイックスタート

リモート OCR で PDF を Markdown に変換します。**Python 3.11–3.13、Poppler、利用可能な DeepSeek OCR 対応サービスの設定**を用意してください。[インストールガイド](../en/INSTALLATION.md)に設定手順があります。このページからリンクする詳細ガイドは英語です。

### 1. インストール

```bash
python -m pip install pdf-craft
```

### 2. PDF を変換

スクリプトを実行するディレクトリに `input.pdf` を置き、URL、API キー、モデル名を置き換えて実行します。

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

`https://example.com/v1` はプレースホルダーです。実際に該当 OCR モデルを提供する対応サービスを指定してください。他のモデルについては [OCR 設定](../en/OCR_BACKENDS.md)を参照してください。

完了後に `output.md` を開きます。画像のある文書では画像などのリソースファイルも生成されます。移動・共有時には Markdown と一緒に保持してください。

### 3. EPUB を作成

設定済みの `craft` を使い、最後の行を次に置き換えます。

```python
craft.convert_pdf_to_epub("input.pdf", "output.epub")
```

`output.epub` を EPUB リーダーで開けます。書名、著者、出力設定は [PDF の変換と翻訳](../en/PDF_TRANSLATION.md)、実行時の問題は[トラブルシューティング](../en/TROUBLESHOOTING.md)を参照してください。

## 翻訳と抽出結果の再利用

**本を翻訳する。** PDF から Markdown / EPUB への変換には章単位の翻訳器を渡せます。既存 EPUB の直接翻訳も可能です。翻訳には独立したテキスト LLM を使い、OCR と別に設定します。EPUB では原文を訳文で置換するか、訳文を追加して対訳にできます。

**翻訳済み PDF を作る。** 抽出・翻訳した文字を元のページに書き戻します。Ghostscript と適切なローカルフォントも必要です。原稿と訳文に応じて出力レイアウトを確認してください。

**抽出結果を保存する。** `.pcex` ファイルを保存すると、後のレンダリング、翻訳、別のマシンでの処理に再利用できます。設定済みの `craft` を使います。

```python
craft.convert_pdf_to_markdown(
    "input.pdf",
    "output.md",
    extraction_path="book.pcex",
)
```

詳しくは [PDF の変換と翻訳](../en/PDF_TRANSLATION.md)、[EPUB 翻訳](../en/EPUB_TRANSLATION.md)、[`.pcex` 形式](../en/PCEX_FORMAT.md)を参照してください。

## OCR と実行環境

**DeepSeek OCR、DeepSeek OCR 2、Unlimited OCR** に対応し、それぞれローカル・リモート設定を提供します。リモート OCR は通常のインストールで利用できます。ローカル OCR 用の追加依存関係は次で導入します。

```bash
python -m pip install "pdf-craft[local]"
```

ローカル実行には対応する CUDA 版 PyTorch、十分な VRAM、モデルファイルも必要です。モデルは既定で Hugging Face からダウンロードされます。事前にダウンロードしてローカルから読み込むことも可能です。モデル別のプリセットと要件は [OCR 設定](../en/OCR_BACKENDS.md)を参照してください。

**対応言語は処理段階によって異なります。** README の言語は文書の提供言語です。認識は OCR モデル、翻訳は翻訳器とテキスト LLM に依存します。EPUB の `lan` は現在 `zh` / `en` を提供します。[API リファレンス](../en/API_REFERENCE.md)を参照してください。

<a id="documentation"></a>

## ドキュメント

以下の詳細ガイドは英語です。

| 目的 | ガイド |
| --- | --- |
| 依存関係と GPU の設定 | [インストール](../en/INSTALLATION.md) |
| モデル、リモートサービス、キャッシュ | [OCR 設定](../en/OCR_BACKENDS.md) |
| PDF 変換、EPUB 生成、PDF への訳文出力 | [PDF の変換と翻訳](../en/PDF_TRANSLATION.md) |
| 既存 EPUB の翻訳と対訳 | [EPUB 翻訳](../en/EPUB_TRANSLATION.md) |
| 引数、型、メソッド | [API リファレンス](../en/API_REFERENCE.md) |
| 抽出結果の保存と交換 | [`.pcex` 形式](../en/PCEX_FORMAT.md) |
| インストールや変換の問題 | [トラブルシューティング](../en/TROUBLESHOOTING.md) |

## フィードバックと貢献

不具合や提案は [Issues](https://github.com/oomol-lab/pdf-craft/issues) にお寄せください。変換の問題にはバージョン、OCR 設定の種類、エラーログ、公開可能な最小の再現ファイルを添えてください。認証情報や私的な内容は取り除いてください。

コード、文書、翻訳の [Pull Request](https://github.com/oomol-lab/pdf-craft/pulls) を歓迎します。翻訳版の機能説明と例は英文 README と揃えてください。役に立ったら Star でプロジェクトを応援してください。

## 関連プロジェクト

[Wiki Graph](https://github.com/oomol-lab/wiki-graph) は、変換後の EPUB や Markdown から構造化された要約、章の構造、ナレッジグラフを生成します。

## ライセンスと謝辞

PDF Craft は [MIT ライセンス](../../LICENSE)です。第三者の依存関係と OCR モデルにはそれぞれのライセンスが適用されます。

[DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR), [DeepSeek OCR 2](https://github.com/deepseek-ai/DeepSeek-OCR-2), [Unlimited OCR](https://github.com/baidu/Unlimited-OCR), [doc-page-extractor](https://github.com/Moskize91/doc-page-extractor), [pyahocorasick](https://github.com/WojciechMula/pyahocorasick) をはじめとするオープンソースプロジェクトに感謝します。

<!-- community-footer:start -->

## コントリビューター

PDF Craft に貢献してくださった皆さんに感謝します。コード、ドキュメント、翻訳への貢献を歓迎します。

[![PDF Craft のコントリビューター](https://contrib.rocks/image?repo=oomol-lab/pdf-craft)](https://github.com/oomol-lab/pdf-craft/graphs/contributors)

## Star History

<!-- star-history:start -->
<a href="https://www.star-history.com/#oomol-lab/pdf-craft&amp;Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date&amp;theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date" />
    <img alt="PDF Craft のスター数の推移" src="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date" />
  </picture>
</a>
<!-- star-history:end -->
<!-- community-footer:end -->
