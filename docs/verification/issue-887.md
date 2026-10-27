# PCEX mixed-content serialization (#887)

## Root cause and reproduction

The captured `abstract-page1` response on source commit `463aeeb` passed the
production XML-fill protocol. Formula restoration then legitimately placed two
source `inline_expr` nodes next to each other with no text between them. The
in-memory fragment had 12 content members.

`chapter.encode()` subsequently called the generic XML indenter. That indenter
treated an absent or whitespace-only `.text`/`.tail` as disposable formatting:
it inserted a newline and spaces between the adjacent formulas. The mixed-content
decoder correctly retained this new text, returning 13 members. Formula ordering
and LLM validation were not the source of the extra member.

`tests/assets/translation/abstract_roundtrip_response.json` retains only the
original translation and final accepted fill response, without credentials or
duplicate prompts. Its deterministic production-adapter regression fails against
the pre-fix codec and passes after the fix. It still requires strict round-trip
equality and source-formula fidelity.

## Fix boundary

The XML indenter now allows callers to preserve named content subtrees. PCEX
chapter writers opt out `fragment` and asset `title`/`content`/`caption` owners,
including their HTML descendants. Structural containers remain indented. Both
`chapter.encode()` and the incremental `ChapterWriter` use the same policy.
Existing spaces, tabs and newlines are preserved; missing separators stay missing.
No decoder stripping, LLM retry change, relaxed assertion or PCEX schema change
is involved.

Offline checks cover formula-only and adjacent-formula content, whitespace-only
tails, nested HTML, reference bodies and asset fields through both writers.

## Independent fresh model run during implementation

From the issue worktree, using current source and a new directory:

```sh
PDF_CRAFT_LLM_DEFAULT_PROVIDER=oomol \
.venv/bin/python -m pdf_craft_tool.smoke.fill_formula_loop \
  --output pdf-craft-output/issue887-implement-live-01
```

The unchanged paid gate completed with exit 0: `passed=true`, `streak=3`,
temperature 0.7, top_p 1.0, model `oomol-chat`, no response cache or fallback.
Source/fixture/tool fingerprint:
`b8046982a50ef0d10aedb2280cdbafdf4f60b5bb2f099f89ffb1e34f075e1946`.

| Consecutive round | Abstract fill calls | Contribution fill calls | DeepEncoder fill calls |
| --- | ---: | ---: | ---: |
| 1 | 4 | 2 | 2 |
| 2 | 2 | 2 | 2 |
| 3 | 2 | 3 | 2 |

Each case also issued one fresh translation request. All nine outputs passed
the existing formula/geometry/asset checks, temporary-node checks and exact PCEX
encode/decode equality. `acceptance.json`, accepted XML, request/response and
protocol-validation transcripts remain in the ignored run directory. Review
must perform its own fresh gate run rather than reuse these responses.

Local verification: 1,152 lightweight tests passed, Pyright reported no errors
or warnings, and Pylint scored 10/10. The focused formula/serialization suite
passed 14 tests. `git diff --check` was clean.
