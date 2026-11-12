"""Final model operations survive fallible OCR wrappers, end to end."""
from copy import deepcopy
from dataclasses import replace
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase
from xml.sax.saxutils import escape
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft.markdown.paragraph import HTMLTag
from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.analysis_content import layout_reference_text, replace_layout_references
from pdf_craft.extractor.chapter.analysis_types import (
    ReferenceSpan, _AnalysedHTMLTag, _AnalysedInlineExpression, _AnalysedTextSource,
)
from pdf_craft.extractor.chapter.assembly import PageAssembly
from pdf_craft.extractor.chapter.chapter import Chapter, InlineExpression, Reference, SourceTextFragment, TextFlowItem, decode, encode
from pdf_craft.extractor.chapter.content_ranges import content_text, slice_content
from pdf_craft.extractor.chapter.page_analysis import restore_streams
from pdf_craft.extractor.chapter.page_repair import build_page_repair_messages, _compile_repaired_page
from pdf_craft.extractor.chapter.repair_plan import RepairPlan, materialize_plan
from pdf_craft.extractor.chapter.text_operations import apply_text_removals
from tests.test_source_range_repair import page, box


def wire_candidate(source, mark, anchor):
    body, note = source.layouts
    plan = RepairPlan.model_validate(dict(
        layouts=[dict(layout_id=body.source_id or f'layout-{body.document_order}', destination=dict(kind='body')),
                 dict(layout_id=note.source_id or f'layout-{note.document_order}', destination=dict(kind='note', note_id='n'))],
        notes=[dict(operation='define', note_id='n', mark=mark)],
        references=[dict(layout_id=body.source_id or f'layout-{body.document_order}', note_id='n', anchor=anchor)]))
    return _compile_repaired_page(source, materialize_plan(plan, source.page_index))


class RefinementContentRangesTests(TestCase):
    def test_real_ocr_symbol_and_reference_are_not_one_atomic_decision(self):
        for expr, mark, before in [('>4', '4', '>'), (r'\odot 1', '1', r'\odot '),
                                   (r'\times 2', '2', r'\times ')]:
            with self.subTest(expr=expr):
                source = page(box(escape('Before \\(' + expr + '\\) after.')),
                              box(mark + ' A printed note.'))
                original = deepcopy(source)
                candidate = wire_candidate(source, mark, dict(before='Before ' + before, mark=mark, after=' after.'))
                restored = restore_streams([candidate])
                flow = restored.paragraphs[0]
                assert isinstance(flow, TextFlowItem)
                fragment = flow.children[0]
                assert isinstance(fragment, SourceTextFragment)
                assert isinstance(fragment.content[1], InlineExpression)
                self.assertEqual(fragment.content[1].content, before)
                assert isinstance(fragment.content[2], Reference)
                self.assertEqual(fragment.content[2].id, restored.citations[0].id)
                self.assertEqual(source, original)
                chapter = Chapter(None, -1, [x for x in restored.paragraphs if isinstance(x, TextFlowItem)], restored.citations)
                self.assertEqual(decode(fromstring(tostring(encode(chapter)))), chapter)

    def test_manuscript_sheet_note_and_leading_callout_survive_assembly(self):
        # A referenced editorial annotation is content, even if the provisional
        # classifier calls its subject publication metadata. The model owns it.
        source = page(box('1Main paragraph.'), box('1 Sheet forty-nine of manuscript A.'))
        candidate = wire_candidate(source, '1', dict(before='', mark='1', after='Main paragraph.'))
        streams = restore_streams([candidate])
        chapter = Chapter(None, -1, [x for x in streams.paragraphs if isinstance(x, TextFlowItem)], streams.citations)
        root = encode(chapter)
        definition = root.find('./references/ref')
        assert definition is not None
        self.assertEqual(definition.findtext('mark'), '1')
        note_flow = definition.find('flow')
        assert note_flow is not None
        self.assertEqual(''.join(note_flow.itertext()).strip(), 'Sheet forty-nine of manuscript A.')
        callouts = root.findall('./flow//ref')
        self.assertEqual([n.attrib['id'] for n in callouts], [definition.attrib['id']])
        self.assertEqual(decode(fromstring(tostring(root))), chapter)

    def test_reference_crosses_format_nodes_without_consuming_surrounding_letters(self):
        source = page(box(escape('<b>prefix1</b><i>2suffix</i>')), box('12 Note.'))
        candidate = wire_candidate(source, '12', dict(before='prefix', mark='12', after='suffix'))
        self.assertEqual(layout_reference_text(candidate.layouts[0]), 'prefix12suffix')
        flow = restore_streams([candidate]).paragraphs[0]
        assert isinstance(flow, TextFlowItem)
        fragment = flow.children[0]
        assert isinstance(fragment, SourceTextFragment)
        left, ref, right = fragment.content
        assert isinstance(left, HTMLTag) and isinstance(right, HTMLTag)
        self.assertEqual(left.children, ['prefix'])
        self.assertEqual(right.children, ['suffix'])
        assert isinstance(ref, Reference)
        self.assertEqual(ref.mark, '12')

    def test_multiple_references_inside_one_ocr_formula_preserve_intervening_text(self):
        source = page(box(r'\(x1+y2\)'))
        layout = source.layouts[0]
        marked = replace_layout_references(layout, [ReferenceSpan(1, 2, 1, 1, '1'), ReferenceSpan(4, 5, 1, 2, '2')])
        self.assertEqual(layout_reference_text(marked), 'x1+y2')
        self.assertEqual([p.content for p in marked.source.content if isinstance(p, _AnalysedInlineExpression)], ['x', '+y'])
        self.assertEqual(len(marked.references), 2)

    def test_slicing_formulas_and_nested_formatting_conserves_exact_characters(self):
        source = (_AnalysedHTMLTag('b', (), ('one ', _AnalysedInlineExpression(ExpressionKind.INLINE_PAREN, 'x+y'), ' two')),)
        text = content_text(source)
        pieces = [slice_content(source, a, b) for a, b in [(0, 5), (5, 7), (7, len(text))]]
        self.assertEqual(''.join(content_text(p) for p in pieces), text)
        self.assertEqual(content_text(pieces[1]), '+y')
        assert isinstance(pieces[1][0], _AnalysedHTMLTag)
        self.assertEqual(pieces[1][0].name, 'b')

    def test_reference_at_end_preserves_empty_structure_once(self):
        source = page(box('x1'))
        layout = source.layouts[0]
        br = _AnalysedHTMLTag('br', (), ())
        layout = replace(layout, source=replace(layout.source, content=('x1', br)))
        result = replace_layout_references(layout, [ReferenceSpan(1, 2, 1, 1, '1')])
        self.assertEqual(result.source.content[-1], br)
        self.assertEqual(result.source.content.count(br), 1)

    def test_nested_empty_structure_is_owned_by_exactly_one_partition(self):
        br = _AnalysedHTMLTag('br', (), ())
        source = (_AnalysedHTMLTag('b', (), ('A', br)), 'B')
        left, right = slice_content(source, 0, 1), slice_content(source, 1, 2)
        self.assertEqual(left, (_AnalysedHTMLTag('b', (), ('A',)),))
        self.assertEqual(right, (_AnalysedHTMLTag('b', (), (br,)), 'B'))

    def test_literal_removal_crosses_nodes_and_formula_wrapper_without_rewriting(self):
        expression = _AnalysedInlineExpression(ExpressionKind.INLINE_PAREN, ' -na')
        source = ('inter-', _AnalysedHTMLTag('i', (), (' ',)), expression, 'tional')
        edited = apply_text_removals(source, ((5, 9),))
        self.assertEqual(content_text(edited), 'international')
        self.assertEqual(edited[1].name, 'i')
        self.assertEqual(edited[2], replace(expression, content='na'))
        with self.assertRaisesRegex(ValueError, 'only literal whitespace or hyphens'):
            apply_text_removals(source, ((9, 10),))
        with self.assertRaises(ValueError):
            apply_text_removals(source, ((5, 7), (6, 9)))

    def test_ocr_display_formula_can_become_joined_prose_with_same_source_geometry(self):
        raw = box('Before.') + '<layout ref="equation" det="10,160,190,190">literal continuation</layout>' + box('After.')
        source = page(raw)
        formula = next(x for x in source.layouts if getattr(x.source, 'ref', None) == 'formula')
        original = deepcopy(source)
        messages = build_page_repair_messages(previous_page=None, target_page=source, next_page=None,
                                              previous_pass_probability=None, next_pass_probability=None)
        self.assertIn('literal continuation', messages[1].message)
        plans = []
        for i, item in enumerate(source.layouts):
            plans.append(dict(layout_id=item.source_id or f'layout-{item.document_order}',
                              as_text=item is formula, destination=dict(kind='body', join=i > 0)))
        candidate = _compile_repaired_page(source, materialize_plan(RepairPlan.model_validate(dict(layouts=plans)), 1))
        converted = next(x for x in candidate.layouts if x.bbox == formula.bbox)
        self.assertIsNotNone(converted.source_order)
        self.assertEqual(layout_reference_text(converted), 'literal continuation')
        self.assertTrue(converted.continues_from_previous)
        self.assertEqual(source, original)
        with TemporaryDirectory() as directory:
            bodies = []
            assembly = PageAssembly(Path(directory)/'result.sqlite', on_body=bodies.append)
            assembly.append(candidate)
            assembly.finish()
        self.assertEqual(len(bodies), 1)
        self.assertEqual(len(bodies[0].findall('./fragment')), 3)
        chapter = decode(fromstring(tostring(encode(Chapter(None, -1, [x for x in restore_streams([candidate]).paragraphs if isinstance(x, TextFlowItem)], [])))))
        self.assertEqual(len(chapter.flow_items), 1)

    def test_ocr_display_formula_text_can_be_partitioned_and_referenced(self):
        source = page('<layout ref="equation" det="10,10,190,150">Body1. 1 Note.</layout>')
        item = source.layouts[0]
        ident = item.source_id or f'layout-{item.document_order}'
        cut = dict(left='Body1. ', right='1 Note.')
        plan = RepairPlan.model_validate(dict(layouts=[
            dict(layout_id=ident, as_text=True, source_range=dict(start='source_start', end=cut), destination=dict(kind='body')),
            dict(layout_id=ident, source_range=dict(start=cut, end='source_end'), destination=dict(kind='note', note_id='n'))],
            notes=[dict(operation='define', note_id='n', mark='1')],
            references=[dict(layout_id=ident, note_id='n', anchor=dict(before='Body', mark='1', after='.'))]))
        candidate = _compile_repaired_page(source, materialize_plan(plan, 1))
        restored = restore_streams([candidate])
        self.assertEqual(len(restored.citations), 1)
        self.assertTrue(all(x.bbox == item.bbox for x in candidate.layouts))
        sources = [x.source for x in candidate.layouts]
        assert all(isinstance(x, _AnalysedTextSource) for x in sources)
        self.assertEqual([x.source_start for x in sources if isinstance(x, _AnalysedTextSource)], [0, 7])
        flow = restored.paragraphs[0]
        assert isinstance(flow, TextFlowItem)
        fragment = flow.children[0]
        assert isinstance(fragment, SourceTextFragment)
        self.assertIsInstance(fragment.content[1], Reference)
