# Citation refinement and furniture exclusions (paid)

From the worktree root, with its local `.env` provider configuration:

```sh
poetry run python -m pdf_craft_tool.smoke.citation_footnotes \
  --footnotes both --output pdf-craft-output/citation-NEW-RUN
```

`--footnotes on` (the default) or `off` runs one mode. Both always enable
JEV + LLM refinement, then English translation and PDF fill with furniture.
Each mode requires a new directory and submits real paid provider requests;
no OCR/translation cache is shared between modes. The pinned three-page
`tests/assets/pdf/citation.pdf` is checked by hash and size. This is opt-in,
not default CI, and requires no `/tmp` runner.

The existing footnote mask and page-3 formula preservation gates remain.
Off mode requires no second OCR preparation. The gate copies the actual
finalized furniture input to `adopted-pages/`, without changing it, and checks
published PCEX furniture against its excluded source boxes. Substantial
containment (95% area) fails; missing page projection evidence also fails.
Off mode requires at least one excluded region to exercise this regression.
On mode can legitimately contain no exclusions; its report explicitly marks
`exercised=false` rather than claiming it tested the exclusion branch. `citation-checks.json` retains the result.
This checks propagation of decisions, not whether the model made good decisions.

Review must compare all three source/output pairs in each mode using multimodal
inspection. Excluded text must not be translated and refilled as furniture;
body and normal uncovered running heads/folios must survive, and the display
formula must remain intact. Original untranslated notes left on the source PDF
background are expected preservation, not evidence of furniture reintroduction.
Do not call a program exit alone visual acceptance. Record existing ownership,
continuation or layout defects separately, without claiming they were fixed.

Keep `book.pcex`, `translated.pdf`, `adopted-pages/`, previews, reports and visual
review conclusions in a stable evidence directory outside the disposable VGE
worktree before merging. No source images, paid responses or credentials belong
in Git.
