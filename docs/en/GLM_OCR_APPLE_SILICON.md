# GLM-OCR on Apple Silicon (experimental)

This backend runs recognition on an Apple GPU through MLX-VLM and layout detection
on CPU through the GLM-OCR SDK. PDF Craft calls the **full SDK parsing service**;
it does not call MLX chat completions directly. No CUDA is needed.

## Availability

The PDF Craft configuration is `GLMOCRServiceConfig`. It requires the paired
`doc-page-extractor` implementation exposing `create_glm_ocr_service_page_extractor`.
The currently released upstream version 1.2.0 does **not** have that factory.
Until the upstream adapter is released, this is an explicit development setup,
not a feature available from an unmodified `pip install pdf-craft`.

The paired adapter is tracked in
[doc-page-extractor PR #104](https://github.com/Moskize91/doc-page-extractor/pull/104).
For the reviewed adapter revision, prepare a sibling checkout (from the directory
containing your PDF Craft checkout):

```sh
git clone https://github.com/jonathan-teamstatus/doc-page-extractor.git doc-page-extractor-glm
git -C doc-page-extractor-glm checkout 4603382
```

Then, from this PDF Craft feature checkout, install both into its environment:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip install -e ../doc-page-extractor-glm
.venv/bin/python -c 'from doc_page_extractor import create_glm_ocr_service_page_extractor'
```

The sibling directory must contain the paired adapter source. This editable
installation is deliberate local integration, not a patch to installed files.
Keep the normal dependency constraints unchanged until an upstream release exists.

## Runtime setup

Use three separate environments: PDF Craft, MLX-VLM, and the SDK. Model weights and
outputs are local artifacts; never commit them. Install Poppler for PDF rendering
(`brew install poppler`). Do not install `pdf-craft[local]` for this backend.

Validated on an M4 Pro with 48 GB memory, macOS 26.5.1, and Python 3.12.13:

- MLX-VLM 0.7.2, MLX 0.32.2, Transformers 5.17.0.
- GLM-OCR SDK source revision `cef4d0ea120d1741f5cefe8985eee45f6c8eff1d`
  (declares version 0.1.5), Torch 2.14.0, Torchvision 0.29.0.
- `mlx-community/GLM-OCR-bf16` and
  `PaddlePaddle/PP-DocLayoutV3_safetensors`.

The upstream MLX guide specifies macOS 14+; the oldest OS and lower-memory Macs
were not tested here. These versions are a tested combination, not a promise that
all future package releases work together.

Example isolated installation:

```sh
python3.12 -m venv .venv-mlx
.venv-mlx/bin/python -m pip install 'mlx-vlm==0.7.2' 'mlx==0.32.2' 'transformers==5.17.0'
python3.12 -m venv .venv-sdk
.venv-sdk/bin/python -m pip install \
  'glmocr[selfhosted,server] @ git+https://github.com/zai-org/GLM-OCR.git@cef4d0ea120d1741f5cefe8985eee45f6c8eff1d' \
  'transformers==5.17.0' 'torch==2.14.0' 'torchvision==0.29.0'
```

Download the models once using `hf download` in an environment providing the
Hugging Face CLI, recording the resolved model revisions:

```sh
hf download mlx-community/GLM-OCR-bf16 --local-dir models/GLM-OCR-bf16
hf download PaddlePaddle/PP-DocLayoutV3_safetensors --local-dir models/PP-DocLayoutV3
```

For reproducibility, add `--revision` with the commit you have validated. Model
licenses remain separate from PDF Craft's license.

### Start recognition on Metal

In a dedicated terminal, replace the model path with an absolute local directory:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
.venv-mlx/bin/python -m mlx_vlm.server \
  --host 127.0.0.1 --port 8080 \
  --model /absolute/path/models/GLM-OCR-bf16 --max-num-seqs 1
```

Check `http://127.0.0.1:8080/health`. The tested MLX version serves
`/v1/chat/completions`; older upstream instructions may use `/chat/completions`.
Use the route supported by your installed runtime, not a guessed URL.

### Start the full SDK pipeline

Copy [the sample configuration](../examples/glm-ocr-sdk.yaml) to a local file.
Replace its two absolute model paths. Keep CPU layout, MaaS disabled, retention
mapping, and both formatter-merge switches as provided.

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
.venv-sdk/bin/python -m glmocr.server --config /absolute/path/glm-ocr-sdk.yaml
```

Check `http://127.0.0.1:5002/health`. The SDK service then sends region images only
to the configured localhost MLX service. Bind both services to loopback; the SDK
recipe does not implement authentication. An optional client `api_key` is useful
only behind a service/proxy that actually enforces it.

**Do not use the SDK defaults unchanged:** they discard footnotes and other page
regions. `label_task_mapping` in the sample retains those regions. Disabling
`enable_merge_text_blocks` and `enable_merge_formula_numbers` keeps their source
geometry separate. Native detector labels are preserved by this SDK revision in
`native_label`, even when the display `label` says only `text`.

Offline environment flags prevent Hugging Face lookups; they are not a general
network firewall. Local validation additionally blocked non-loopback Python
socket/DNS access. Review server configuration and use OS/container-level network
isolation if stronger privacy enforcement is required. There is no cloud fallback
in the PDF Craft adapter, but it cannot enforce the remote service's behavior.

## Convert a PDF

```python
from pdf_craft import ExtractionOptions, GLMOCRServiceConfig, PDFCraft, PDFOptions

craft = PDFCraft(pdf=PDFOptions(ocr=GLMOCRServiceConfig(
    endpoint_url="http://127.0.0.1:5002/glmocr/parse",
    timeout_seconds=180,
)))
extraction = craft.extract_pdf(
    "input.pdf",
    "book.pcex",
    ExtractionOptions(includes_footnotes=True),
    analysing_path="pdf-craft-output/glm-first-run",
)
craft.render_markdown(extraction, "book.md")
craft.render_epub(extraction, "book.epub")
```

Use a **fresh analysis directory** when switching OCR backends or changing service
configuration. Existing OCR page caches can bypass recognition. Once `.pcex` is
saved, rendering it again does not require either OCR service to be running.
Stop the servers explicitly when finished; PDF Craft does not manage them.

## Behavior and limitations

- `endpoint_url` is the complete SDK `/glmocr/parse` URL, not an MLX URL or a
  generic OpenAI `base_url`. Defaults to `http://127.0.0.1:5002/glmocr/parse`.
- `api_key=None` by default; credentials are hidden from configuration repr.
  `timeout_seconds=180` must be finite and positive.
- This is a separate `ServiceOCRConfig` mode, not CUDA-local or vendor OCR.
  PDF Craft does not download/load its models; `predownload_models` is unsupported.
  `ocr_size` and device numbers do not select SDK/MLX settings.
- The SDK returns normalized 0–1000 boxes; the adapter converts them to the exact
  submitted image's pixel dimensions and preserves reading order/native labels.
- Footnotes are retained only when detected, retained by SDK configuration, and
  requested through `includes_footnotes=True`. PDF Craft's existing chapter parser
  also requires a supported paired inline/definition mark (for example `①`);
  unreferenced definitions and ordinary ASCII-numbered notes may be omitted from
  PCEX and rendered outputs. This backend does not change that contract.
  Upstream structuring intentionally
  puts headers/footers/page numbers in its ignored collection rather than body
  text; this does not enable OCR-based furniture extraction in PDF Craft.
- The SDK currently returns empty usage. Zero reported token counters mean
  **unavailable**, not free/no-token inference. PDF Craft OCR token budgets are
  unsupported and rejected; set recognition generation limits on the SDK side.
- Cancellation is checked before/after HTTP; an in-flight request cannot be
  cancelled server-side. The timeout bounds connection/read waits, not a strict
  end-to-end wall-clock deadline for arbitrary streaming responses. No retries
  are added by the adapter.
- A missing structured result, invalid geometry, or unsupported label fails
  rather than inventing page coordinates. The adapter cannot detect content the
  SDK has already dropped, including failed/empty recognized regions.
- Real synthetic-page validation retained titles, body text, table HTML, a chart,
  captions and a footnote. A formula region was **missed by layout detection**;
  formula mapping is unit-tested but this is not evidence of perfect formula OCR.
  A follow-up fixture with a font-verified `①` pair was recognized as inline
  `\\textcircled{1}` and no footnote layout. Thus rendered footnote fidelity is
  **not validated**, even with a correctly drawn reference mark.
- A single synthetic page took about 5.35 s through the SDK/adapter; MLX reported
  approximately 3.12 GB peak GPU memory. These are observations, not throughput or
  whole-system memory requirements. Long-book, low-memory, rotated-page, and
  translated-PDF quality remain unvalidated.
- A two-page real PDF smoke test produced validated PCEX geometry/assets, Markdown
  tables/images and nonempty EPUB XHTML/assets. Rendering the PCEX with both
  services stopped passed. EPUB checks were structural, not a visual reader review.
  The host's unrelated Poppler `pdftotext -bbox` crash required disabling
  `includes_furniture` for that smoke test and also reproduces in the baseline
  unit suite.

See [the implementation plan](../plans/glm-ocr-apple-silicon.md) for release gates
and the remaining validation matrix.
