# EPUB Translation Guide

pdf-craft can translate an existing EPUB into either a target-language edition or a
bilingual edition while preserving the book's chapters, table of contents, images,
and layout as far as the source EPUB allows. EPUB translation uses an OpenAI-compatible
text LLM; OCR configuration is not involved when the input is already an EPUB.

## Minimal example

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

`target_language` accepts a language code or name, such as `"zh"`, `"en"`, or
`"Japanese"`.

## Choosing how translations appear

The `submit` argument controls how translated text is written into the EPUB:

| Mode | Output | Best for |
| --- | --- | --- |
| `SubmitKind.REPLACE` | Replaces the source with the translation | A target-language-only edition |
| `SubmitKind.APPEND_TEXT` | Places the translation directly after the source text | A continuous bilingual reading flow |
| `SubmitKind.APPEND_BLOCK` | Adds the translation as a separate block after the source | Clear bilingual comparison; recommended |

The table of contents and book metadata follow the same mode. With `APPEND_BLOCK`,
the translated table of contents and metadata are kept inline so the EPUB structure
remains usable.

## Adjusting translation behavior

Pass a custom prompt, retry limit, or concurrency level through `translate_epub`:

```python
PDFCraft().translate_epub(
    "source.epub",
    "translated.epub",
    target_language="zh",
    submit=SubmitKind.APPEND_BLOCK,
    llm=llm,
    user_prompt="Use formal language and preserve names, terminology, and footnote numbers.",
    max_retries=5,
    concurrency=4,
)
```

Start with `concurrency=1`. Increase it only after checking your provider's rate
limits and concurrency allowance. Output order remains stable. `max_retries` controls
how many times an individual translation or structure-repair attempt may be retried.

## Cache and recovery

Set `cache_path` on the LLM to store completed requests locally. If a translation is
interrupted, running it again with the same setup can reuse cached results and avoid
repeating completed requests:

```python
llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="gpt-4.1-mini",
    token_encoding="o200k_base",
    cache_path="translation-cache",
)
```

Use separate cache directories for separate books or translation jobs to keep cache
management and troubleshooting straightforward.

## Using two LLMs

EPUB translation handles both language translation and occasional XML structure repair.
By default, the same `llm` handles both jobs. To tune them independently, pass
`translation_llm` and `fill_llm`:

```python
translation_llm = LLM(
    key="your-api-key", url="https://api.openai.com/v1",
    model="gpt-4.1-mini", token_encoding="o200k_base", temperature=0.7,
)
fill_llm = LLM(
    key="your-api-key", url="https://api.openai.com/v1",
    model="gpt-4.1-mini", token_encoding="o200k_base", temperature=0.2,
)

PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.APPEND_BLOCK,
    translation_llm=translation_llm,
    fill_llm=fill_llm,
)
```

## Progress and failure callbacks

`on_progress` receives a value between `0.0` and `1.0`. `on_fill_failed` receives a
`FillFailedEvent` when XML structure repair fails or is retried. When
`over_maximum_retries` is `True`, the error has exhausted its retry budget and may
affect the final EPUB:

```python
from pdf_craft import FillFailedEvent

def on_progress(progress: float) -> None:
    print(f"Translation progress: {progress:.0%}")

def on_fill_failed(event: FillFailedEvent) -> None:
    if event.over_maximum_retries:
        print(f"Unrecovered structure error: {event.error_message}")

PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.APPEND_BLOCK,
    llm=llm, on_progress=on_progress, on_fill_failed=on_fill_failed,
)
```
