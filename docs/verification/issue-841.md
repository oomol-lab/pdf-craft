# Issue 841: TOC and chapter identity recovery

This is a deterministic simulation of failure mechanisms compatible with
[service #229](https://github.com/oomol/pdf-craft-service/issues/229). It does not
claim to recreate that production task's unknown OCR/refinement output, and no
live OCR, JEV, LLM, or service-image smoke was run. The service issue remains open.

## Red before implementation

Baseline: `b49d614db8476c78c0af5e7bd445535aa973e202` (latest main when work began).
Only `tests/test_toc_identity.py` was added before changing production code.

```text
.venv/bin/python -m pytest -q tests/test_toc_identity.py
5 failed, 1 passed in 0.87s
```

The initial six gates failed on:

- refined title changed to body/ignored: real chapter/TOC validation reported
  `chapter IDs do not match toc.xml item IDs`; recovery then tried image fallback;
- merged heading: the second TOC anchor disappeared from generated chapters;
- partial page recovery: the removed title's TOC entries remained dangling;
- sparse pages: real page 10 was treated as page 1, causing a read of nonexistent
  `page_1.xml`;
- final diagnostics: the original `ValueError` exposed no missing IDs or pages.

The unchanged identity-refinement/traditional control already passed.
Transient local output: `/private/tmp/issue841-red.txt`.

An additional full-chain gate was independently checked against an isolated
archive of the baseline, with only the new test module copied in. Its fake OCR
writes six usable page XML files and emits six COMPLETE events. Production TOC
analysis, fixed-response page repair, chapter generation, PCEX validation and
actual image recovery all run. On baseline:

```text
python -m pytest -q tests/test_toc_identity.py -k simulated_incident
E pdf_craft.error.NoUsableOCRPagesError: ... all requested pages fell back ...
  1, 2, 3, 4, 5, 6
1 failed, 11 deselected in 0.71s
```

Transient local output: `/private/tmp/issue841-incident-red.txt`. To independently
check this, archive the baseline into a fresh directory, copy only the test file,
and run the same command using the project environment, that directory as cwd,
and `PYTHONPATH` pointing to it. Do not copy the repaired package into the baseline.

## Green and contracts

The initial same six gates passed after implementation. Expanded gates cover:

- identity refinement and unrefined extraction, including repeated page-local
  citation index 1 with distinct `(page, index)` identities;
- role and ownership changes, strict/rejected recovery, explicit directory-only
  recovery, and full-chain simulated incident success without losing body text;
- multiple TOC anchors in a heading, cross-page sources, inline table and footnote
  reference preservation, and single-anchor multi-fragment titles staying intact;
- partial image recovery retaining chapter ID 3, never renumbering it to 1;
- surviving directory children lifted past unavailable parents without mutation;
- sparse real page IDs and selected-window directory detection, excluded printed
  TOC pages never becoming body anchors;
- missing/unexpected ID and known source-page diagnostics.

```text
.venv/bin/python -m pytest -q tests/test_toc_identity.py
14 passed in 0.77s
```

The simulated incident now produces a valid PCEX with all six body pages. The
first recovery is an explicit analysis decision to withdraw incompatible
refinement once. Independent anchors in a merged heading instead split at the
actual source boundary without needing fallback. No canonical text, reference,
source identity, or final PCEX consistency check is disabled.

The existing recovery suites also test actual all-page degradation failures,
callback/storage/fatal error propagation, local page replacement, original OCR
cache preservation, and no duplicate preview notifications.

Final verification on the implementation worktree:

```text
.venv/bin/python -m pytest -q tests
1090 passed, 64 warnings in 76.64s
.venv/bin/python -m pyright --pythonpath .venv/bin/python pdf_craft tests
0 errors, 0 warnings, 0 informations
.venv/bin/python -m pylint pdf_craft tests
exit 0, 10.00/10 (existing PySide extension introspection notices only)
```

The warnings are the existing pypdf detached-page replacement deprecations, not
new failures. No version, dependency, PCEX format, tag or release was changed.
