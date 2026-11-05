# Blank translation units and unbound formula references

Related service investigations: oomol/pdf-craft-service#310 and
oomol/pdf-craft-service#313. This change concerns the engine only.

## Acceptance and recovery

Unicode whitespace alone has no translation work. The translator tests for work
without rewriting the text, then submits cloned source segments. This retains
NBSP, other special spaces, inline nodes and empty paragraphs. Read-only context
does not turn an otherwise blank primary unit into a model request. This is not
a change to CSS whitespace normalization or the batch response contract: empty
responses to actual prose remain invalid.

The translation prompt described source formula references even for units with
no formula table. Previously only units with source formulas validated that
protocol. An invented reference could therefore become canonical text: inserting
it during fill violated structure, while dropping it violated canonical text.

Translation now rejects newly introduced source-math markup when there is no
source formula table, including paired, incomplete and case-variant markup.
Exact literal markers already present in source prose may survive unchanged;
they grant no additional identities. No response content is stripped to make it
pass. Invalid references consume the existing translation repair budget and
receive feedback alongside the original input. Exhaustion uses the existing
translation failure policy before any fill request. Valid batch siblings remain
accepted. Envelope failures still resend the original request without treating
feedback as source prose. Real formula identity, occurrence and value validation,
structure validation and canonical fill validation remain enabled.

## Offline evidence review

Only aggregate observations are recorded here; private documents, prompts and
model responses are excluded from the repository.

- #310: 198 distinct physical responses included 1,776 blank entries whose
  corresponding input was exactly U+00A0, plus one unparseable response. The
  latter remains subject to normal envelope recovery. The 199 logged loops do
  not represent 199 lost prose paragraphs.
- #313 main sample: all 12 initial fill batches contained a unit whose translated
  text introduced source-math absent from its source. Feedback contained 67
  structural errors and 89 canonical mismatches. This repair prevents those
  invalid translations from reaching fill.
- Additional samples 315a/315b contained 6/4 fill loops and no source-math
  markers. They cannot be assigned to this cause. Across their saved attempts,
  28/9 had no single complete XML block; the other 50/43 still failed a separate
  replay of current canonical whitespace recovery. That replay does not prove
  a new engine defect or complete structural acceptance. These remaining fill
  failures require separate investigation; this change does **not** establish
  that every symptom reported under #313 is resolved. Do not close #313 solely
  on the basis of this repair. Balance exhaustion and progress-count terminal
  failures are outside this change.

## Deterministic verification

`tests/test_translation_protocol_gaps.py` uses synthetic text and stub providers.
The first five regressions produced 13 errors on the original implementation;
after repair they passed. Coverage includes blank and mixed prose units, special
spaces, inline nodes, literal protocol documentation, invalid reference variants,
translation exhaustion before fill, strict empty batch entries and mixed batch
repair without retrying successful siblings.

Together with `tests/test_source_math_identity.py` and
`tests/test_request_batch.py`, the focused command runs 32 tests. It also checks
real formula preservation, changed/missing/duplicate/unknown formulas, reordered
identities, malformed envelopes and bounded retry behavior:

```sh
poetry run python -m unittest tests.test_translation_protocol_gaps tests.test_source_math_identity tests.test_request_batch -q
```

No live model API, model download, deployment, release or service dependency
change is part of this verification.

Repository checks completed during implementation:

- `poetry run pyright pdf_craft tests`: no errors or warnings.
- `poetry run pylint pdf_craft tests`: 10.00/10; existing informational Qt
  extension introspection notices only. Final changed Python files also passed.
- `poetry run python test.py`: 1,891 tests, six skipped, successful (125 seconds).
  That discovery preceded the last three regression additions; the final focused
  32-test run passed after those additions and the case-variant formula guard.
- `git diff --check`: successful.
