"""Prose JEV templates preserve identifiers, question sections and isolation."""
import unittest

from jinja2 import UndefinedError

from pdf_craft.extractor.chapter.review_questions import question
from pdf_craft.extractor.chapter.review_templates import review_question, review_text


class ReviewTemplateTests(unittest.TestCase):
    def test_zero_source_order_is_not_lost_during_interpolation(self):
        value = review_question('body/ownership', order=0, key='owner_0', retained=True)
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
            review_question('body/publisher_apparatus')

    def test_retained_and_excluded_ownership_share_rules_but_keep_distinct_verdicts(self):
        kept = review_question('body/ownership', order=0, key='owner_0', retained=True)
        removed = review_question('body/ownership', order=0, key='owner_0', retained=False)
        self.assertEqual(kept['instructions'][1:], removed['instructions'][1:])
        self.assertNotEqual(kept['criteria'], removed['criteria'])

    def test_a_prose_fragment_has_no_question_or_json_wrapper(self):
        text = review_text('footnote/check_scope', name='owner_0')
        self.assertTrue(text.startswith('The sole audit target is state.checks.owner_0.'))
        self.assertNotIn('# Instructions', text)
