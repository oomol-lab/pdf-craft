"""Prose JEV templates preserve identifiers, question sections and isolation."""
import unittest

from jinja2 import UndefinedError

from pdf_craft.extractor.chapter.review_questions import question
from pdf_craft.extractor.chapter.review_templates import review_question


class ReviewTemplateTests(unittest.TestCase):
    def test_zero_source_order_is_not_lost_during_interpolation(self):
        value = review_question('body_base', kind='ownership', order=0, key='owner_0', retained=True)
        self.assertIn('source_order=0', value['instructions'][0])
        self.assertEqual(list(value['criteria']), ['true', 'false'])

    def test_dynamic_identifier_survives_verbatim(self):
        name = 'gap_"quoted"\\path\n中文<&>'
        value = question('gap_word', name)
        self.assertIn('checks.' + name + '.', value['instructions'][0])
        self.assertEqual(set(value['criteria']), {'true', 'false'})

    def test_rendered_question_containers_are_not_shared(self):
        original = question('gap_word', 'gap_0')
        original['instructions'].clear()
        original['criteria']['true'] = 'changed'
        fresh = question('gap_word', 'gap_0')
        self.assertTrue(fresh['instructions'])
        self.assertNotEqual(fresh['criteria']['true'], 'changed')

    def test_missing_context_does_not_silently_remove_the_question_identity(self):
        with self.assertRaises(UndefinedError):
            review_question('body_followup', kind='publisher_apparatus')

    def test_retained_and_excluded_ownership_share_rules_but_keep_distinct_verdicts(self):
        kept = review_question('body_base', kind='ownership', order=0, key='owner_0', retained=True)
        removed = review_question('body_base', kind='ownership', order=0, key='owner_0', retained=False)
        self.assertEqual(kept['instructions'][1:], removed['instructions'][1:])
        self.assertNotEqual(kept['criteria'], removed['criteria'])

    def test_each_mode_and_round_has_a_root_entry(self):
        cases = (
            ('body_base', 'ownership', dict(order=0, key='owner_0', retained=True)),
            ('body_followup', 'publisher_apparatus', dict(key='publisher_apparatus_0')),
            ('footnote_base', 'reference', dict(name='reference_0')),
            ('footnote_followup', 'dependency_completion', dict(name='dependency_completion_0')),
        )
        for entry, kind, context in cases:
            with self.subTest(entry=entry):
                value = review_question(entry, kind=kind, **context)
                self.assertEqual(value['type'], 'noul')
                self.assertTrue(value['instructions'])
                self.assertEqual(list(value['criteria']), ['true', 'false'])

    def test_reference_entry_includes_scope_and_location_without_python_append(self):
        value = review_question('footnote_base', kind='reference', name='reference_0')
        self.assertTrue(value['instructions'][0].startswith(
            'The sole audit target is state.checks.reference_0.'))
        self.assertTrue(value['instructions'][-1].startswith('literal_edits lists literal changes'))
        self.assertNotIn('# Instructions', value['instructions'][0])

    def test_shared_question_keeps_mode_specific_scope_in_its_template(self):
        body = question('left_frame_complete', 'frame_0', entry='body_followup')
        footnote = question('left_frame_complete', 'frame_0', entry='footnote_followup')
        self.assertEqual(body['instructions'][:-1], footnote['instructions'])
        self.assertTrue(body['instructions'][-1].startswith('Judge whether the LEFT'))
        self.assertEqual(body['criteria'], footnote['criteria'])

    def test_conditional_quotation_scope_does_not_leak_to_other_questions(self):
        normal = question('quotation_boundary', 'quote_0', entry='footnote_followup')
        recitation = question('quotation_boundary', 'quote_0', entry='footnote_followup',
                              recitation=True)
        self.assertEqual(recitation['instructions'][:-1], normal['instructions'])
        self.assertTrue(recitation['instructions'][-1].startswith('An independently presented'))
        fresh = question('quotation_boundary', 'quote_0', entry='footnote_followup')
        self.assertEqual(normal, fresh)

    def test_helpers_are_not_additional_review_entries(self):
        for path in ('shared_base', 'shared_followup', 'evidence'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                review_question(path, kind='gap_word', name='gap_0')

    def test_unknown_question_does_not_silently_render_an_empty_prompt(self):
        with self.assertRaises(ValueError):
            review_question('body_base', kind='unknown')
