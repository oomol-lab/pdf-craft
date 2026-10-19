# One-shot bilingual conversion (#793)

## Failure before implementation

On baseline `f641cc7`, the public Markdown and EPUB one-shot APIs accepted
`SubmitKind.APPEND_BLOCK` but submitted both source and translated fragments into
one PCEX chapter. The real workspace validator then rejected duplicate source
identities before rendering.

Commit `e7d813f` contains only tests and the original
[red output](append-793-red.txt), before any package implementation changes.
The run produced 3 failures and 1 pass: both public APPEND_BLOCK tests reproduced
`duplicate fragment identity ('1', '0')`; the mode matrix also exposed formatting
loss in the deterministic asset-response fixture. That fixture was corrected to
retain whitespace-only markup slots required by the asset response contract;
no success assertions were removed or weakened.

For an additional check, the final tests were copied into an ignored checkout of
`e7d813f` and the original two public reproductions plus mode matrix were run
again. [Final-test baseline output](append-793-final-tests-red.txt) records
3 failures: both APPEND_BLOCK calls still fail with duplicate identity, while the
matrix rejects the old path's failure to create a separate translation layer.
The same final tests pass on the implementation.

## Resulting contract

- One-shot Markdown/EPUB and `translate_extraction` share the same async
  replacement translation and layer-creation implementation. One-shot conversion
  explicitly selects the newly appended layer, not a pre-existing default.
- Source chapters, fragment keys and bounding boxes are unchanged. A translation
  layer has replacement text and matching source geometry. The uniqueness
  validator, archive format and package/dependency versions are unchanged.
- REPLACE selects replacement rendering. APPEND_TEXT selects the additive
  `RenderMode.BILINGUAL_TEXT`: rich translated content is appended within each
  matching fragment. APPEND_BLOCK selects the existing BILINGUAL view, which
  emits the source body paragraph before its translated paragraph. Combined
  display views are temporary renderer inputs, never persisted PCEX chapters.
- Explicit preserved footnote coverage is honored for both replace and bilingual
  display. Older layers without footnote entries keep their prior availability
  behavior. A separate red run of the preserved-footnote assertion preceded this
  correction (`coverage-red.log`), followed by the passing same assertion.
- Headings/TOC remain one combined title. Images and tables occur once. Asset
  coverage is applied before inserting bilingual paragraphs, while its source
  indexes are still stable. Table cells are combined in place rather than
  appending a second table that EPUB would not consume. Footnote definitions
  follow the selected paragraph style; repeated noterefs have unique XHTML
  anchors and share one definition/backlink target.
- Metadata is overlaid when rendering, leaving source metadata intact. One-shot
  EPUB retains the previous explicit `lan` / source language / `zh` selection,
  including when the translator's target is a human label such as `English`.
  The stricter public stored-layer language contract remains unchanged.
- PDF writeback retains its replacement-only root view and metadata overlay,
  using the shared replacement translator. Existing EPUB XML submit semantics
  are unchanged. No retries, fallback output, prompts or exception suppression
  were added.

## Coverage and verification

`tests/test_one_shot_submission.py` uses a deterministic response instead of a
network service and replaces the OCR extraction boundary with a small valid
fixture. It runs real segmentation, template validation, XML submission,
NarrativeXMLTransformer, workspace validation, layer creation and renderers.
Both public facades and output formats exercise all three submit kinds. EPUB
XHTML is parsed for paragraph grouping, text, asset counts and unique IDs.

The fixture includes heading/TOC, a multi-fragment paragraph interrupted by an
image, another paragraph with a table after earlier bilingual insertions, an
inline reference/footnote, metadata, and identical source/translation text.
Additional tests verify preserved coverage (including explicit preserved footnote
paragraphs), skipped anchored translation,
selection with an older translation present, archive reopen/export, unchanged
source XML and matching unique fragment identities/bboxes in the translation,
and generic XHTML submission modes. Existing malformed-archive rejection and
PDF/footnote regression tests remain in the full suite.

Final verification on Python 3.13:

- `python -m pytest -q`: **848 passed**.
- `python test.py`: **820 tests, OK**.
- `python -m pyright --pythonpath <verification-python> pdf_craft tests`:
  **0 errors, 0 warnings**.
- `python -m pylint pdf_craft tests`: **10.00/10**, exit 0.
- `poetry build`: wheel and sdist built, version remains **2.4.3**.
- `git diff --check`: passed.

The verification interpreter was the existing main workspace environment at
`/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/.venv/bin/python`; all imports
and tests ran against this worktree's code. The new worktree's Python 3.11 Poetry
install could not finish because the PySide6 Addons download broke repeatedly;
a pip retry also rejected the downloaded wheel's hash. No hash checks were
bypassed and no dependency declarations or lockfile were changed. Python 3.11
verification is therefore left to the normal CI environment. Build uses Poetry's
isolated build backend and does not depend on that incomplete runtime install.
Logs are retained under the ignored `pdf-craft-output/append-793/` directory.
No live LLM/JEV, OCR, CUDA, model download or full PDF conversion was performed.
