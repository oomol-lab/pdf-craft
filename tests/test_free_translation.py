"""The artist is unrestricted; only engineering allocation may repair."""
import json
from types import SimpleNamespace
from unittest import TestCase
from xml.etree.ElementTree import Element
from pdf_craft.transformer.free_translation import allocate
from pdf_craft.transformer.source_math import SourceMathTranslation
from pdf_craft.transformer.canonical_projection import SourceMath


class FreeTranslationTests(TestCase):
    def test_semantic_formula_order_is_not_source_order(self):
        zero, one = Element('formula', id='zero'), Element('formula', id='one')
        math = SourceMathTranslation([SourceMath(r'\(=0\)', zero), SourceMath(r'\(=1\)', one)])
        calls = [SimpleNamespace(source_math=math)]
        raw = 'First =1, then =0.'
        response = json.dumps([dict(id='0', text=raw, formulas=[
            dict(key='s2', text='=1', before='First ', after=', then'),
            dict(key='s1', text='=0', before='then ', after='.')])])
        self.assertEqual(allocate(response, calls, raw)['0'], r'First \(=1\), then \(=0\).')
        self.assertEqual([group[0].element.get('id') for group in math.bindings], ['one', 'zero'])

    def test_engineer_cannot_rewrite_artist(self):
        with self.assertRaisesRegex(ValueError, 'complete original translation'):
            allocate(json.dumps([dict(id='0', text='corrected', formulas=[])]),
                     [SimpleNamespace(source_math=None)], 'anything at all')
        with self.assertRaisesRegex(ValueError, 'complete original translation'):
            allocate(json.dumps([dict(id='0', text='code  indent', formulas=[])]),
                     [SimpleNamespace(source_math=None)], 'code\tindent')

    def test_unusual_and_empty_text_are_valid_inputs_to_fill(self):
        for raw in ('', '<arbitrary>unstructured</arbitrary>', 's10 χ mod q', '  weird\n\nresponse  '):
            with self.subTest(raw=raw):
                self.assertEqual(allocate(json.dumps([dict(id='0', text=raw, formulas=[])]),
                                         [SimpleNamespace(source_math=None)], raw)['0'], raw)

    def test_same_value_different_sources_obeys_engineer_identity(self):
        first, second = Element('formula', origin='a'), Element('formula', origin='b')
        math = SourceMathTranslation([SourceMath(r'\(x\)', first), SourceMath(r'\(x\)', second)])
        raw = 'B x; A x.'
        allocate(json.dumps([dict(id='0', text=raw, formulas=[
            dict(key='s2', text='x', before='B', after='; A'),
            dict(key='s1', text='x', before='A', after='.')])]), [SimpleNamespace(source_math=math)], raw)
        self.assertEqual([group[0].element.get('origin') for group in math.bindings], ['b', 'a'])

    def test_semantic_owner_is_independent_of_translated_unit_order(self):
        answer = allocate(json.dumps([dict(id='1', text='Second.', formulas=[]),
                                      dict(id='0', text='First.', formulas=[])]),
                          [SimpleNamespace(source_math=None), SimpleNamespace(source_math=None)],
                          'Second.\n\nFirst.')
        self.assertEqual(answer, {'0': 'First.', '1': 'Second.'})

    def test_malformed_engineering_data_is_retryable(self):
        for formulas in ([dict(keys=[], text='x', before='', after='')], [None], 'not an array'):
            with self.subTest(formulas=formulas), self.assertRaises(ValueError):
                allocate(json.dumps([dict(id='0', text='x', formulas=formulas)]),
                         [SimpleNamespace(source_math=None)], 'x')
