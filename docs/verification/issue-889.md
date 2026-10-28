# Formula whitespace projection (#889)

## Cause and repair

A canonical `取甲 乙。` could accept `<expression>甲 </expression><expression>乙</expression>`
because its concatenated text matched. Restoring the original formulas then
replaced the first expression's whole contents, including the accepted separator.
The same missing separator was already recovered correctly when neither expression
contained it: recovery placed it in a surviving tail. Acceptance was therefore
inconsistent with the later projection.

The PCEX adapter now declares its replacement tags to shared canonical recovery.
Boundary ownership is resolved using surviving fields before comparison, even
when concatenated text already matches. Content inside a temporary formula cannot
own its outer separator. Source context still determines whitespace mode; interior
formula gaps remain inside the formula. Ambiguous positions around empty tokens
cannot be collapsed together because those tokens restore real visible formulas.
Recovery remains transactional and does not alter words or punctuation.

This replaces the remaining hard-coded expression-padding fallback inside the
shared algorithm; it does not add a second cleanup pass. The earlier #886 helper
was already replaced by #888. Immutable math references, required empty slots and
formula identities remain necessary PCEX-specific behavior. The #887 lossless
chapter writers are unchanged and covered by full translation-to-writer tests.

## Offline verification

- Replaying the new complete-fill regression with the original recovery function
  produces three failures: left-internal, right-internal and repeated internal
  separator placement. The failure is the final adopted content, not a mocked
  intermediate assertion.
- The corrected complete path accepts all unambiguous placements in one fill
  call, retains source formula identities/geometry, and round-trips through both
  chapter writers. Math-reference/empty-slot, protected-mode, interior-math and
  ambiguous empty-slot cases are covered too.
- Final focused suite: **85 passed**. Full suite: **1,198 passed**. Pyright:
  **0 errors / 0 warnings**. Changed-file Pylint: **10/10**. `git diff --check` clean.

Logs and the filtered table configuration are in the ignored worktree directory
`pdf-craft-output/issue889-evidence/`.

## Paid implementation gates

All three gates used fresh output directories, actual remote providers and the
same final package source. Credentials were obtained at runtime using the oo
LLM configuration contract, without recording keys. No gate thresholds changed.

| Gate | Evidence directory | Result |
| --- | --- | --- |
| `python -m pdf_craft_tool.smoke.citation_footnotes` | `pdf-craft-output/issue889-citation-01/` | Exit 0; all three masks pass; pipeline completed; no accepted fallback |
| `python -m pdf_craft_tool smoke matrix` with the unchanged `table&formula.pdf` entry from `tests/smoke/vendor_real.json` | `pdf-craft-output/issue889-table-01/` | Exit 0; checks passed; Chinese Markdown and translated PCEX produced; all 22 paragraphs and the table translated; one text-free image preserved normally |
| `python -m pdf_craft_tool.smoke.fill_formula_loop` | `pdf-craft-output/issue889-formula-01/` | Exit 0; three consecutive complete rounds passed, nine cases total |

The formula gate records source fingerprint
`b58c9770cca6e1a1e199efa41a2e0f3e5720c19680c3619897d392171f0dceb3`, verified against
the final source after the runs. Each case used one translation request; fill
requests ranged from one to two. The one-call guarantee applies to the controlled
unambiguous recovery regressions, not every possible model response.

All six citation source/output previews were inspected: the large duplicated
footnote overlay is absent. The resulting Markdown and formula XML were also
read. These gates check execution, structural restoration and serialization, not
complete semantic translation quality. Visible limitations remain: header/folio
and continuation labels, clipped equation presentation in the citation PDF, and
formula-to-prose allocation mistakes in translated mathematical text (for example,
numbers/formulas can be restored beside the wrong translated phrase). This change
does not solve those separate quality problems or claim the output is flawless.

Independent Review must run its own fresh three gates on the reviewed source;
these implementation runs do not substitute for that acceptance.
