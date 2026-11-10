# LLM acceptance and content comparison

PDF Craft validates deliverable content and structure rather than a model's ability
to reproduce irrelevant surface formatting. Differences that the program can handle
unambiguously should not cost another model request. When an algorithm rejects a
legitimate expression, fix the algorithm instead of adding protocols or gates that
make the model accommodate the implementation.

## Translation and fill responsibilities

The translation LLM owns expression. Prompts guide translation, tone, style and
terminology, not XML/JSON, formula identities, placeholders or other output protocols.
The artist's response is not subject to content review, rejection or retranslation.

Keep the raw response for diagnostics. In addition to shared invalid-character
cleaning, an adjustable optimization may use a Markdown parser to remove recognized
formatting while retaining its text, producing a plain-text engineering view.
Unrecognized syntax survives; cleanup errors fall back to the original response.
Fill input and full-text comparison use the same view, unchanged throughout repair.

PCEX body, footnotes, independent image/table fields and native EPUB share one finite
XML fill loop. Source XML supplies stable identities, structure and resources. One
fill response handles text ownership, formatting and formula placement. The program
does not create artist formula placeholders, identity tables or range protocols, or
infer correspondence from position or similarity. There is no separate JSON
allocation, helper model or reverse retranslation stage.

## Full-text and structural acceptance

Full-text acceptance only compares normalized content.

Extract content in the final candidate's actual delivery order and apply the same
shared string comparison function to that text and the translation engineering view.
Source block identities determine writeback positions; response-tree order alone
cannot establish correct delivery order. The comparison view does not rewrite output
or change source offsets or CFIs. Character rules belong in the
[translation/fill boundary](../../references/translation-fill-boundary.md), not in
separate adapter policies. CSS preservation modes and the text/tail field owning a
space are not additional full-text acceptance gates.

Structural checks protect real delivery requirements: parseable XML/format, complete
required identities and resources, valid references and legal submission targets.
Required formatting tags and identities remain present; slots may be empty when no
translated characters belong there. Do not fill them with source wording, placeholders
or duplicate translation. Read-only context cannot hide text discarded on delivery.

PCEX/EPUB adapters own format materialization and content extraction. Materialization
happens within candidate acceptance. Formula presentation and hidden annotations must
not count as two copies, and hidden data must not mask lost visible content. Submission
must not rewrite accepted words or substitute another formula. Detached nodes cannot
become the document root. PCEX serialization preserves adopted content without applying
interactive whitespace rules to it.

Real content edits or structural damage receive concrete differences and locations in
the same finite fill loop. Failure must not adopt partial results or label source
fallback as translation. Diagnostic evidence survives until the owning results and
direct content callbacks complete. See the lifecycle in
[error handling](ERROR_HANDLING.md#llm-repair-and-adoption-failure-evidence).

## Friendly XML and storage boundaries

All model XML inputs and outputs use the shared friendly codec. It handles
representation and structure without granting additional semantic validation powers.
Literal tags, entity spellings and actual structure must remain distinguishable.
See the [LLM XML boundary](../../references/llm-xml-boundary.md).

`common/whitespace.py` provides shared character rules; `transformer/whitespace.py`
handles preservation contexts used by friendly XML and source segmentation. HTML/CSS
normal, pre and pre-line rules may still serve those transport/source representations;
they do not reconstruct fill field whitespace ownership or establish another content
comparison gate. Encoding and segmentation retain meaningful mixed-content boundaries;
independent per-node trimming must not concatenate words. Storage uses standard XML
and preserves adopted results, never the comparison view as output text.
