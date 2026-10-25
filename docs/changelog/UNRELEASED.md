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
  `completed_characters`/`total_characters` arguments with
  `completed_words`/`total_words`, and `source_character_count` with
  `source_word_count`. Add `TranslationItemKind.FURNITURE` for independent page
  furniture. See [the counting and event contract](../en/TRANSLATION_PROGRESS.md).
