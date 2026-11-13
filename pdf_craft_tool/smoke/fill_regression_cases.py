"""Cases retired from routine fill regression by explicit maintenance decisions.

Keep the original evidence on disk. OCR degeneration belongs to upstream OCR
coverage, not repeated downstream fill stress. Do not count retired cases as
successful fill runs.
"""
from collections.abc import Iterable

RETIRED_FILL_CASES = {
    '986803f2832f4111910ba4f12a44023b': 'Malformed original EPUB; unsupported fixture retired by agreement.',
    '4fe91c0c12a8476a849d3d52dd149fa0': 'OCR neural repetition degeneration; covered upstream.',
    '932c674114704dd9bfa782d757a0eb65': 'OCR neural repetition degeneration; covered upstream.',
    'a8893c9f95c7424eae348973d84421c7': 'Historical OCR degeneration, page index 141, 805 repeated formulas; covered upstream.',
    'b30754c5f4094773a12c8390462ab7ba': 'Retired local reconstruction fixture; not a current fill regression gate.',
}


def regular_fill_cases(cases: Iterable[str]) -> tuple[str, ...]:
    return tuple(key for key in cases if key not in RETIRED_FILL_CASES)
