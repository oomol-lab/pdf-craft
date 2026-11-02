"""JEV templates preserve opaque identifiers and isolated JSON question values."""
import unittest

from jinja2 import UndefinedError

from pdf_craft.extractor.chapter.review_questions import question
from pdf_craft.extractor.chapter.review_templates import review_template


class ReviewTemplateTests(unittest.TestCase):
    def test_zero_source_order_is_not_lost_during_string_interpolation(self):
        value = review_template('body_review', 'ownership', order=0, meaning='keep',
                                key='owner_0', _OWNERSHIP_STANDARD=[],
                                criteria={'true': 'keep', 'false': 'exclude'})
        self.assertIn('source_order=0', value['instructions'][0])
        self.assertEqual(list(value['criteria']), ['true', 'false'])

    def test_dynamic_identifier_survives_json_quoting_verbatim(self):
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
        with self.assertRaises((UndefinedError, TypeError)):
            review_template('body_review', 'publisher_apparatus')

    def test_empty_optional_role_rules_preserve_the_remaining_instructions(self):
        value = review_template('footnote_review', 'role_fact', scope='scope',
                                ask='ask', rule='rule', extra=[])
        self.assertEqual(value['instructions'][:3], ['scope', 'ask', 'rule'])
        self.assertEqual(len(value['instructions']), 4)
        scoped = review_template('footnote_review', 'bound_instructions',
                                 name='owner_0', spec={'instructions': []})
        self.assertEqual(len(scoped), 1)
        self.assertIn('state.checks.owner_0', scoped[0])
