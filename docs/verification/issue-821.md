# PCEX fill formula loop (#821)

## Implemented contract

A PCEX paragraph's first-stage translation is the canonical text for XML fill.
The model can flatten LaTeX into ordinary symbols or prose. Temporary
`expression` elements follow the same structural and ID rules as other inline
slots; their content is not compared with source LaTeX inside the loop.
Structural validation and exact full-paragraph canonical validation still run
before a candidate enters the accepted mapping state. Only after the repair loop
accepts the complete result does the existing formula callback restore original
source formulas. Restoration is deterministic post-processing, not another fill
attempt or another comparison with the model's flattened translation.

The fill instructions explicitly describe this separation. Image/table anchors
retain their zero-width structural rules. PCEX source geometry, paragraph
boundaries and the public schema/API are unchanged.

## Deterministic regression

`tests/assets/translation/fill_formula_cases.json` contains the actual source
paragraphs and cached first-stage translations from the six-page DeepSeek-OCR
run on 2026-10-23. Its reference fill mappings are manually constructed valid
XML-like examples, **not historical model responses**. The three cases are the
page-1 abstract, page-3 compression-results paragraph and page-5/6 DeepEncoder
paragraph with its image anchor. The original PDF SHA-256 is
`dd5cec9ee94ebf4fa755a9e8d6155d83156df50af0294495910f5ac2d3cde5ea`.

The new tests reproduced three failures before the change. With the change,
all three complete production adapter/loop/codec paths accept flattened formula
text and then restore the original formulas, preserving geometry and assets.
Other tests reject wrong prose until a corrected response arrives and still
reject missing/wrong tags and foreign IDs. The existing two-formula regression
now verifies ordinary fill followed by source-ID restoration rather than
requiring the LLM to reproduce source formula text. Existing canonical and
explicit-missing-result regressions remain enabled.

```sh
.venv/bin/python -m pytest -q tests/test_fill_formula_loop.py \
  tests/test_anchored_content_translation.py --disable-warnings
```

The fixed historical translations and 39 later captured fill responses remain
locally under `pdf-craft-output/fill-loop-regression-20261023/`. The original full
PDF run did not record fill responses; those 39 responses came from a subsequent
fill-only reproduction. They are not substituted for fresh acceptance requests.

## Repeatable real-model gate

This opt-in paid command uses the **current repository source**, not a deployed
service or another installed SDK. It does not call OCR/JEV, download models or
modify the raw PDF. It runs each fixed source paragraph through real translation,
fill and formula restoration, without reusing either stage's model responses.

First read `~/.agents/skills/oo/SKILL.md` and `references/llm-client.md` under
that skill. The repository's `pdf_craft_tool/runtime.py` invokes
`oo llm config --json` with captured output and uses the current account's key,
base URL and model in memory. Do not print/persist that JSON or inspect local
auth files. The local tool reads the existing repository `.env`.

From the repository root, choose a new output directory each time:

```sh
PDF_CRAFT_LLM_DEFAULT_PROVIDER=oomol \
PDF_CRAFT_LLM_DEFAULT_PROFILE=default \
.venv/bin/python -m pdf_craft_tool.smoke.fill_formula_loop \
  --output pdf-craft-output/issue821-review-NEW-RUN
```

The tool fixes temperature to **0.7** and top_p to **1.0**, uses the established
RetryPolicy(translation=8, analysis=6, fill=12), and disables response caching.
It requires three consecutive rounds, each with all three cases accepted.
A failed round resets the streak and stops; fix the candidate and start a new
run rather than accumulating nonconsecutive successes. Strict error policy
prevents fallback from being counted as success. The source/fixture/tool
fingerprint must remain unchanged during the run.

`acceptance.json` records the commit, source fingerprint, model, sampling
configuration, each case's translation/fill request counts and the final streak.
Each case directory retains full request/response JSON, protocol validation
feedback and `accepted.xml`. The output must round-trip as PCEX chapter XML,
retain source formula content and geometry, preserve its image anchor, and
contain no temporary expression/anchor/restoration markers. Credentials are not
serialized. Generated logs/cache/output stay ignored by Git.

## Implementation verification

- Full lightweight suite: **1002 passed**.
- Focused fill/anchored translation tests: **27 passed**.
- Pyright on source/tests/new tool: **0 errors / 0 warnings**.
- Pylint on changed source/tests/new tool: **10.00/10**, exit 0.
- `git diff --check`: clean.
- Diagnostic real-model run `pdf-craft-output/issue821-implement-live-02/`:
  three full rounds accepted without recovery. Fill request counts by case
  (abstract / contribution / DeepEncoder) were **5/2/2**, **4/2/2**, **3/2/2**;
  each case also made one new first-stage translation request. Temperature=0.7,
  top_p=1.0, model=oomol-chat. This preceded final unused-import/tool-signature
  cleanup, so the final-source gate is recorded separately below.

Review must independently run the command and inspect its transcripts; the
implementation's successful requests are evidence, not Review's own gate.


### Final-source real-model acceptance

The committed implementation **0c27bff** completed an independent new output run
`pdf-craft-output/issue821-implement-final-01/` with exit **0**. The final
`acceptance.json` records **passed=true, streak=3**, temperature **0.7**, top_p
**1.0**, model **oomol-chat**, and unchanged source/fixture/tool fingerprint
`c20e21a757d13068db3be7d254e4db54d92e282699abaeb4d4fb3ff35db63fb3`.

| Round | Abstract fill requests | Contribution fill requests | DeepEncoder fill requests |
| --- | ---: | ---: | ---: |
| 1 | 2 | 3 | 2 |
| 2 | 2 | 2 | 2 |
| 3 | 4 | 2 | 2 |

All nine cases made a fresh first-stage translation request and were accepted
without recovery. There were **9 translation requests and 21 fill requests**;
this is not a claim that every model response passed on its first attempt.
Every saved output passed formula/geometry/asset preservation and strict chapter
codec round-trip checks after restoration. An additional scan of all nine output
XML files confirmed no temporary expression/anchor or internal restoration
markers. The final-source fingerprint was checked again after the run.

Transcripts: `round-N/CASE/request-*.json` (complete messages and raw responses),
`validation-1.jsonl` (protocol outcome and repair feedback), `result.json`, and
`accepted.xml` beneath that output directory. No credentials are stored in these
records. This completion evidence does not replace Review's independent real
three-round test.
