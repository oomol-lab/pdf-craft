"""Character patches, conditional uncertainty and unchanged content acceptance."""
import random
from unittest import TestCase

from pdf_craft.transformer.fill_text import comparison_text, text_difference
from pdf_craft_tool.smoke.fill_regression_cases import regular_fill_cases
from tests.fill_diff_helpers import apply_displayed_diff


class FillDiffFeedbackTests(TestCase):
    def test_patch_reconstructs_target_with_unicode_and_multiple_nearby_changes(self):
        samples = [('甲丙乙', '甲乙丙'), ('A < B & C', 'C & A < B'),
                   ('🙂aβc', '🙂xβz'), ('', '新增'), ('删除', '')]
        generator = random.Random(43)
        for _ in range(80):
            actual = ''.join(generator.choices('ab甲🙂.', k=30))
            expected = actual[:5]+'乙'+actual[9:18]+'xy'+actual[23:]
            samples.append((actual, expected))
        for actual, expected in samples:
            with self.subTest(actual=actual):
                message = text_difference(expected, actual)
                assert message is not None
                self.assertEqual(apply_displayed_diff(message, actual), comparison_text(expected))

    def test_count_hint_for_missing_and_extra_repeated_occurrences(self):
        for expected, actual, pair in [('Echo.'*3, 'Echo.'*2, (3, 2)),
                                        ('Echo.'*2, 'Echo.'*3, (2, 3)), ('.'*16, '.'*15, (16, 15))]:
            with self.subTest(pair=pair):
                message = text_difference(expected, actual)
                assert message is not None
                self.assertIn(f'{pair[0]} times in the translation and {pair[1]} times in the candidate', message)
                self.assertNotIn('needs another copy', message)
                self.assertNotIn('moving an existing copy does not', message)
                self.assertEqual(apply_displayed_diff(message, actual), comparison_text(expected))

    def test_overlapping_matches_do_not_claim_a_definite_count(self):
        message = text_difference('aaaaa', 'aaa')
        assert message is not None
        self.assertNotIn(' times in ', message)
        self.assertIn('presence alone may not establish completeness', message)

    def test_relocation_hint_does_not_add_a_count_instruction_or_field_destination(self):
        actual = 'Intro.Sidebar.First.Second.End.'
        expected = 'Intro.First.Second.Sidebar.End.'
        message = text_difference(expected, actual)
        assert message is not None
        self.assertIn('may reflect relocation', message)
        self.assertNotIn('Repeated occurrences', message)
        self.assertNotIn(' times in ', message)
        self.assertNotIn('Move ', message)
        self.assertEqual(apply_displayed_diff(message, actual), comparison_text(expected))

    def test_split_relocation_is_not_a_swap_protocol(self):
        actual = 'start.sidebar-one.body-one.sidebar-two.body-two.end.'
        expected = 'start.body-one.body-two.sidebar-one.sidebar-two.end.'
        message = text_difference(expected, actual)
        assert message is not None
        self.assertIn('may reflect relocation', message)
        self.assertNotIn('swap', message)
        self.assertEqual(apply_displayed_diff(message, actual), comparison_text(expected))

    def test_ordinary_replacement_does_not_receive_repetition_or_relocation_hints(self):
        message = text_difference('correct', 'wrong')
        assert message is not None
        self.assertNotIn('may reflect relocation', message)
        self.assertNotIn('occurrence', message)

    def test_mixed_counts_do_not_select_an_arbitrary_numeric_pair(self):
        message = text_difference('AAAA|'+'BBBBB', 'AAA|'+'BBBB')
        assert message is not None
        self.assertNotIn(' times in ', message)
        self.assertIn('Exact passages have different occurrence counts', message)

    def test_diff_fence_cannot_be_closed_by_literal_document_text(self):
        actual, expected = 'before```after', 'before```other'
        message = text_difference(expected, actual)
        assert message is not None
        self.assertIn('````diff', message)
        self.assertEqual(apply_displayed_diff(message, actual), comparison_text(expected))

    def test_no_new_normalization_or_additional_gate_is_introduced(self):
        self.assertIsNone(text_difference('é A\u200b B', 'e\u0301AB'))
        self.assertIsNone(text_difference(r'$20$', r'\( 20 \)'))
        for expected, actual in [('20', '40'), ('ё', 'е'), ('..', '.')]:
            self.assertIsNotNone(text_difference(expected, actual))

    def test_retired_upstream_cases_do_not_remove_unrelated_or_new_cases(self):
        retired = ['a8893c9f95c7424eae348973d84421c7', 'b30754c5f4094773a12c8390462ab7ba',
                   '4fe91c0c12a8476a849d3d52dd149fa0', '932c674114704dd9bfa782d757a0eb65']
        current = ['f834ac2c1ad948a8ae1f6b8bb446dac6', 'f019cda4bf1c47bbbab8158ed0141c24',
                   'b0ef33f27820436e93a44ca490f255ee', 'future-case']
        self.assertEqual(regular_fill_cases([*retired, *current]), tuple(current))

    def test_retired_only_run_cannot_report_an_empty_green_regression(self):
        import asyncio
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from pdf_craft_tool.smoke.fill_incident_regression import run
        with TemporaryDirectory() as directory:
            output = Path(directory)/'output'
            with self.assertRaisesRegex(ValueError, 'No active fill regression cases'):
                asyncio.run(run(Path(directory), output, 'fill', ['a8893c9f95c7424eae348973d84421c7']))
            self.assertFalse(output.exists())
