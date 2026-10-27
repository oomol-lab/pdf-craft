# citation.pdf second-pass redaction

The three-page fixture `tests/assets/pdf/citation.pdf` has SHA-256
`e42e3288d80f500009535ce3e47fab6a31e0e4644ead323917a2ea150de3f56e`.
The previous interval-splitting algorithm truncated a wide mask at a lower,
narrower box and never resumed the wide mask to its right. The reported
two-box example leaves pixels `(763, 1898, 1746, 2530)` uncovered (Pillow bbox).

Redaction now paints the upper two thirds once, then each recognized box's
horizontal span from that boundary down to its bottom. Boxes ending above the
boundary are omitted. Overlap is intentional; no interval splitting is needed.
The same rectangles are retained for clipping second-pass OCR output. Both
vendor and local double-pass paths use this helper.

## Verification on 2026-10-27

- The historical two-box pixel-union check fails against the previous committed
  helper and passes with this change. Offline regressions also cover nested,
  crossing and fully truncated boxes, input permutations, and excessive masking.
- Ran the opt-in paid gate with a fresh output directory:

  ```sh
  PDF_CRAFT_LLM_DEFAULT_PROVIDER=oomol \
  .venv/bin/python -m pdf_craft_tool.smoke.citation_footnotes \
    --output pdf-craft-output/citation-footnotes-union-20261027-01
  ```

- Real DeepSeek vendor OCR, JEV, LLM refinement, English translation and PDF
  writeback completed with strict error policy, without accepted fallbacks.
  `citation-checks.json` reports `automated_passed=true`: all three pages have
  neither missing nor extra mask pixels, and the historical check passes.
- Inspected all three source/translated preview pairs. The reported page-1
  right-hand duplicate footnote overlay is absent; no analogous duplicate
  overlay was observed on pages 2–3. This is targeted visual acceptance, not a
  claim that every OCR classification or translation is perfect.
- OCR input pairs, actual first-pass boxes/masks, layout plots, XML, PCEX and
  the translated PDF remain in the ignored run directory. No credentials or
  generated conversion artifacts are committed.

The gate's usage and evidence requirements are documented in
[`pdf_craft_tool/README.md`](../../pdf_craft_tool/README.md#citation-脚注遮罩付费回归).
