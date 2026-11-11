"""Whitespace-only location tolerance retains source coordinates and ambiguity."""
# pylint: disable=protected-access
from unittest import TestCase

from pdf_craft.extractor.chapter.page_repair import RefAnchor, _resolve_anchor
from pdf_craft.extractor.chapter.source_ranges import SourceBoundary, _boundary
from pdf_craft.extractor.chapter.text_locator import locate_text


class TextLocationTests(TestCase):
    def test_missing_space_in_empty_reference_has_one_boundary(self):
        text = 'Let O 为坐标原点,求距离。'
        anchor = RefAnchor(before='Let O', mark='', after='为坐标原点')
        start = text.index('为')
        self.assertEqual(_resolve_anchor(text, anchor, 'anchor', 'source'), ((start, start), None))

    def test_exact_and_tolerant_cut_spellings_use_same_original_offset(self):
        for gap in (' ', '  ', '\t', '\n', '\r\n', '\u3000', '\u00a0', '\u200b', '\u2060', '\ufeff', '\x08'):
            text = 'left' + gap + 'right'
            positions = []
            for left, right in [('left', 'right'), ('left'+gap, 'right'), ('left', gap+'right')]:
                with self.subTest(gap=repr(gap), left=left, right=right):
                    positions.append(_boundary(text, SourceBoundary(left=left, right=right)))
            self.assertEqual(positions, [len('left'+gap)] * 3)
            cut = positions[0]
            self.assertEqual(text[:cut] + text[cut:], text)
            self.assertEqual(text[:cut], 'left'+gap)

    def test_nonempty_marker_maps_original_span_not_submitted_length(self):
        text = 'word\t1\u200b7\u3000tail'
        anchor = RefAnchor(before='word ', mark='1 7', after=' tail')
        span, error = _resolve_anchor(text, anchor, 'anchor', 'source')
        self.assertIsNone(error)
        self.assertIsNotNone(span)
        assert span is not None
        self.assertEqual(text[slice(*span)], '1\u200b7')
        self.assertEqual(text[:span[0]] + text[slice(*span)] + text[span[1]:], text)

    def test_exact_marker_span_keeps_its_original_characters(self):
        text = 'a 1\t7 z'
        self.assertEqual(locate_text(text, 'a ', '1\t7', ' z'), [(2, 5)])

    def test_two_distinct_normalized_occurrences_stay_ambiguous(self):
        text = 'left right / left\tright'
        anchor = RefAnchor(before='left\u3000', mark='', after='right')
        span, error = _resolve_anchor(text, anchor, 'anchor', 'source')
        self.assertIsNone(span)
        self.assertIn('REF_ANCHOR_AMBIGUOUS', error or '')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            _boundary(text, SourceBoundary(left='left\u3000', right='right'))

    def test_whitespace_only_context_does_not_identify_a_position(self):
        self.assertEqual(locate_text(' \t', '\u200b', '', '\n'), [])
        span, error = _resolve_anchor(' \t', RefAnchor(before=' ', mark='\u200b', after=''), 'a', 's')
        self.assertIsNone(span)
        self.assertIn('REF_ANCHOR_EMPTY', error or '')
        with self.assertRaises(ValueError):
            _boundary(' \t', SourceBoundary(left=' ', right=''))

    def test_source_cut_does_not_fuzzily_ignore_nonwhite_characters(self):
        for wrong in ('LEFT', 'lef!', 'le ftX'):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                _boundary('left right', SourceBoundary(left=wrong, right='right'))
        for control in ('\u200c', '\u200d', '\ufe0f'):
            self.assertEqual(locate_text('left'+control+'right', 'left', '', 'right'), [])

    def test_leading_trailing_spaces_and_sentinels_preserve_whole_source(self):
        text = ' \tleft right \n'
        self.assertEqual(_boundary(text, 'source_start'), 0)
        self.assertEqual(_boundary(text, 'source_end'), len(text))
        self.assertEqual(_boundary(text, SourceBoundary(left='', right='left')), 0)
        self.assertEqual(_boundary(text, SourceBoundary(left='right', right='')), len(text))

    def test_exact_match_precedence_and_existing_fuzzy_reference_remain(self):
        self.assertEqual(locate_text('left right / left\tright', 'left ', '', 'right'), [(5, 5)])
        text = 'Start① end. Another start① ending.'
        span, error = _resolve_anchor(text, RefAnchor(before='Anothr start', mark='①', after=' ending.'), 'a', 's')
        self.assertIsNone(error)
        self.assertEqual(span, (text.rindex('①'), text.rindex('①')+1))
