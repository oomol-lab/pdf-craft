"""Relocation identity, source-tree bounds and actual adoption contracts."""
from copy import deepcopy
from unittest import TestCase, IsolatedAsyncioTestCase
from xml.etree.ElementTree import Element, fromstring, tostring

from pdf_craft._policy import policy_operation
from pdf_craft.extractor.chapter.chapter import Chapter, TextFlowItem, SourceTextFragment, encode
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.fill_text import comparison_text
from pdf_craft.transformer.xml_translator.segment import (
    BlockSegment, ImmutableBlockElement, search_inline_segments, search_text_segments,
)
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from pdf_craft.transformer.xml_translator.xml_translator.submitter import SubmitKind
from tests.translation_model import translator_fixture
from tests.fill_fixtures import model_replies
from tests.fill_fixtures import expression


def contract(xml, anchors=None):
    root = fromstring(xml)
    segments = list(search_inline_segments(search_text_segments(root, normalize_whitespace=False)))
    block = BlockSegment('xml', segments, anchors)
    return root, segments, block, block.create_element()


class SiblingInlineTests(TestCase):
    def test_identical_anonymous_inline_nodes_get_distinct_request_ids(self):
        _, _, block, candidate = contract('<body><p><span>x</span><span>y</span></p><p>z</p></body>')
        ids = [node.get('id') for node in candidate.iter('span')]
        self.assertTrue(all(ids))
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(candidate[0].get('inline-scope'), candidate[1].get('inline-scope'))
        self.assertEqual(list(block.validate(candidate)), [])

    def test_move_and_swap_preserve_source_attributes_and_candidate_parent(self):
        source, _, block, candidate = contract(
            '<body><p class="first">A<span id="original-a" class="a">one<em title="nested">!</em></span>.</p>'
            '<p class="second">B<span id="original-b" class="b">two</span>.</p></body>')
        before = tostring(source)
        first, second = candidate
        left, right = first[0], second[0]
        first.remove(left)
        second.remove(right)
        first.append(right)
        second.append(left)
        raw = tostring(candidate)
        self.assertEqual(list(block.validate(candidate)), [])
        adopted = [item.submitted_element for item in block.submit(candidate)]
        self.assertEqual(adopted[0].get('class'), 'first')
        self.assertEqual(adopted[1].get('class'), 'second')
        self.assertEqual(adopted[0][0].attrib, {'id': 'original-b', 'class': 'b'})
        self.assertEqual(adopted[1][0].attrib, {'id': 'original-a', 'class': 'a'})
        self.assertEqual(adopted[1][0][0].get('title'), 'nested')
        self.assertEqual([''.join(n.itertext()) for n in adopted], ['Atwo.', 'Bone!.'])
        self.assertEqual(tostring(candidate), raw)
        self.assertEqual(tostring(source), before)

    def test_missing_duplicate_unidentified_or_retagged_inline_is_rejected(self):
        for change in ('missing', 'duplicate', 'unidentified', 'retagged'):
            with self.subTest(change=change):
                _, _, block, candidate = contract('<body><p><span>x</span></p><p>y</p></body>')
                node = candidate[0][0]
                if change == 'missing':
                    candidate[0].remove(node)
                elif change == 'duplicate':
                    candidate[1].append(deepcopy(node))
                elif change == 'unidentified':
                    node.attrib.pop('id')
                else:
                    node.tag = 'em'
                self.assertTrue(list(block.validate(candidate)))

    def test_subtree_cannot_be_split_or_change_depth(self):
        for change in ('split', 'deeper'):
            with self.subTest(change=change):
                _, _, block, candidate = contract('<body><p><span>x<em>y</em></span></p><p><span>z</span></p></body>')
                if change == 'split':
                    node = candidate[0][0][0]
                    candidate[0][0].remove(node)
                    candidate[1].append(node)
                else:
                    node = candidate[0][0]
                    candidate[0].remove(node)
                    candidate[1][0].append(node)
                self.assertTrue(list(block.validate(candidate)))

    def test_nested_identified_children_cannot_exchange_parents(self):
        for sibling in ('', '<p>end</p>'):
            with self.subTest(sibling=sibling):
                _, _, block, candidate = contract(
                    '<body><p><span><em class="a"><b class="1">1</b><b class="2">2</b></em>'
                    '<em class="b"><b class="3">3</b><b class="4">4</b></em></span></p>' + sibling + '</body>')
                first, second = candidate[0][0]
                left, right = first[0], second[0]
                first.remove(left)
                second.remove(right)
                first.append(right)
                second.append(left)
                self.assertTrue(list(block.validate(candidate)))

    def test_display_formula_payload_is_not_an_inline_destination(self):
        _, _, block, candidate = contract(
            '<body><p><span>a</span></p><expression display="block">x</expression><p>b</p></body>')
        self.assertIsNone(candidate[1].get('inline-scope'))
        node = candidate[0][0]
        candidate[0].remove(node)
        candidate[1].append(node)
        self.assertTrue(list(block.validate(candidate)))

    def test_same_depth_under_other_grandparent_is_not_the_same_scope(self):
        _, _, block, candidate = contract(
            '<body><section><p><span>a</span></p><p>b</p></section>'
            '<section><p><span>c</span></p><p>d</p></section></body>')
        self.assertNotEqual(candidate[0].get('inline-scope'), candidate[2].get('inline-scope'))
        node = candidate[0][0]
        candidate[0].remove(node)
        candidate[2].append(node)
        candidate[2].set('inline-scope', candidate[0].attrib['inline-scope'])
        self.assertTrue(list(block.validate(candidate)))

    def test_unrelated_roots_in_one_request_do_not_become_siblings(self):
        roots = [fromstring('<body><p><span>a</span></p></body>'), fromstring('<body><p>b</p></body>')]
        segments = [part for root in roots for part in search_inline_segments(search_text_segments(root))]
        block = BlockSegment('xml', segments)
        candidate = block.create_element()
        self.assertIsNone(candidate[0].get('inline-scope'))
        node = candidate[0][0]
        candidate[0].remove(node)
        candidate[1].append(node)
        self.assertTrue(list(block.validate(candidate)))

    def test_protected_anchor_divides_sibling_scope(self):
        anchor = ImmutableBlockElement(Element('anchor', {'anchor_key': 'asset:1'}), 2)
        _, _, block, candidate = contract(
            '<body><p><span>a</span></p><p>b</p><p><span>c</span></p><p>d</p></body>', [anchor])
        self.assertNotEqual(candidate[0].get('inline-scope'), candidate[3].get('inline-scope'))
        node = candidate[0][0]
        candidate[0].remove(node)
        candidate[3].append(node)
        self.assertTrue(list(block.validate(candidate)))

    def test_block_order_and_whole_text_are_still_checked_at_real_output_positions(self):
        _, segments, block, candidate = contract('<body><p>A<span>x</span></p><p>B</p></body>')
        canonical = 'AxB'
        candidate[:] = list(reversed(candidate))
        self.assertEqual(list(block.validate(candidate)), [])
        self.assertIsNone(StructuredTranslation().validate(segments, canonical, candidate))
        self.assertIsNotNone(StructuredTranslation().validate(segments, 'BAx', candidate))


class SiblingInlineDeliveryTests(IsolatedAsyncioTestCase):
    async def test_epub_formulas_exchange_parents_without_exchanging_source_identity(self):
        translator, model = translator_fixture()
        model_replies(model, 'First $y$. Second $x$.', [
            '<xml><p id="1">First <expression id="4">$y$</expression>.</p>'
            '<p id="3">Second <expression id="2">$x$</expression>.</p></xml>'])
        source = fromstring('<body><p>First <math id="x"><mi>x</mi></math>.</p>'
                            '<p>Second <math id="y"><mi>y</mi></math>.</p></body>')
        adapter = XMLInterrupter()
        target, _ = await StructuredTranslation(formulas=adapter).translate(
            translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False)
        formulas = list(target.iter('math'))
        self.assertEqual([node.get('id') for node in formulas], ['y', 'x'])
        self.assertEqual([node.findtext('semantics/mi') for node in formulas], ['y', 'x'])
        self.assertEqual(adapter.visible_text(target), 'First $y$.Second $x$.')
        self.assertNotIn('inline-scope', tostring(target, encoding='unicode'))
        self.assertEqual(len(model.wire_calls), 2)

    async def test_epub_link_moves_with_href_and_document_id(self):
        translator, model = translator_fixture()
        model_replies(model, 'First. Second link.', [
            '<xml><p id="1">First.</p><p id="3">Second <a id="2">link</a>.</p></xml>'])
        source = fromstring('<body><p>First <a id="link-original" href="#target">link</a>.</p><p id="target">Second.</p></body>')
        target, _ = await StructuredTranslation(formulas=XMLInterrupter()).translate(
            translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False)
        self.assertIsNone(target[0].find('a'))
        self.assertEqual(target[1][0].attrib, {'id': 'link-original', 'href': '#target'})
        self.assertEqual(''.join(target.itertext()), 'First.Second link.')
        self.assertEqual(len(model.wire_calls), 2)

    async def test_pcex_formula_moves_between_fragments_with_source_geometry_intact(self):
        translator, model = translator_fixture()
        artist = 'First. Second $x$.'
        model_replies(model, artist, [
            '<xml><fragment id="1">First.</fragment><fragment id="3">Second '
            '<expression id="2">$x$</expression>.</fragment></xml>'])
        source = Chapter(None, -1, [TextFlowItem('body', -1, [
            SourceTextFragment(0, 1, (0, 0, 100, 20), ['First ', expression('x'), '.']),
            SourceTextFragment(0, 2, (0, 30, 100, 50), ['Second.']),
        ])])
        target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        xml = encode(target)
        fragments = list(xml.iter('fragment'))
        self.assertIsNone(fragments[0].find('inline_expr'))
        self.assertIsNotNone(fragments[1].find('inline_expr'))
        self.assertEqual([n.get('bbox') for n in fragments], ['0,0,100,20', '0,30,100,50'])
        self.assertEqual(comparison_text(''.join(xml.itertext())), comparison_text(artist))
        self.assertNotIn('inline-scope', tostring(xml, encoding='unicode'))
        self.assertEqual(len(model.wire_calls), 2)

    async def test_repair_keeps_source_scope_even_when_candidate_omits_marker(self):
        translator, model = translator_fixture()
        model_replies(model, 'First. Second link.', [
            '<xml><p id="1">Wrong.</p><p id="3">Second <a id="2">link</a>.</p></xml>',
            '<xml><p id="1">First.</p><p id="3">Second <a id="2">link</a>.</p></xml>',
        ])
        source = fromstring('<body><p>First <a href="#target">link</a>.</p><p id="target">Second.</p></body>')
        target, _ = await StructuredTranslation().translate(
            translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False)
        self.assertEqual(target[1][0].get('href'), '#target')
        self.assertEqual(len(model.wire_calls), 3)
        repair = model.wire_calls[-1][-1].message
        self.assertIn('inline-scope 1: 1, 3', repair)
        self.assertIn('Working candidate XML', repair)
        self.assertNotIn('required source structure for this repair', repair.lower())

    @policy_operation
    async def test_async_document_rebinding_retains_moved_inline_parent(self):
        translator, model = translator_fixture()
        model_replies(model, 'First. Second link.', [
            '<xml><p id="1">First.</p><p id="3">Second <a id="2">link</a>.</p></xml>'])
        source = fromstring('<body><p>First <a id="link-original" href="#target">link</a>.</p><p id="target">Second.</p></body>')
        async def tasks():
            yield TranslationTask(source, SubmitKind.REPLACE, 'chapter')
        # Exercise the disk/source identity rebinding path used by streaming
        # delivery, not just the synchronous iterable adapter.
        results = await translator._translate_elements(  # pylint: disable=protected-access
            tasks(), **StructuredTranslation().arguments())
        target, identity = results[0]
        self.assertEqual(identity, 'chapter')
        self.assertIsNone(target[0].find('a'))
        self.assertEqual(target[1][0].attrib, {'id': 'link-original', 'href': '#target'})
        self.assertEqual(''.join(target.itertext()), 'First.Second link.')
        self.assertEqual(len(model.wire_calls), 2)
