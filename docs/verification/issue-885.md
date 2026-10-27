# Citation fill whitespace (#885)

## Root cause and contract

The failing three-page citation run completed OCR and all redaction checks,
then exhausted fill repair attempts because the candidate's visible text ended
in an extra space. The canonical translation did not contain that space.

The fill protocol's own friendly XML writer violated this invariant before a
model was involved: it stripped every text/tail boundary, wrapped values longer
than 35 characters in newlines and indentation, and inserted newlines around
inline children. The friendly decoder retains those characters. The segment
reader then collapses newline/indentation runs to a visible space, so encoding
and decoding a canonical long value adds exactly the leading/trailing spaces
that the validator rejects. Stripping tails also loses actual English word
boundaries. Thus templates demonstrated a whitespace-changing representation
while the same protocol required exact visible-text preservation.

The historical run retained canonical translations and the final diagnostic,
but not all failed raw fill responses. We do not claim every failed response
used identical formatting; the deterministic regression establishes the
protocol's own lossy representation and its direct effect on the real fill loop.

The writer now leaves all content inside the top-level block slots compact,
retaining text/tail characters exactly. Only the outer protocol wrapper can be indented.
Fill instructions explain that whitespace inside paragraph/fragment tags is
content. Decoding and canonical validation are unchanged: an actual extra space,
missing character or incorrect fragment boundary still requires correction.
No retry budget is increased and no candidate is accepted by trimming its text.

## Offline evidence

`tests/test_fill_text_protocol.py` reproduces the defect without network calls:

- A long citation-style footnote encoded by the production writer fails the
  production fill loop before the fix, even though the model supplies the exact
  canonical value. The fixed writer passes on the first attempt.
- Text/tail boundaries and deliberately present outer spaces survive encoding
  and decoding. Before the fix these tests fail on inserted indentation and
  removed spaces.
- A candidate that really adds a trailing space is still rejected until a
  corrected response arrives. Entity, structural and existing formula/anchor
  regressions remain enabled.

The local reproduction reports are `/tmp/885-protocol-before.log` and
`/tmp/885-protocol-after.log`; they are development artifacts, not fixtures.

## Paid acceptance

Use a new directory and the normal configured vendor/JEV/LLM resources:

```sh
PDF_CRAFT_LLM_DEFAULT_PROVIDER=oomol \
.venv/bin/python -m pdf_craft_tool.smoke.citation_footnotes \
  --output pdf-craft-output/issue885-live-02
```

The fresh run, report and original/output page previews live in that ignored
worktree-local directory. No conversion output, model response cache or
credential belongs in the commit. Reviewer acceptance must use another new
directory and independently inspect the resulting PDF, not reuse this run.

Final-source run `issue885-live-02` completed successfully: all three actual mask
checks and the historical geometry check passed, extraction and translation
completed, and a three-page English PDF was written with no accepted fallbacks.
All three translated previews were inspected against the original fixture:
the reported large duplicate footnote overlay is absent. Residual reference
marker/OCR imperfections remain; this is not a claim of perfect recognition.

An earlier fresh run `issue885-live-01` also passed before the final tightening
that confines indentation to the outer wrapper. It is retained as investigation
evidence, not substituted for final-source acceptance. The final tightening has
its own offline adjacent-inline regression and the independent `live-02` run.

Final verification: 42 focused fill/formula/anchored tests pass; the full 1,155
test suite passes on recheck; Pyright reports zero errors and Pylint scores
10/10. An earlier full run hit the previously observed title-readiness timing
assertion in `test_dependency_scheduling` (both subcases). A single isolated
recheck also hit one subcase. Ten further isolated runs pass with an instrumented
encoder that records **zero calls**, confirming that assertion's execution path
does not depend on the changed writer. No scheduler or test assertion was changed.
Keep `/tmp/issue885-tests-final.log` and `/tmp/issue885-tests-recheck.log` separate
when reviewing the failed and passing runs; do not report the first as green.
