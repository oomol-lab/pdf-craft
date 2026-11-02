# JEV review prompts

Start with one of these four entry templates. Each contains the complete wording for
its mode and review round. Read the `if` / `elif` branches to see the question kinds
selected for the current source; the static prose stays inline.

| Footnote extraction | Review round | Entry |
| --- | --- | --- |
| Disabled | Base decisions | [body_base.jinja](body_base.jinja) |
| Disabled | Supplementary facts | [body_followup.jinja](body_followup.jinja) |
| Enabled | Base decisions | [footnote_base.jinja](footnote_base.jinja) |
| Enabled | Supplementary facts | [footnote_followup.jinja](footnote_followup.jinja) |

Four entries do **not** mean four HTTP requests. A round can produce multiple batches
of named questions, with at most four questions per request. Supplementary facts are
selected from the current page's evidence and base scores.

## Shared content

Only content used across entries is extracted:

- [shared_base.jinja](shared_base.jinja): word and syntax checks for proposed breaks,
  shared by the two base entries.
- [shared_followup.jinja](shared_followup.jinja): facts shared by the two supplementary
  entries. Small mode-specific qualifications stay beside the rule they qualify.
- [evidence.jinja](evidence.jinja): coordinate and lossless-text descriptions used in
  the evidence fields. This is not another review entry.

Do not split a question into another file just because it is long. Keep entry-specific
wording inline; use ordinary conditions for genuine selection. Avoid macros, generated
JSON and Python-side concatenation of prompt instructions.

## Rendering contract

Python selects the entry, `kind`, source identifiers and applicable conditions, prepares
raw evidence, batches requests and aggregates scores. It does not append prompt prose.
The entry is rendered for each selected question. `# Instructions`, `# True` and `# False`
separate the provider fields; the headings and Jinja comments are not sent. Blank lines
separate individual instructions. Do not silently change paragraph boundaries during a
layout refactor: they affect the actual provider payload.

Prompt instructions are English; raw document evidence keeps its original language.
Treat wording changes separately from file organization. Verify an organization-only
change by generating both modes and both rounds from fixed evidence and comparing full
request payloads, field order, instruction strings and score-driven follow-up selection.
This comparison can replay saved scores and requires no provider calls.
