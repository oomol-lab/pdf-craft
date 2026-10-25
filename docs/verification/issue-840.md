# GitHub #526 / #527: deterministic red → green

Baseline: `f58d101ef965d864edc731b661a11a3c154fdabf` (main, including the already merged #538). This task does not change the version or add release preparation changes.

## Before implementation changes

Added `tests/test_fill_text_protocol.py` first; ran:

```sh
.venv/bin/python -m pytest -q tests/test_fill_text_protocol.py
```

Result: **4 failed, 2 passed**. No production code or prompt changes existed at this point.

- Named/numeric entity decoding: actual `&lt;span&gt;`, expected `<span>`.
- Codec round trip: actual `Use &lt;span&gt; and &lt;xml&gt;.`, expected `Use <span> and <xml>.` (formatting indentation excluded).
- Real PCEX fill: `XMLFillError`, expected canonical `使用 <span> 标签。`, received `使用 &lt;span&gt; 标签。` after the configured two attempts.
- Real assembled system prompt: the required empty-inline-slot instruction was absent; the prompt instead required source padding and forbade empty spans.
- Controls already passed: tolerant raw comparisons/ampersands; the actual fill loop rejects source padding, then accepts the corrected empty slots.

Tests use real codec, prompt loading/assembly, structural validation and canonical validation. Only provider responses/first-stage translation are fixed; no network/model calls are made. The initial test-only commit allows reviewers to reproduce this baseline independently.


## Additional generic-path conflict

Before modifying `translator.py`, a further regression test ran through the public
`translate_element` API without a canonical validator. It failed because the real
user message contained `Canonical visible-text constraint:` and PCEX-only formula
instructions. The conditional was inside a lambda, so a callable was always
passed even for noncanonical callers. This test failed before moving that
condition outside the lambda, then passed after the fix.

## After implementation changes

The original six tests pass unchanged in their behavioral assertions. Extended
coverage totals **10 passing tests**, including generic XHTML fill, numeric XML
code points, invalid/unknown references remaining literal, noncanonical prompt
assembly, and CDATA not bypassing structure/full-text validation. The same
production validators still reject source padding and accept a corrected response.

Run the final test file against the baseline by placing only that test file in an
isolated baseline checkout (using the same Python environment). Both the codec and
assembled prompt positive regressions must fail. The initial test-only commit is
`186f473`; it reproduces the original **4 failed, 2 passed** result directly.

The final ten-test file was also copied into a clean `git archive` snapshot of
baseline `f58d101` and executed there (not by reverting the implementation
worktree). Result: **7 failed, 3 passed**. Failing positive regressions: entity
decoding, codec round trip, canonical PCEX escaped text, generic XHTML escaped
text, real system prompt empty-slot policy, noncanonical user prompt, and the
correct escaped response after rejecting CDATA. Controls passed: raw tolerant
text, invalid/unknown references, source-padding rejection/empty-slot acceptance.
The same final file passes **10/10** against the fix.

The temporary snapshot was created with `git archive BASELINE`, unpacked to a
fresh temporary directory, and received only `tests/test_fill_text_protocol.py`.
The worktree Python ran `-m pytest -q tests/test_fill_text_protocol.py` with that
directory as both working directory and `PYTHONPATH`. Snapshot files were removed
on exit. Raw local output is `/private/tmp/pdf-craft-840-final-tests-baseline.log`;
the durable evidence is the recorded baseline, test-only commit and assertions.

## Final verification

- `.venv/bin/python -m pytest -q tests`: **1076 passed**, 64 existing pypdf deprecation warnings.
- Focused codec/fill/formula/anchored/EPUB regressions: **55 passed**.
- `.venv/bin/python -m pyright --pythonpath .venv/bin/python pdf_craft tests`: **0 errors, 0 warnings**.
- `.venv/bin/python -m pylint pdf_craft tests`: **exit 0**, score 10.00/10; existing informational Qt extension notices only. Final changed files also lint cleanly.
- No model requests, OCR, downloads, or version changes were performed.

When creating the PR after review, state that both defects are fixed, include the
red/green results above, and use `Fixes #526` and `Fixes #527` so merge closes both.
