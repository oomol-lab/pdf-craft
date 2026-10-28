# LLM acceptance and whitespace

PDF Craft accepts recoverable results, not a model's ability to reproduce
unnecessary surface formatting. **When the intended result is unambiguous and
an algorithm can restore it, restore and validate it without another model
request.** Request correction when information is missing or multiple meaningful
allocations remain possible. This principle applies to LLM operations generally;
it does not authorize guessing wording or weakening final-result correctness.

## XML fill

The first-stage translated paragraph is the trusted text. Original XML supplies
source identities, structure, styling and whitespace context, never replacement
untranslated wording. After structural validation, unchanged non-whitespace
characters are aligned with that translated paragraph. Uniquely owned
whitespace is reconstructed before the candidate is adopted. A trailing cosmetic
space or a missing word separator inside one text slot need not cost another
fill call. A missing separator between differently styled slots needs an existing
or unique source owner; otherwise the model must clarify its allocation. Source
formula restoration retains its explicit outside-expression padding rule.

Changes are transactional: unsuccessful recovery leaves the candidate untouched.
Words, punctuation, NBSP, other Unicode separators, source IDs and required tags
remain checked. Missing/changed prose or ambiguous slot ownership still enters
the bounded repair loop. Punctuation/spelling correction is not part of this
whitespace refactor.

## Shared HTML/CSS transport subset

The shared policy in `transformer/whitespace.py` is used by friendly XML,
segmentation and canonical recovery. Block/inline classification comes from the
existing `xml/inline.py` registry and its explicit `display` override.

| Context | Processing |
| --- | --- |
| `normal`, `nowrap` | Collapse ordinary spaces/tabs and segment breaks; normal paragraph edges can be trimmed. |
| `pre-line` | Collapse spaces/tabs, retain segment breaks. |
| `pre`, `pre-wrap`, `break-spaces` | Preserve spaces, tabs and breaks. |
| NBSP and other Unicode separators | Preserve; they are not ordinary collapsible whitespace. |
| Block-only containers in collapsing modes | May add inter-block indentation. |
| Text mixed with inline elements | Never introduce separators that were absent; normalize existing runs continuously across slots. |

Only U+0020, U+0009 and CR/LF document whitespace participates. CRLF/CR is
normalized to LF. CSS Text 3 leaves normal segment-break transformation to the
user agent; this transport chooses a space consistently, without language-based
CJK removal. This is not browser layout, bidi positioning, line wrapping or a
complete CSS cascade. It does not alter the source DOM to claim DOM identity.

Modes inherit through the tree. HTML `pre`/`textarea` defaults, explicit
`white-space` transport hints and inline `style` declarations are supported.
An explicit `xml:space="preserve"` is treated as a preservation hint at that
node; descendants inherit it and may override with an explicit supported mode.
Unknown explicit modes are conservatively preserved. External stylesheets and
selectors are not evaluated. Source context, not model-supplied styling, governs
acceptance. Temporary context hints in fill templates are replaced by original
source attributes on adoption.

Friendly XML normalizes existing ordinary whitespace and indents block-only
containers. It never treats a block's mixed text as disposable indentation.
The protocol wrapper remains readable while inline sequences remain semantic.
Preserved contexts keep literal whitespace; explicit `br` and other structural
nodes are retained, not converted to disposable whitespace.

The normative reference is [CSS Text 3: white-space](https://www.w3.org/TR/css-text-3/#white-space-property)
and [whitespace processing](https://www.w3.org/TR/css-text-3/#white-space-processing).
HTML defaults are checked against the [HTML rendering standard](https://html.spec.whatwg.org/multipage/rendering.html).
Our stated transport subset does not promise complete CSS/browser equivalence
when external styling or unsupported layout behavior is involved.

Source contexts use the same ID/tag/occurrence matching as final attribute adoption,
including different-tag reordering without IDs. An unmatched source context is
rejected. A protected node does not protect its normal parent-owned tail; gaps
are classified by actual field ownership. Known normal padding cannot migrate
into a preserved slot merely to satisfy comparison.

A missing edge gap is not automatically normal paragraph padding. Its possible
source text/tail owners determine the preservation context, even if neither the
source nor the candidate contains edge whitespace. In a single protected field,
the known canonical leading/trailing whitespace can be restored exactly. At a
mixed boundary with multiple possible owners, correction is required rather than
silently discarding it or guessing which style owns it. This is recovery from
known translated text, not permission to collapse protected CSS whitespace.

Gap ownership is decided once, before normalization and reconstruction. A unique
source owner takes precedence over the model's placement: whitespace moved from
`pre` to a normal parent (or the reverse) is restored to that owner, even when
concatenated characters already match. Inside one text field, canonical spaces,
tabs and breaks are restored using that field's mode without another fill call.
This includes protected whitespace: it is restored exactly, not normalized away.
Multiple protected owners without a unique allocation still require clarification.
