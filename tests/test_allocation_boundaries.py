"""Compact fill responses preserve the complete immutable artist response."""
import json
from types import SimpleNamespace
from unittest import TestCase
from pdf_craft.transformer.free_translation import allocate


class AllocationBoundaryTests(TestCase):
    def test_reordered_owners_and_empty_owner_keep_every_character(self):
        raw = '  第二段。\n\n第一段。\t'
        result = allocate(json.dumps([dict(id='1', end='第二段。'), dict(id='2', text=''),
                                      dict(id='0', end='第一段。')]),
                          [SimpleNamespace(source_math=None)]*3, raw)
        self.assertEqual(result, {'0': '\n\n第一段。\t', '1': '  第二段。', '2': ''})
        self.assertEqual(result['1'] + result['2'] + result['0'], raw)

    def test_invalid_ambiguous_incomplete_boundaries_and_identity_are_rejected(self):
        for units in ([dict(id='0', end='same')], [dict(id='0', end='absent')],
                      [dict(id='0', end='first same')], [dict(id='0', end='')],
                      [dict(id='0', end='last same', text='changed')],
                      [dict(id='1', end='last same')]):
            with self.subTest(units=units), self.assertRaises(ValueError):
                allocate(json.dumps(units), [SimpleNamespace(source_math=None)], 'first same; last same')

    def test_repeated_passages_need_longer_anchor_not_deduplication(self):
        raw = 'Same.\n\nSame. End.'
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            allocate(json.dumps([dict(id='0', end='Same.'), dict(id='1', end='End.')]),
                     [SimpleNamespace(source_math=None)]*2, raw)
        result = allocate(json.dumps([dict(id='0', end='Same.\n\n'), dict(id='1', end='End.')]),
                          [SimpleNamespace(source_math=None)]*2, raw)
        self.assertEqual(result['0'] + result['1'], raw)

    def test_heading_repeated_in_body_uses_exact_following_context(self):
        raw = 'Dataset.\n\nDataset. This is the body.'
        result = allocate(json.dumps([dict(id='0', end='Dataset.', after='\n\n'),
                                      dict(id='1', end='the body.')]),
                          [SimpleNamespace(source_math=None)]*2, raw)
        self.assertEqual(result, {'0': 'Dataset.', '1': '\n\nDataset. This is the body.'})
        for after in ('invented context', 1):
            with self.subTest(after=after), self.assertRaises(ValueError):
                allocate(json.dumps([dict(id='0', end='Dataset.', after=after),
                                     dict(id='1', end='the body.')]),
                         [SimpleNamespace(source_math=None)]*2, raw)
