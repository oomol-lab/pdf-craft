"""Compact JEV evidence is equivalent text, never a source repair."""
from copy import deepcopy
import json
import unittest

from pdf_craft.extractor.chapter.review_encoding import (
    compact_review_evidence, encode_review_text,
)


def expand(value):
    if isinstance(value, str):
        return value
    result = []
    for part in value['literal_parts']:
        if isinstance(part, str):
            result.append(part)
        elif 'repeat' in part:
            result.extend([part['repeat']] * part['count'])
        else:
            seq = part['integer_sequence']
            result.extend(str(n) + seq['suffix']
                          for n in range(seq['start'], seq['start'] + seq['count']))
    return ''.join(result)


class ReviewEncodingTests(unittest.TestCase):
    def test_repetition_and_ordinals_preserve_every_character(self):
        cases = ['prefix ' + '1. ' * 3000 + ' tail', '中文。\n' * 2000,
                 '\\frac{1}{2} ' * 1000, 'start ' + ''.join(f'{n}. ' for n in range(1, 2000)),
                 ''.join(f'{n}.\n' for n in range(80, 2000)) + 'END',
                 ''.join(f'{n}. ' for n in range(1, 100)) * 40,
                 ''.join(f'{n:04}. ' for n in range(1, 300)),
                 ('different words ' * 100) + ''.join(f'{n}.' for n in range(1, 400))]
        for text in cases:
            with self.subTest(prefix=text[:40]):
                self.assertEqual(expand(encode_review_text(text)), text)

    def test_ordinary_prose_is_not_clipped(self):
        text = ''.join(chr(n) for n in range(0x4E00, 0x5800))
        self.assertEqual(encode_review_text(text), text)

    def test_large_repeated_source_fits_without_dropping_evidence(self):
        text = 'Original start.' + '1. ' * 3000 + 'Original end.'
        encoded = encode_review_text(text)
        self.assertLess(len(json.dumps(encoded)), 200)
        self.assertEqual(expand(encoded), text)

    def test_only_text_fields_are_encoded_and_source_is_unchanged(self):
        original = {'citation_id': 'a' * 2000,
                    'layouts': [{'raw_text': 'note ' * 1000, 'analysed_text': 'note',
                                 'references': [], 'ownership': 'citation'}]}
        before = deepcopy(original)
        value = compact_review_evidence(original)
        self.assertEqual(original, before)
        self.assertEqual(value['citation_id'], original['citation_id'])
        self.assertNotIn('analysed_text', value['layouts'][0])
        self.assertEqual(expand(value['layouts'][0]['raw_text']), original['layouts'][0]['raw_text'])

    def test_active_or_unresolved_ref_placement_is_retained(self):
        for references, text in [([{'citation_index': 1}], 'Body'), ([], 'Body⟦REF⟧')]:
            value = compact_review_evidence({'raw_text': 'Body①', 'analysed_text': text,
                                             'references': references})
            self.assertEqual(value['analysed_text'], text)

    def test_repeated_encoding_is_idempotent(self):
        value = compact_review_evidence({'raw_text': '1. ' * 3000})
        self.assertEqual(compact_review_evidence(value), value)
