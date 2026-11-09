# Service #364: offline translation allocation regression

This regression makes no model, OCR or paid smoke calls. The HTTP model boundary
is replaced; grouping, bounded repair, allocation, XML fill, identity checks and
format projection execute normally.

Run from the checked-out implementation branch with its Python dependencies:

```sh
python -m pytest -q tests/test_elastic_formula_restore.py tests/test_allocation_boundaries.py tests/test_online_formula_allocation.py tests/test_free_translation.py
PDF_CRAFT_SERVICE_364_FIXTURES=/Users/taozeyu/Downloads/pdfcraft-review-fixtures/service-364-allocation-20261109 python -m pytest -q tests/test_service_364_replay.py
```

The second command requires the retained private ZIP files. Without that explicit
environment variable those private tests are skipped; a skipped run is not proof
of the original-case gate. The directory and failure log IDs are recorded in the
test. No original book text or model logs are committed to the repository.

The medical regression reuses the recorded 20-unit source group and unchanged
Chinese translation. Its first saved response is missing formula IDs; its saved
11th response supplies correspondence but includes changed numeric contents and
omits redundant unique-span contexts. It must finish within the existing budget,
with one artist request and two engineering requests. The final amount is formula
`40` plus ordinary prose `亿`, not `4亿`; all four formulas in that paragraph survive.
A separate local replay also exercises the original unit 8 through model XML fill.

The four EPUB groups contain 61, 63, 60 and 62 units. Each saved response is malformed
JSON and remains rejected. The provider substitute first returns that response,
then a compact boundary allocation. The reference boundaries use complete objects
from the saved prefix and manually checked final source/translation paragraphs;
this reference preparation is not a production JSON repair algorithm. Each group
must use one artist request and two engineering requests, preserve every owner and
all translated text under the existing document-whitespace rules, and successfully
complete actual XML fill/adoption. End anchors are never used to infer source IDs.

Recorded response character sizes versus compact reference responses (including
following-word context, so whitespace normalization cannot confuse repeated headings):

| Units | Saved incomplete response | Compact complete response |
| --- | ---: | ---: |
| 61 | 20,312 | 7,205 |
| 63 | 20,016 | 7,404 |
| 60 | 20,044 | 7,075 |
| 62 | 20,299 | 7,262 |

This proves offline recovery behavior and reduces the required output size. The
archives do not record provider finish reasons, so these tests do not establish
that a particular provider token limit caused the original truncation, nor do
they measure new live-model reliability.

The normal tests additionally cover unchanged percentage notation restoration,
changed numeric values, failed LaTeX conversion, exact residual boundaries,
formula reordering, missing/duplicate/unknown source IDs, ambiguous locators,
ambiguous or incomplete allocation boundaries, and repeated headings/passages.
