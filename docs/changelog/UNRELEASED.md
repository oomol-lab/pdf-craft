# Unreleased

- Fix translation progress blind spots during PDF heading preparation, metadata,
  furniture and chapter translation ([#544](https://github.com/oomol-lab/pdf-craft/issues/544)).
  Standard XML translation emits progress after each finalized round, including
  allowed fallback/skip; retries and reused headings do not duplicate work.
- Pre-scan EPUB translation tasks for a complete total before translation, using
  the same source selection as the actual translator without buffering the book.
- **Breaking:** translation counters now use source words rather than characters.
  Each Han character counts as one word; other letter/number runs count as
  `ceil(length / 20)` words, ignoring combining marks in length. The shared
  default long-word allowance is 20 characters. Export `WordCounter`,
  `count_text_words`, and `MAX_WORD_LENGTH` for reuse.
- **Breaking:** replace the four `*_characters` event fields with `*_words`,
  `TranslationTask.character_count` with `word_count`, advanced translator
  `total_characters` with `total_words`, and `source_character_count` with
  `source_word_count`. Independent furniture translation is included in source-word accounting. See [the counting and event contract](../en/TRANSLATION_PROGRESS.md).

- **Breaking:** unify extraction and translation progress under `ProgressEvent` with
  `STARTED` / `PROGRESS` / `COMPLETED`, integer operation-local unit IDs, integer
  per-unit stages and final `UnitOutcome`. Replace `ExtractionOptions.on_ocr_event`
  with `on_extraction_event`; remove the root OCR/translation event classes and
  old chapter/item fields. Operation starts contain required per-kind `unit_counts`;
  translation pre-scans counts alongside words without a unit plan. All accepted
  ignore/fallback/skip/cache paths settle their units. Operation completion waits
  for successful publication. See [migration and stage numbers](../en/TRANSLATION_PROGRESS.md).

- Remove advanced translation completed-word offsets and legacy scope/item event switches; scoped reporting is uniform whenever a callback is enabled.

- Allocate XML work-unit IDs on execution admission, including synchronous inputs,
  rather than eagerly allocating the book's units. Opaque custom batch translators
  report at their observable result-adoption boundary.
- Count independent image/table assets rather than request batches. Asset progress
  now covers decoder/identity validation and accepted recovery, so ignored decoder
  failures settle the same units and words as other fallback paths.

- Settle consumed XML tasks with no translation segments as `SKIPPED`, including
  synchronous/one-pass/async inputs and empty roots among nonempty tasks. Enabling
  progress callbacks no longer changes a successful empty-task result into failure.
- Classify EPUB document `head/title` fields as NAVIGATION, sharing the navigation
  aggregate while keeping OPF book metadata in METADATA, as documented.


- Start OCR-based book metadata from committed front pages while later OCR continues;
  native PDF metadata now has an immediate extraction unit and consistent manifest
  values regardless of callbacks.
- Schedule ready PCEX metadata, narrative, footnotes, assets and furniture through a
  shared bounded window. Release furniture by individual heading completion, retain
  original asset context, and deliver body/footnote callbacks independently in order.
- Keep pending dependencies and reorder results on disk, close progress SQLite
  connections explicitly, and avoid operation completion for partially consumed streams.

- Avoid waiting for impossible replacement events on unselected failed OCR caches;
  cancel and settle remaining PCEX jobs when a content stream is closed early.
