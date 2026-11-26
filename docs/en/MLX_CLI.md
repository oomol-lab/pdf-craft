# Local PDF to EPUB on Apple Silicon

`PDF_CRAFT_DEEPSEEK_OCR_LOCAL_RUNTIME=mlx PYTHONPATH=../deepseekocr-mlx python -m pdf_craft_tool pdf convert book.pdf --format epub --ocr-mode deepseek-ocr-local`

Uses the existing conversion CLI and page extraction pipeline with the MLX model.
OCR runs locally on Apple Silicon; no OCR API or API key is needed.

- Select the checkpoint cache with `PDF_CRAFT_DEEPSEEK_OCR_LOCAL_MODELS_CACHE_PATH`.
- `--output FILE.epub` sets the output; `--pages 1,2,3` selects pages.
- `--work-dir PATH` retains OCR; use a fresh directory when the PDF or model changes.
- Generation runs to EOS; `--max-ocr-output-tokens N` sets an optional token budget.
- Render saved extraction with `package render book.pcex --format epub`.

## Install

Run from this checkout with Python 3.12 or 3.13:

```sh
brew install poppler
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e . -r pdf_craft_tool/requirements-mlx.txt
git clone --branch pdf-craft-model-interface https://github.com/humdrum00001010/deepseekocr-mlx.git ../deepseekocr-mlx
```

Create `.env` from `.env.template` if missing; the existing CLI reads runtime settings there.
Use an existing checkpoint cache, or download with pdf-craft's `predownload_models()`.
Keep these MLX dependencies separate from the CUDA `local` extra.
The model fork exposes the inference methods used by the existing extractor.
Its SAM correction is proposed in [deepseekocr-mlx PR #3](https://github.com/rayking99/deepseekocr-mlx/pull/3).
