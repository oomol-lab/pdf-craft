# Issue 920: fill acceptance and exhausted-loop evidence

The implementation includes the completed #922 `safe_text` baseline through
merge commit ancestry. No dependency versions or PCEX schema were changed.

## What is covered

- Block and nested inline IDs share one unique namespace. Numeric IDs are
  interpreted consistently by structural validation, canonical validation and
  adoption. Duplicate IDs (including normalized collisions) cannot seed the
  hill-climbing baseline; repair feedback explicitly identifies the collision.
- Canonically equivalent Unicode text can be accepted without another model
  request. Only when needed, recovery uses NFD views and recalculates field and
  replacement boundaries. It compares the combined stream, so combining marks
  may span fragment or inline text/tail boundaries. Source objects are not
  rewritten; recovered candidates may use a canonically equivalent decomposed
  representation. Real letter/mark/punctuation and protected-space changes still
  fail; compatibility folding, case folding and removing accents are not used.
- Friendly XML accepts single quotes, XML attribute separators, spaces around
  equals signs and single-pass attribute entities. Duplicate attributes remain
  ambiguous and are rejected. Text entities are decoded once, after structure
  recognition, so escaped prose never becomes markup.
- `LLM.failure_log_path` enables generic protocol-loop evidence, independently
  of response caching. A loop streams attempt and transport records into a
  private staging directory. Only exhaustion publishes the directory and adds
  `failure_log_id` to the exception and detached failure metadata. Success,
  cancellation and non-exhaustion failure clean staging. Disk errors warn
  without changing the business result; partial records are marked incomplete.

## Public deterministic regression

`tests/test_fill_acceptance_regressions.py` uses synthetic Russian wording with
explicit `е + U+0308` and `и + U+0306` sequences, retaining the production failure
mechanism without customer names, locations, copyright text or URLs. It covers
whole fields, fragments, inline text/tail, identity collisions, exact letter
changes and legal XML spellings. `tests/test_llm_failure_log.py` covers 13-round
exhaustion, actual messages and parameter schedules, empty protocol responses,
ID propagation, ignored fallback, concurrent isolation, success/cancel cleanup,
credential redaction and disk failures. These public tests construct a local
byte tokenizer and need no downloadable tokenizer tables.

```sh
.venv/bin/python -m unittest tests.test_fill_acceptance_regressions tests.test_llm_failure_log
```

Both modules also passed with an empty `TIKTOKEN_CACHE_DIR` and TCP connections
blocked. The network seam alone is simulated; real template creation, repair
loop, validation, adoption, error policy and persistence are exercised.

## Private recorded case

No original documents or interaction logs are committed. The opt-in regression
reads a caller-supplied private directory and checks SHA-256 of the captured
initial messages, source unit, translation and third recorded response:

```sh
PDF_CRAFT_FILL_REPLAY=/path/to/private/replay-directory \
  .venv/bin/python -m unittest tests.test_fill_recorded_case
```

The directory layout is `initial-state/{initial-request.json,source-unit.xml,
canonical-translation.txt}` plus `run-20261031-094432/attempt-{01,02,03}.json`.
It needs the existing `o200k_base` tokenizer resources; the verified run also
blocked TCP connections. It restores the full six-fragment paragraph,
substitutes the already generated translation and recorded model responses,
and uses the current checkout's real fill/acceptance path. The first request
matches the captured initial messages, including token-length hints.

Results: recorded response 3 succeeds on attempt 1; the sequence of recorded
responses 1–3 succeeds on attempt 3. XML persistence and reloading retain the
translation up to canonical equivalence, all six page/source-order/bbox tuples
and reading order. OCR calls: 0. Real LLM calls: 0.

Historical reconstruction used engine `23fb019ed14fd53470d10cbd0aa15e6175614b6b`
and service `03ac7cb381952a8a861f1a3c7586606dcb25ff09`. The newly captured live
responses came from `oomol-chat`, whereas the historical incident named
`pcc-deepseek-flash`; they are new evidence, not the lost historical responses.
Unknown historical model routing/defaults or custom prompts remain unknown.
The old code rejected canonically equivalent response 3 through 13 attempts;
a diagnostic normalization experiment established the failure mechanism. The
candidate regression runs the actual implementation, without that experimental
normalization patch. No paid re-run or original PDF OCR is necessary.

## Verification commands

```sh
.venv/bin/python test.py
.venv/bin/pyright --pythonpath .venv/bin/python pdf_craft tests
.venv/bin/pylint pdf_craft tests
```

The interpreter argument makes Pyright use this worktree's dependencies. Pylint
may print its existing informational PySide6 extension-introspection notices;
these are not errors. See the managed implementation report for final test count
and commit. The private replay is opt-in and skipped in normal CI; its synthetic
Russian regression runs in ordinary CI without the private files.


Initial implementation validation: 1386 tests passed (3 expected skips), Pyright
reported 0 errors/warnings, and Pylint passed (10.00/10; informational PySide6
extension notices only). The 23 new public tests and the private recorded replay
also passed with TCP disabled; the public tests used an empty tokenizer cache.
The redacted Russian fixture was run against the unchanged merged baseline and
reproduced the old `XMLFillError`.

During an earlier full run, the existing furniture recovery test intermittently
raised `furniture ended with unresolved page dependencies`. On the unchanged
merged baseline `94be3e61f5e8ea72b6faf7e8814cd7b49c4ad75c`, 40 repetitions of
`tests.test_recovery_followup.FurnitureRecoveryTests.test_streaming_pdf_preserves_known_furniture_geometry`
reproduced 4 identical errors. All 10 tests in that module passed independently,
and the final full candidate suite passed. No furniture scheduler code was
changed for this issue; the intermittent baseline race remains a limitation.


## Review correction: combining marks and whitespace boundaries

The independent review rejected the initial implementation because removing
whitespace before canonical normalization could reorder combining marks across
that whitespace. For canonical `a\u0301 \u0323`, the response `a\u0323\u0301`
was incorrectly accepted and persisted as `a\u0323 \u0301`, which is not
canonically equivalent. The supplied counterexample and the precomposed form
were reproduced in the permanent full-path tests before this correction.

Recovery now decomposes each original code point without reordering across
characters, retaining its association with the field/offset where whitespace
must be restored. Whole-stream normalization in the preliminary comparison
keeps existing whitespace as a barrier. Before committing any response field,
the implementation constructs the expected complete stream using canonical
characters and source-context-approved gaps, and verifies the reconstructed
complete text for Unicode canonical equivalence. Matching gap offsets alone is
insufficient. A failure leaves the XML fields unchanged.

The added regression covers both combining-mark orders, absent and present
incorrect spaces, precomposed characters, fragment boundaries and inline
text/tail boundaries. Valid within-cluster reordering before a space still
succeeds, including cross-slot cases. Missing spaces after a precomposed
character can be recovered when ownership is unique; the accepted output is
saved and reloaded, with canonical equivalence and source geometry checked.
A separate table of 24 mark/space combinations checks either equivalent output
or transactional rejection. The production Russian replay and the earlier
positive acceptance/logging gates remain unchanged.


Correction validation: the full suite passed 1392 tests with 3 conditional skips;
Pyright reported 0 errors/warnings, and Pylint passed at 10.00/10 (only the
existing informational PySide6 extension notices). With TCP connections blocked,
the 28 public acceptance/evidence tests passed using an empty tokenizer cache;
21 whitespace tests plus the private recorded case passed using existing
cached tokenizer resources. The real recorded response still succeeds on the
first fill attempt, and recorded responses 1–3 still succeed on attempt 3.
No OCR, real LLM calls or changes to private evidence were involved.
