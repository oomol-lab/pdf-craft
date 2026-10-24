# Issue 835: EPUB label language and configured PDF preflight

The implementation starts from main commit `77c56d0` (PR #529). It leaves the
pinned `epub-generator==0.1.7` dependency unchanged.

## Fixed labels

The SDK's synchronous/asynchronous `render_epub` and `convert_pdf_to_epub`, and
`EpubRenderer.render`, now accept `label_language: str = "en"`. Exact `zh` selects
Chinese labels; `en` and all other strings use English. The final dependency
call still uses its own `lan` keyword, after normalization in one helper.
Source and translator languages never select or restrict this label catalog.
Docstrings and the conversion reference explain why it must not become a
content-language whitelist again.

`tests/test_epub_label_language.py` exercises the real generator and ZIP output:

- Fourteen label values, including Korean names/codes, French, Japanese, Arabic,
  regional codes, empty/unknown values and non-exact Chinese spellings.
- Korean source paragraphs and footnotes remain in both output and callbacks.
- Default labels remain English even with Chinese source metadata.
- Stored Korean translation layers and streaming translation render identically;
  metadata callbacks and translator/layer language remain `ko`.
- A synchronous facade renders the stored Korean translation; facade signatures
  match the async entry points and no longer expose `lan`.
- PDF one-shot conversion runs real PDF preflight with deterministic extraction
  and translation substitutes, forwarding explicit Chinese labels and falling
  back for Korean labels without changing the target language.

Model responses/OCR are test doubles. No paid or network model requests were
made. The dependency's separate hard-coded `zh` OPF/XHTML language markers are
not changed or claimed fixed; the API docs and release notes state that limitation.

## Poppler configuration

`DefaultPDFHandler.poppler_path` exposes the configured executable directory.
Task-local preflight inherits native component configuration without requiring
new fields on arbitrary custom handlers. The same directory reaches default
readers and native-text extraction. Cache keys include the explicit directory
or PATH lookup configuration, so a successful installation cannot validate a
different one. Configuration is reset after the top-level call.

Qt composition runs in a separate process, so the resolved directory is frozen
into its native default handler before transport rather than relying on ContextVars
crossing the process boundary.

`tests/test_poppler_configuration.py` performs seven regressions using actual
local tools and small generated PDFs:

- An empty PATH plus a valid explicit tool directory permits public page count,
  direct preflight and rendering; bad/default-missing directories still fail.
- Nested operations render once per task, recheck on the next call, and reject
  another directory despite a cached success.
- Native patcher/pipeline configuration is retained; identity patch output is
  byte-for-byte unchanged.
- An opaque custom handler is not probed for new fields.
- Changing PATH within a scope does not reuse a previous lookup configuration.
- A facade/engine/extractor composition preserves the explicit directory for
  preflight and `pdftotext`, without duplicate preflight rendering.
- Actual Qt PDF composition succeeds with Poppler absent from PATH, using an
  inherited directory for background sampling. Only Ghostscript is placed on
  the temporary PATH for its independent visual-base compilation. The output
  is reopened with pypdf and its translated text is verified.

## Release preparation

Version is 2.6.2, with a changelog covering PR #529 and this issue. Public API,
installation, format and translation docs and localized README descriptions are
updated. No tag, release, PR binding or publication is performed during Implement.

## Validation

- Standard `.venv/bin/python test.py`: **1003 tests passed** on the final code.
  A separate full run with per-test environment diagnostics also passed all 1003.
- EPUB labels / Poppler configuration / existing PDF smoke subset: **13 passed**
  before the final additional configuration case; final configuration suite:
  **7 passed**. All new cases are included in the standard runner above.
- Pyright (`pdf_craft tests pdf_craft_tool/smoke/runner.py`): **0 errors**.
- Pylint on the same scope: **10.00/10**, exit zero (existing Qt informational
  introspection notices only).
- `poetry check --lock`: passed. `poetry build`: sdist and wheel succeeded;
  built wheel metadata reports **2.6.2**.
- `git diff --check`: passed. No hosted model calls or source PDF rewrites.

## Submit integration with updated main

Main merged PR #530 while this PR was running CI. Both installation notes are
retained, and the release notes include the pypdf 6.21.2 requirement. Revalidated
with pypdf 6.21.2: all 1003 tests passed (58.324s), Pyright reported zero errors,
Pylint rated 10.00/10, and lock validation plus sdist/wheel builds passed.
The real Qt composition test normalizes extracted whitespace because the updated
parser may return a line break between words; it still verifies both translated
words are searchable in their original order.
