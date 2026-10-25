# Translation progress and source words

Pass `on_translation_event` to PCEX, PDF, or EPUB translation. Standard
`XMLTranslator` workflows scan the selected source content before translation:
`START` includes `total_words` and `completed_words=0`, including metadata,
narrative, enabled asset fields, and enabled furniture. EPUB scans its actual
navigation, metadata, document titles and body task generator without calling a
model or retaining the whole book. Scanning adds local parsing work.

## Counting rule

`WordCounter` and `count_text_words` are exported from `pdf_craft` and use the same
rule as progress. This inherits Wiki Graph's mixed-language word convention:

- Each Unicode Han character is one word, including supplementary ideographs.
- A consecutive run of other Unicode letters/numbers is one word.
- Whitespace and punctuation separate words and do not count.
- Combining marks continue an existing word but do not increase its length.
- A non-Han word of length L counts as `ceil(L / MAX_WORD_LENGTH)` words.
  The current shared default **MAX_WORD_LENGTH is 20**: 20 characters count as
  one word, 21 as two, 60 as three and 61 as four. This is a project counting
  convention, not a linguistic tokenizer; kana and Hangul runs also use this cap.

```python
from pdf_craft import WordCounter, count_text_words

assert count_text_words("hello world 中文") == 4
counter = WordCounter()
counter.feed("hel")
assert counter.feed("lo中文") == 3  # cumulative, independent of chunk boundaries
counter.boundary()                 # real paragraph/field boundary
```

`feed` preserves word state across chunks and inline XML nodes; paragraphs and
fields end words. PCEX source fragments within an author paragraph share that
state. Prompt context, temporary formula/folio tokens and reused heading copies
are not new source work. Model tokenization and translated output length do not
change the count. The standalone counter accepts `max_word_length` for separate
applications; translation uses the one shared project default.

## Event contract

| Kind | Meaning |
| --- | --- |
| `START` | Source totals are available; translation has not started. |
| `ITEM_START` | A TOC, METADATA, CHAPTER or FURNITURE item begins. |
| `PROGRESS` | A translation round has a final accepted result or allowed fallback. |
| `ITEM_COMPLETE` | Processing of the logical item has finished. |
| `COMPLETE` | The translation scope finished normally. |

Each `PROGRESS` contains **cumulative**, not incremental, values:
`completed_words`, `total_words`, `item_completed_words`, `item_total_words`,
plus `item_kind` and `item_id`. A round spanning items reports each item's
contribution. Counts are monotonic, and a normally completed scope reaches its
total. A completed round can contribute zero words, for example the continuation
of a word already counted in an earlier source block.

The scope includes PDF heading/furniture preparation. Headings reused for body
rendering or furniture are counted once; independent furniture templates are
counted once per template, not per printed occurrence. Furniture has the single
item `(FURNITURE, "furniture")`; enabled image/table fields belong to their chapter.
PCEX's persisted TOC is derived/reused rather than independently translated, so it
is not counted again. EPUB navigation and document titles are separate translated
source fields and do count.

A rejected attempt that will retry does not advance progress. Final fallback,
preservation or skipping does: progress measures **processed source work**, not
translation quality, success rate, token usage, or a billing decision. ErrorPolicy
and coverage still describe failures. Cancellation, fatal errors and callback
failures propagate without a fabricated `COMPLETE`. A failed task can already
have reported some processed work, including its full source total.

`on_body`, `on_footnote` and `on_metadata` still deliver content, independently of
progress. Custom transformers that hide their own model loop can only report at
the adapter boundary; the per-round guarantee applies to the standard
`XMLTranslator` engine. Advanced callers supplying a genuinely streaming
`XMLTranslator.translate_elements` task iterable without a pre-scan may still
have an unknown total; high-level PCEX/PDF/EPUB workflows pre-scan their inputs.

## Breaking change

The four former `*_characters` event fields are replaced by the `*_words` fields
above. `TranslationTask.character_count` becomes `word_count`; advanced
translator `completed_characters`/`total_characters` parameters become
`completed_words`/`total_words`, and
`NarrativeXMLTransformer.source_character_count` becomes `source_word_count`.
There are no compatibility aliases. Update consumers before upgrading.
