"""The artist is unrestricted; only engineering allocation may repair."""
import json
from typing import cast
from types import SimpleNamespace
from unittest import TestCase
from xml.etree.ElementTree import Element
from pdf_craft.transformer.free_translation import allocate
from pdf_craft.transformer.source_math import SourceMathTranslation
from pdf_craft.transformer.canonical_projection import SourceMath


class FreeTranslationTests(TestCase):
    def test_plain_marker_does_not_shift_global_delimited_targets(self):
        marker, value = Element('formula', name='marker'), Element('formula', name='value')
        first = SourceMathTranslation([SourceMath(r'\(①\)', marker)])
        second = SourceMathTranslation([SourceMath(r'\(x\)', value)])
        calls = [SimpleNamespace(source_math=first), SimpleNamespace(source_math=second)]
        units = [dict(id='0', text='① Note.\n\n', formulas=[]),
                 dict(id='1', text=r'Value \(x\).', formulas=[dict(key='s1', target='t1')])]
        raw = ''.join(unit['text'] for unit in units)
        with self.assertRaises(ValueError) as missing:
            allocate(json.dumps(units), calls, raw)
        self.assertIn('Missing source contents:', str(missing.exception))
        self.assertIn('①', str(missing.exception))
        units[0]['formulas'] = [dict(key='s1', target='t0')]
        with self.assertRaises(ValueError) as invalid:
            allocate(json.dumps(units), calls, raw)
        self.assertIn("Defined global target ids=['t1']", str(invalid.exception))
        self.assertIn('allocated text excerpt=', str(invalid.exception))
        units[0]['formulas'] = [dict(key='s1', text='①', before='', after=' Note.')]
        answers = allocate(json.dumps(units), calls, raw)
        self.assertEqual(answers['0'], r'\(①\) Note.'+'\n\n')
        self.assertEqual(answers['1'], r'Value \(x\).')
        self.assertEqual(first.bindings, [[first.values['s1']]])
        self.assertIs(first.values['s1'].element, marker)
        self.assertEqual(second.bindings, [[second.values['s1']]])

    def test_missing_units_get_specific_feedback_and_may_own_empty_text(self):
        calls = [SimpleNamespace(source_math=None) for _ in range(25)]
        units = [dict(id=str(i), text='Body text.' if i == 0 else '', formulas=[])
                 for i in range(13)]
        with self.assertRaises(ValueError) as caught:
            allocate(json.dumps(units), calls, 'Body text.')
        details = json.loads(str(caught.exception).split(': ', 1)[1])
        self.assertEqual(details, dict(expected_count=25, received_count=13,
            missing_ids=[str(i) for i in range(13, 25)], duplicate_ids=[], unexpected_ids=[]))
        units.extend(dict(id=str(i), text='', formulas=[]) for i in range(13, 25))
        result = allocate(json.dumps(units), calls, 'Body text.')
        self.assertEqual(result, {str(i): 'Body text.' if i == 0 else '' for i in range(25)})

    def test_identity_feedback_reports_duplicate_and_unexpected_ids_together(self):
        units = [dict(id=key, text='', formulas=[]) for key in ('0', '0', 'rogue')]
        with self.assertRaises(ValueError) as caught:
            allocate(json.dumps(units), [SimpleNamespace(source_math=None)]*3, '')
        details = json.loads(str(caught.exception).split(': ', 1)[1])
        self.assertEqual(details['duplicate_ids'], ['0'])
        self.assertEqual(details['unexpected_ids'], ['rogue'])
        self.assertEqual(details['missing_ids'], ['1', '2'])

    def test_semantic_formula_order_is_not_source_order(self):
        zero, one = Element('formula', id='zero'), Element('formula', id='one')
        math = SourceMathTranslation([SourceMath(r'\(=0\)', zero), SourceMath(r'\(=1\)', one)])
        calls = [SimpleNamespace(source_math=math)]
        raw = 'First =1, then =0.'
        response = json.dumps([dict(id='0', text=raw, formulas=[
            dict(key='s2', text='=1', before='First ', after=', then'),
            dict(key='s1', text='=0', before='then ', after='.')])])
        self.assertEqual(allocate(response, calls, raw)['0'], r'First \(=1\), then \(=0\).')
        self.assertEqual([cast(SourceMath, group[0]).element.get('id') for group in math.bindings], ['one', 'zero'])

    def test_engineer_cannot_rewrite_artist(self):
        with self.assertRaisesRegex(ValueError, 'complete original translation'):
            allocate(json.dumps([dict(id='0', text='corrected', formulas=[])]),
                     [SimpleNamespace(source_math=None)], 'anything at all')
        result = allocate(json.dumps([dict(id='0', text='code  indent', formulas=[])]),
                          [SimpleNamespace(source_math=None, preserve_space=True)], 'code\tindent')
        self.assertEqual(result['0'], 'code\tindent')

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
        self.assertEqual([cast(SourceMath, group[0]).element.get('origin') for group in math.bindings], ['b', 'a'])

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


    def test_coverage_feedback_identifies_real_missing_text(self):
        raw = 'Intro. Formula x followed by important prose. End.'
        response = json.dumps([dict(id='0', text='Intro. Formula ', formulas=[])])
        with self.assertRaises(ValueError) as caught:
            allocate(response, [SimpleNamespace(source_math=None)], raw)
        message = str(caught.exception)
        for detail in ('Missing', 'added 0', 'unit 0', 'translation offset',
                       'important prose', 'Allocated unit lengths: id=0:'):
            self.assertIn(detail, message)

    def test_unique_formula_does_not_need_unnecessary_context(self):
        math = SourceMathTranslation([SourceMath(r'\(x=0\)', Element('formula'))])
        raw = r'If condition then \(x=0\).'
        result = allocate(json.dumps([dict(id='0', text=raw, formulas=[
            dict(key='s1', text=r'\(x=0\)', before='If', after='then')])]),
            [SimpleNamespace(source_math=math)], raw)
        self.assertEqual(result['0'], raw)
        self.assertEqual(math.bindings[0], [math.values['s1']])

    def test_repeated_formula_still_needs_real_disambiguation(self):
        math = SourceMathTranslation([SourceMath(r'\(x\)', Element('formula', origin='a')),
                                     SourceMath(r'\(x\)', Element('formula', origin='b'))])
        raw = 'B x; A x.'
        with self.assertRaisesRegex(ValueError, 'Exact candidate contexts'):
            allocate(json.dumps([dict(id='0', text=raw, formulas=[
                dict(key='s2', text='x', before='wrong', after=''),
                dict(key='s1', text='x', before='A', after='.')])]),
                [SimpleNamespace(source_math=math)], raw)
        self.assertFalse(math.allocated)

    def test_ordinary_whitespace_is_recovered_without_changing_words(self):
        result = allocate(json.dumps([dict(id='0', text='The  bold\nword.', formulas=[])]),
                          [SimpleNamespace(source_math=None)], 'The bold word.')
        self.assertEqual(result['0'], 'The bold word.')
        for raw in ('The\u200dbold word.', 'The bold words.'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                allocate(json.dumps([dict(id='0', text='The bold word.', formulas=[])]),
                         [SimpleNamespace(source_math=None)], raw)

    def test_whitespace_across_allocations_keeps_semantic_owners(self):
        result = allocate(json.dumps([dict(id='1', text='Other  paragraph.', formulas=[]),
                                      dict(id='0', text='Boldword.', formulas=[])]),
                          [SimpleNamespace(source_math=None), SimpleNamespace(source_math=None)],
                          'Other paragraph.\n\nBold word.')
        self.assertEqual(result['0'], 'Bold word.')
        self.assertEqual(result['1'].strip(), 'Other paragraph.')

    def test_formula_locator_recovers_spacing_without_rewriting_math(self):
        math = SourceMathTranslation([SourceMath(r'\(x=0\)', Element('formula'))])
        raw = r'Result \(x=0\).'
        result = allocate(json.dumps([dict(id='0', text=raw, formulas=[
            dict(key='s1', text=r'\(x = 0\)', before='', after='')])]),
            [SimpleNamespace(source_math=math)], raw)
        self.assertEqual(result['0'], raw)
        self.assertEqual(math.bindings, [[math.values['s1']]])

    def test_spacing_recovery_does_not_guess_repeated_math_identity(self):
        from pdf_craft.transformer.allocation_text import formula_occurrences
        raw = r'A \(\text{a b}\); B \(\text{ab}\).'
        candidates = formula_occurrences(raw, r'\( \text{a  b} \)')
        self.assertEqual(len(candidates), 2)
        math = SourceMathTranslation([SourceMath(r'\(x=0\)', Element('formula'))])
        with self.assertRaisesRegex(ValueError, 'Literal occurrences=0'):
            allocate(json.dumps([dict(id='0', text=r'\(x=1\)', formulas=[
                dict(key='s1', text=r'\(x = 0\)', before='', after='')])]),
                [SimpleNamespace(source_math=math)], r'\(x=1\)')

    def test_translated_occurrence_ids_follow_semantic_owner_after_reordering(self):
        calls = [SimpleNamespace(source_math=SourceMathTranslation([
            SourceMath(r'\(x\)', Element('formula', origin=name))])) for name in ('a', 'b')]
        raw = r'B \(x\). A \(x\).'
        answer = allocate(json.dumps([
            dict(id='1', text=r'B \(x\). ', formulas=[dict(key='s1', target='t1')]),
            dict(id='0', text=r'A \(x\).', formulas=[dict(key='s1', target='t2')])]), calls, raw)
        self.assertEqual(answer, {'0': r'A \(x\).', '1': r'B \(x\). '})
        self.assertEqual(calls[0].source_math.bindings[0][0].element.get('origin'), 'a')
        self.assertEqual(calls[1].source_math.bindings[0][0].element.get('origin'), 'b')
        with self.assertRaisesRegex(ValueError, 'not in unit'):
            allocate(json.dumps([
                dict(id='1', text=r'B \(x\). ', formulas=[dict(key='s1', target='t2')]),
                dict(id='0', text=r'A \(x\).', formulas=[dict(key='s1', target='t1')])]), calls, raw)

    def test_occurrence_ids_support_merged_math_and_cannot_overlap(self):
        math = SourceMathTranslation([SourceMath(r'\(x=\)', Element('formula', id='a')),
                                     SourceMath(r'\(1\)', Element('formula', id='b'))])
        calls = [SimpleNamespace(source_math=math)]
        raw = r'\(x=1\)'
        allocate(json.dumps([dict(id='0', text=raw, formulas=[
            dict(keys=['s1', 's2'], target='t1')])]), calls, raw)
        self.assertEqual([cast(SourceMath, s).element.get('id') for s in math.bindings[0]], ['a', 'b'])
        with self.assertRaisesRegex(ValueError, 'overlap'):
            allocate(json.dumps([dict(id='0', text=raw, formulas=[
                dict(key='s1', target='t1'), dict(key='s2', target='t1')])]), calls, raw)

    def test_combined_source_nodes_cannot_swallow_internal_artist_math(self):
        math = SourceMathTranslation([SourceMath(r'\(x=\)', Element('formula', id='x')),
                                     SourceMath(r'\(y=\)', Element('formula', id='y'))])
        calls = [SimpleNamespace(source_math=math)]
        raw = r'\(x=1;y=2\)'
        formula = dict(keys=['s1', 's2'], target='t1')
        with self.assertRaisesRegex(ValueError, 'explicit parts'):
            allocate(json.dumps([dict(id='0', text=raw, formulas=[formula])]), calls, raw)
        self.assertFalse(math.allocated)
        parts = [dict(keys=['s1'], text='x='), dict(text='1;'),
                 dict(keys=['s2'], text='y='), dict(text='2')]
        for bad in (parts[:-1], parts + [dict(text='2')],
                    [dict(keys=['s1'], text='x='), dict(text='1;'), dict(keys=['s1'], text='y='), dict(text='2')]):
            with self.assertRaises(ValueError):
                allocate(json.dumps([dict(id='0', text=raw, formulas=[dict(formula, parts=bad)])]), calls, raw)
            self.assertFalse(math.allocated)
        self.assertEqual(allocate(json.dumps([dict(id='0', text=raw, formulas=[dict(formula, parts=parts)])]), calls, raw),
                         {'0': raw})
        self.assertEqual(math.bindings, [[math.values['s1'], r'\(1;\)', math.values['s2'], r'\(2\)']])
        for shorthand in ({'key': 's1'}, {}):
            result = allocate(json.dumps([dict(id='0', text=raw, formulas=[
                dict(shorthand, target='t1', parts=parts)])]), calls, raw)
            self.assertEqual(result, {'0': raw})
        with self.assertRaisesRegex(ValueError, 'Outer source keys'):
            allocate(json.dumps([dict(id='0', text=raw, formulas=[
                dict(key='unknown', target='t1', parts=parts)])]), calls, raw)


    def test_feedback_collects_errors_across_units_without_partial_bindings(self):
        calls = [SimpleNamespace(source_math=SourceMathTranslation([
            SourceMath(r'\(x\)', Element('formula'))])) for _ in range(2)]
        with self.assertRaises(ValueError) as caught:
            allocate(json.dumps([
                dict(id='0', text=r'A \(x\). ', formulas=[dict(key='s1', target='unknown')]),
                dict(id='1', text=r'B \(x\).', formulas=[dict(key='s1', target='unknown')])]),
                calls, r'A \(x\). B \(x\).')
        self.assertIn('Unit 0', str(caught.exception))
        self.assertIn('Unit 1', str(caught.exception))
        self.assertTrue(all(not call.source_math.allocated for call in calls))
