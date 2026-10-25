# EPUB translation

pdf-craft can translate an existing EPUB directly. This workflow reads the book's chapters, table of contents, and translatable metadata, then writes a new EPUB. It does not use OCR and does not require a PDF configuration.

Use it for either a target-language edition or a bilingual edition that retains the original text.

## Minimal example

Configure a text LLM that exposes an OpenAI-compatible Chat Completions endpoint, then choose a target language and submission mode.

```python
from pdf_craft import (
    ConcurrentExecutor, FixedCapacity, LLM, PDFCraft, SubmitKind,
)

llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="gpt-4.1-mini",
    token_encoding="o200k_base",
)
executor = ConcurrentExecutor(FixedCapacity(4))

PDFCraft().translate_epub(
    "source.epub",
    "translated.epub",
    target_language="zh",
    submit=SubmitKind.APPEND_BLOCK,
    llm=llm,
    executor=executor,
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
    executor=executor,
)

# A bilingual edition with visibly separate original and translated paragraphs.
PDFCraft().translate_epub(
    "source.epub", "book.bilingual.zh.epub",
    target_language="zh", submit=SubmitKind.APPEND_BLOCK, llm=llm,
    executor=executor,
)
```

The table of contents, XHTML document titles, and translatable book metadata are translated too. EPUB 2 NCX `docTitle` follows the translated book title, and `dc:language` is set directly to `target_language`. Structures that cannot safely gain separate blocks use inline append while chapter bodies retain the requested block layout.

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
    window=4,
    executor=executor,
)
```

See [retry and recovery policies](ERROR_HANDLING.md) for failure decisions and retry budgets.

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
    translation_llm=translation_llm, fill_llm=fill_llm, executor=executor,
)
```

Supply `llm`, or supply both specialized configurations. A translation-only configuration is not enough because the pipeline must also be able to repair malformed translated XML.

## Observe progress and structural failures

`on_translation_event` receives low-level `TranslationEvent` values as the translation
scope and its TOC, metadata, and chapter items progress. Character counts are based on
source text and are not token-based percentages. The same callback is supported by
package and PDF translation workflows.

See [retry and recovery policies](ERROR_HANDLING.md) for failure decisions and retry budgets.

```python
# Decide final recoverable failures with error_policy; see ERROR_HANDLING.md.
def allow_failure(failure):
    print(failure.stage, failure.targets, failure.affected)
    return True
```

## Input and output expectations

- Use a readable EPUB input and a different output path.
- The pipeline follows the EPUB spine and preserves the package's required `mimetype` entry.
- Technical metadata such as identifiers, dates, language markers, `meta`, and contributor fields is not sent to the LLM. The output language marker is set directly from `target_language`; other text metadata may be translated.
- The current implementation requires a recognizable table-of-contents file. An EPUB with a TOC file but no entries can proceed; one with no recognizable TOC fails instead of silently skipping that stage.
- An unrecoverable error means the destination should not be treated as a complete translation. Inspect the callback report and any `log_dir_path` output, then rerun to a fresh output path.

For endpoint, cache, or empty-response errors, see [Troubleshooting](TROUBLESHOOTING.md).


## Receive translated content before the EPUB is ready

Pass `on_body` to receive `EpubFragmentContent(epubcfi, original, translated)` for
accepted atomic fragments, which can be smaller than a paragraph. Text is plain
and excludes overlapping LLM context. The optional range CFI addresses the input
EPUB, not the translated output. `epubcfi` is `str | None`: XHTML retains precise
locations, while `text/html` or missing reliable location information yields `None`
without suppressing original/translated text. In mixed-format books, other
fragments keep their reliable CFIs. Check for `None` before using a location.
Only known location unavailability is downgraded; parsing, translation, unexpected
mapping errors and callback exceptions still propagate. Chapter paths relative
to a nested OPF, including `../`, are supported.

`on_metadata` receives one complete `MetadataContent` for OPF book metadata.
In bilingual modes it still separates translated metadata from the original;
this does not change the append mode used in the final EPUB. Body callbacks do
not report navigation, document-head titles, or book metadata.

Both callbacks accept synchronous or asynchronous functions. They are optional;
async functions are awaited, and exceptions abort the operation. The output EPUB
is published as a complete file after success. See [incremental callbacks](STREAMING.md)
for examples, CFI details, ordering and source lifetime.


See [error handling](ERROR_HANDLING.md) for strict defaults, source locations and task-wide all-fallback protection.

Temporary LLM limits are recovered by the executor's `RateLimitScope` without consuming
`LLM.retry_times`. See [shared rate-limit scopes](API_REFERENCE.md#shared-rate-limit-scopes).
