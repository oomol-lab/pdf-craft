# EPUB translation

pdf-craft can translate an existing EPUB directly. This workflow reads the book's chapters, table of contents, and translatable metadata, then writes a new EPUB. It does not use OCR and does not require a PDF configuration.

Use it for either a target-language edition or a bilingual edition that retains the original text.

## Minimal example

Configure a text LLM that exposes an OpenAI-compatible Chat Completions endpoint, then choose a target language and submission mode.

```python
from pdf_craft import LLM, PDFCraft, SubmitKind

llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="gpt-4.1-mini",
    token_encoding="o200k_base",
)

PDFCraft().translate_epub(
    "source.epub",
    "translated.epub",
    target_language="zh",
    submit=SubmitKind.APPEND_BLOCK,
    llm=llm,
)
```

`target_language` accepts a language code or a language name, such as `"zh"`, `"en"`, or `"Japanese"`. `LLM` holds the endpoint, model, credential, and token-encoding details for the text service. It is separate from the OCR configuration used by PDF workflows.

## Pick the reading experience first

`submit` controls how translated chapter text is incorporated into the book.

| Mode | Result | Best for |
| --- | --- | --- |
| `SubmitKind.REPLACE` | Replaces the original with the translation. | A target-language-only edition. |
| `SubmitKind.APPEND_TEXT` | Adds the translation directly after the original in the same text flow. | Compact bilingual reading. |
| `SubmitKind.APPEND_BLOCK` | Adds the translation as a separate block after the original. | Side-by-side-in-sequence bilingual reading; usually the clearest choice. |

For example:

```python
# A Chinese-only edition.
PDFCraft().translate_epub(
    "source.epub", "book.zh.epub",
    target_language="zh", submit=SubmitKind.REPLACE, llm=llm,
)

# A bilingual edition with visibly separate original and translated paragraphs.
PDFCraft().translate_epub(
    "source.epub", "book.bilingual.zh.epub",
    target_language="zh", submit=SubmitKind.APPEND_BLOCK, llm=llm,
)
```

The table of contents and translatable book metadata are translated too. Their structure cannot safely gain separate blocks, so `APPEND_BLOCK` is treated as inline append for those two areas while chapter bodies retain the requested block layout.

## Configure the text LLM

`LLM` is a declarative configuration object. Common options are:

| Field | Meaning |
| --- | --- |
| `key` | Provider API key. |
| `url` | OpenAI-compatible base URL. |
| `model` | Model identifier accepted by that endpoint. |
| `token_encoding` | Encoding used to count input tokens, for example `o200k_base`. |
| `timeout` | Per-request timeout in seconds. |
| `temperature`, `top_p` | Sampling controls. |
| `retry_times`, `retry_interval_seconds` | Retry policy for transport failures or empty replies. |
| `cache_path` | Directory for successful text-request cache entries. |
| `log_dir_path` | Directory for request and cache logs. |

```python
llm = LLM(
    key="your-api-key",
    url="https://your-provider.example/v1",
    model="your-translation-model",
    token_encoding="o200k_base",
    timeout=120,
    retry_times=5,
    retry_interval_seconds=6,
    cache_path="translation-cache",
)
```

The default `retry_times=5` means up to five retries after the first request. Cache entries are retained only for successful requests, allowing a rerun to reuse finished translation work. Give unrelated books or translation jobs separate cache directories so they are easier to inspect and discard.

## Tune translation behavior

`translate_epub()` accepts the following options in addition to the required paths, language, and submission mode:

```python
PDFCraft().translate_epub(
    "source.epub",
    "translated.epub",
    target_language="fr",
    submit=SubmitKind.APPEND_BLOCK,
    llm=llm,
    user_prompt=(
        "Use formal written French. Preserve proper names, technical terms, "
        "and footnote numbers."
    ),
    max_group_tokens=2600,
    concurrency=4,
)
```

- `user_prompt` adds project-specific requirements such as terminology or tone. It supplements rather than replaces pdf-craft's structural instructions.
- `max_group_tokens` defaults to `2600`. Larger groups make fewer, larger requests and increase the cost of retrying a failed request.
- `concurrency` defaults to `1`. Increase it gradually only after confirming the provider's rate limits and cost behavior. Output order remains stable.
- `max_retries` controls XML structure-repair attempts and defaults to `5`. It is distinct from `LLM.retry_times`, which controls text-request retries.

### Use separate translation and repair models

The same LLM handles prose translation and XML structure repair when only `llm` is provided. If those jobs need different models or sampling settings, provide `translation_llm` and `fill_llm` instead:

```python
translation_llm = LLM(
    key="your-api-key", url="https://example.com/v1",
    model="translation-model", token_encoding="o200k_base", temperature=0.7,
)
fill_llm = LLM(
    key="your-api-key", url="https://example.com/v1",
    model="structure-model", token_encoding="o200k_base", temperature=0.2,
)

PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.APPEND_BLOCK,
    translation_llm=translation_llm, fill_llm=fill_llm,
)
```

Supply `llm`, or supply both specialized configurations. A translation-only configuration is not enough because the pipeline must also be able to repair malformed translated XML.

## Observe progress and structural failures

`on_progress` receives a value from `0.0` to `1.0` as work completes. Chapter work receives most of the weight; a present table of contents and translatable metadata each receive five percent.

`on_fill_failed` receives `FillFailedEvent` when an XML repair attempt fails. The final event with `over_maximum_retries=True` signals that no repair attempts remain and the output may need inspection.

```python
from pdf_craft import FillFailedEvent

def show_progress(value: float) -> None:
    print(f"{value:.0%}")

def report_fill_failure(event: FillFailedEvent) -> None:
    if event.over_maximum_retries:
        print(f"Unrecovered EPUB structure error: {event.error_message}")

PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.APPEND_BLOCK,
    llm=llm, on_progress=show_progress, on_fill_failed=report_fill_failure,
)
```

## Input and output expectations

- Use a readable EPUB input and a different output path.
- The pipeline follows the EPUB spine and preserves the package's required `mimetype` entry.
- Technical metadata such as identifiers, dates, language markers, `meta`, and contributor fields is not sent to the LLM. Other text metadata may be translated.
- The current implementation requires a recognizable table-of-contents file. An EPUB with a TOC file but no entries can proceed; one with no recognizable TOC fails instead of silently skipping that stage.
- An unrecoverable error means the destination should not be treated as a complete translation. Inspect the callback report and any `log_dir_path` output, then rerun to a fresh output path.

For endpoint, cache, or empty-response errors, see [Troubleshooting](TROUBLESHOOTING.md).
