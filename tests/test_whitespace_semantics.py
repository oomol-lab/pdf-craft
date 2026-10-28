"""CSS Text transport subset and algorithmic recovery (no model requests)."""
# pylint: disable=protected-access
from copy import deepcopy
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring, tostring
from pdf_craft.extractor.chapter.chapter import TextFlowItem, SourceTextFragment
from pdf_craft.markdown.paragraph import HTMLTag, tag_definition

from pdf_craft.transformer.whitespace import WhiteSpace, normalize, mode_for, can_indent
from pdf_craft.transformer.canonical_text import restore_canonical_whitespace
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.xml_translator.xml.friendly import encode_friendly, decode_friendly
from tests.test_anchored_content_translation import _repairing_translator
from tests.test_fill_text_protocol import source_chapter


class WhiteSpaceStandardsTests(TestCase):
    def test_modes_match_css_text_whitespace_table(self):
        text = ' a\t  b \r\n  c '
        for mode, expected in (
                (WhiteSpace.NORMAL, ' a b c '), (WhiteSpace.NOWRAP, ' a b c '),
                (WhiteSpace.PRE_LINE, ' a b\nc '),
                (WhiteSpace.PRE, ' a\t  b \n  c '),
                (WhiteSpace.PRE_WRAP, ' a\t  b \n  c '),
                (WhiteSpace.BREAK_SPACES, ' a\t  b \n  c ')):
            with self.subTest(mode=mode):
                self.assertEqual(normalize(text, mode), expected)
                self.assertEqual(normalize(expected, mode), expected)

    def test_special_spaces_are_not_document_whitespace(self):
        text = 'a\u00a0\u2009\u202f\u3000b'
        self.assertEqual(normalize(text, trim=True), text)
        self.assertEqual(normalize('\u00a0x\u00a0', trim=True), '\u00a0x\u00a0')

    def test_context_inheritance_overrides_and_html_defaults(self):
        self.assertEqual(mode_for(fromstring('<pre/>')), WhiteSpace.PRE)
        self.assertEqual(mode_for(fromstring('<textarea/>')), WhiteSpace.PRE_WRAP)
        self.assertEqual(mode_for(fromstring('<span/>'), WhiteSpace.PRE), WhiteSpace.PRE)
        self.assertEqual(mode_for(fromstring('<span style="white-space: normal"/>'), WhiteSpace.PRE), WhiteSpace.NORMAL)
        self.assertEqual(mode_for(fromstring('<p style="white-space: pre !important; white-space: normal"/>')), WhiteSpace.PRE)
        self.assertEqual(mode_for(fromstring('<p xml:space="preserve"/>')), WhiteSpace.PRE)

    def test_only_block_only_containers_get_indentation(self):
        root = fromstring('<div><div><p>first</p><p>second</p></div><p>last</p></div>')
        rendered = encode_friendly(root)
        self.assertIn('\n  <div>\n    <p>first</p>\n    <p>second</p>\n  </div>', rendered)
        for value in ('<div>hello<span>world</span></div>',
                      '<span><p>one</p><p>two</p></span>',
                      '<div display="inline"><p>one</p><p>two</p></div>',
                      '<div style="white-space: pre"><p>one</p><p>two</p></div>'):
            element = fromstring(value)
            self.assertFalse(can_indent(element, mode_for(element)))
            self.assertNotIn('\n', encode_friendly(element))

    def test_friendly_normalizes_without_creating_inline_separators(self):
        root = fromstring('<div>hello   <span> world</span>\n again<span>!</span></div>')
        rendered = encode_friendly(root)
        self.assertIn('hello <span> world</span> again<span>!</span>', rendered)
        decoded = next(decode_friendly(rendered, tags='div'))
        self.assertEqual(normalize(''.join(root.itertext()), trim=True),
                         normalize(''.join(decoded.itertext()), trim=True))
        pre = fromstring('<pre>hello   <span> world</span>\n again</pre>')
        self.assertEqual(''.join(next(decode_friendly(encode_friendly(pre), tags='pre')).itertext()),
                         ''.join(pre.itertext()))
        self.assertEqual(encode_friendly(fromstring('<p>hello<span>world</span></p>')),
                         '<p>hello<span>world</span></p>')
        preserved = fromstring('<div xml:space="preserve">a  <span>b</span>\n c</div>')
        decoded = next(decode_friendly(encode_friendly(preserved), tags='div'))
        self.assertEqual(tostring(decoded), tostring(preserved))


class WhitespaceRecoveryTests(TestCase):
    def test_missing_protected_edges_restore_only_with_unique_field(self):
        for mode in ('pre', 'pre-wrap', 'break-spaces', 'pre-line'):
            gaps = ('\n',) if mode == 'pre-line' else (' ', '  ', '\t', '\n')
            for gap in gaps:
                for canonical in (gap + 'a', 'a' + gap, gap + 'a' + gap):
                    with self.subTest(mode=mode, canonical=canonical):
                        source = fromstring(f'<fragment white-space="{mode}">source</fragment>')
                        candidate = fromstring('<fragment>a</fragment>')
                        self.assertTrue(restore_canonical_whitespace([candidate], canonical, [source]))
                        self.assertEqual(candidate.text, canonical)

    def test_missing_protected_edges_at_mixed_boundary_are_not_padding(self):
        for mode in ('pre', 'pre-wrap', 'break-spaces', 'pre-line'):
            for canonical in (' a', 'a ', '\na', 'a\n'):
                with self.subTest(mode=mode, canonical=canonical):
                    source = fromstring(f'<fragment><span white-space="{mode}">source</span></fragment>')
                    candidate = fromstring('<fragment><span>a</span></fragment>')
                    before = tostring(candidate)
                    self.assertFalse(restore_canonical_whitespace([candidate], canonical, [source]))
                    self.assertEqual(tostring(candidate), before)

    def test_ambiguous_preserved_boundary_cannot_redistribute_gap(self):
        source = fromstring('<fragment><b white-space="pre">left </b><i white-space="pre"> right</i></fragment>')
        candidate = fromstring('<fragment><b>a </b><i> b</i></fragment>')
        before = tostring(candidate)
        self.assertFalse(restore_canonical_whitespace([candidate], 'a\nb', [source]))
        self.assertEqual(tostring(candidate), before)

    def test_unmatched_source_never_uses_model_preservation_attributes(self):
        source = fromstring('<fragment><b white-space="pre">source</b></fragment>')
        candidate = fromstring('<fragment><i white-space="normal">a b</i></fragment>')
        before = tostring(candidate)
        self.assertFalse(restore_canonical_whitespace([candidate], 'a  b', [source]))
        self.assertEqual(tostring(candidate), before)

    def test_preserved_sibling_does_not_own_normal_tail_space(self):
        source = fromstring('<fragment><span white-space="pre">source</span> tail</fragment>')
        for gap in (' ', '  ', '\n  '):
            candidate = fromstring(f'<fragment><span>a</span>{gap}b</fragment>')
            self.assertTrue(restore_canonical_whitespace([candidate], 'a  b', [source]))
            self.assertEqual(candidate[0].text, 'a')
            self.assertEqual(candidate[0].tail, ' b')
        misplaced = fromstring('<fragment><span>a </span> b</fragment>')
        self.assertTrue(restore_canonical_whitespace([misplaced], 'a  b', [source]))
        self.assertEqual(misplaced[0].text, 'a')
        self.assertEqual(misplaced[0].tail, ' b')

    def test_normal_gap_recovery_is_transactional_and_keeps_special_content(self):
        for response, canonical, expected in (
                ('<fragment>  Helloworld. \n</fragment>', 'Hello world.', 'Hello world.'),
                ('<fragment>one word</fragment>', 'oneword', 'oneword'),
                ('<fragment>Hello\n  world</fragment>', 'Hello   world', 'Hello world'),
                ('<fragment>Hello\u00a0world</fragment>', 'Hello\u00a0world', 'Hello\u00a0world')):
            root = fromstring(response)
            self.assertTrue(restore_canonical_whitespace([root], canonical))
            self.assertEqual(''.join(root.itertext()), expected)
        for response, canonical in (('<fragment>wrong word</fragment>', 'right word'),
                                    ('<fragment>word?</fragment>', 'word!'),
                                    ('<fragment>a b</fragment>', 'a\u00a0b')):
            root = fromstring(response)
            before = tostring(root)
            self.assertFalse(restore_canonical_whitespace([root], canonical))
            self.assertEqual(tostring(root), before)

    def test_missing_gap_between_styled_slots_requires_unique_owner(self):
        root = fromstring('<fragment><span id="2">Hello</span><span id="3">world</span></fragment>')
        before = tostring(root)
        self.assertFalse(restore_canonical_whitespace([root], 'Hello world'))
        self.assertEqual(tostring(root), before)
        source = fromstring('<fragment><span id="2">First </span><span id="3">second</span></fragment>')
        self.assertTrue(restore_canonical_whitespace([root], 'Hello world', [source]))
        self.assertEqual(root[0].text, 'Hello ')
        self.assertEqual(root[1].text, 'world')

    def test_preserved_whitespace_uses_source_context_not_model_attributes(self):
        for mode in ('pre', 'pre-wrap', 'break-spaces'):
            source = fromstring(f'<fragment style="white-space:{mode}">source</fragment>')
            root = fromstring('<fragment white-space="normal">a b</fragment>')
            self.assertTrue(restore_canonical_whitespace([root], 'a  b', [source]))
            self.assertEqual(root.text, 'a  b')
        source = fromstring('<fragment style="white-space:pre-line"/>')
        root = fromstring('<fragment>a \n b</fragment>')
        self.assertTrue(restore_canonical_whitespace([root], 'a\nb', [source]))
        self.assertEqual(''.join(root.itertext()), 'a\nb')
        self.assertTrue(restore_canonical_whitespace([root], 'a b', [source]))
        self.assertEqual(root.text, 'a b')


class FillRecoveryRequestTests(IsolatedAsyncioTestCase):
    async def test_unique_protected_gaps_recover_in_one_fill(self):
        for mode in ('pre', 'pre-wrap', 'break-spaces', 'pre-line'):
            cases = (
                ('source', 'ab', '', '', 'a\nb', 'a\nb'),
                (' source', 'a', ' ', '', ' a', ' a'),
                ('source ', 'a', '', ' ', 'a ', 'a '),
            )
            for original, candidate, head, tail, canonical, expected in cases:
                with self.subTest(mode=mode, canonical=canonical):
                    source = source_chapter()
                    item = source.flow_items[0]
                    assert isinstance(item, TextFlowItem)
                    fragment = item.children[0]
                    assert isinstance(fragment, SourceTextFragment)
                    span = tag_definition('span')
                    assert span is not None
                    style = [('style', f'white-space: {mode}')]
                    fragment.content = [HTMLTag(span, style, [original])]
                    response = f'<xml><fragment id="1">{head}<span>{candidate}</span>{tail}</fragment></xml>'
                    translator, runtime = _repairing_translator([response])
                    translator._translate_text_async = AsyncMock(return_value=canonical)
                    target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
                    self.assertEqual(runtime.context_value.calls, 1)
                    result = target.flow_items[0]
                    assert isinstance(result, TextFlowItem)
                    result_fragment = result.children[0]
                    assert isinstance(result_fragment, SourceTextFragment)
                    self.assertEqual(len(result_fragment.content), 1)
                    tag = result_fragment.content[0]
                    assert isinstance(tag, HTMLTag)
                    self.assertEqual(tag.attributes, style)
                    self.assertEqual(tag.children, [expected])

    async def test_missing_protected_edges_retry_without_discarding_canonical(self):
        for mode in ('pre', 'pre-wrap', 'break-spaces', 'pre-line'):
            canonicals = ('\na', 'a\n') if mode == 'pre-line' else (' a', 'a ', '\na', 'a\n')
            for canonical in canonicals:
                with self.subTest(mode=mode, canonical=canonical):
                    source = source_chapter()
                    item = source.flow_items[0]
                    assert isinstance(item, TextFlowItem)
                    fragment = item.children[0]
                    assert isinstance(fragment, SourceTextFragment)
                    span = tag_definition('span')
                    assert span is not None
                    style = [('style', f'white-space: {mode}')]
                    fragment.content = [HTMLTag(span, style, ['source'])]
                    wrong = '<xml><fragment id="1"><span>a</span></fragment></xml>'
                    corrected = wrong.replace('>a<', f'>{canonical}<')
                    translator, runtime = _repairing_translator([wrong, corrected])
                    translator._translate_text_async = AsyncMock(return_value=canonical)
                    target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
                    self.assertEqual(runtime.context_value.calls, 2)
                    result = target.flow_items[0]
                    assert isinstance(result, TextFlowItem)
                    result_fragment = result.children[0]
                    assert isinstance(result_fragment, SourceTextFragment)
                    tag = result_fragment.content[0]
                    assert isinstance(tag, HTMLTag)
                    self.assertEqual(tag.attributes, style)
                    self.assertEqual(tag.children, [canonical])

    async def test_no_id_different_tags_reorder_keeps_source_pre_during_adoption(self):
        source = source_chapter()
        item = source.flow_items[0]
        assert isinstance(item, TextFlowItem)
        fragment = item.children[0]
        assert isinstance(fragment, SourceTextFragment)
        bold, italic = tag_definition('b'), tag_definition('i')
        assert bold is not None and italic is not None
        fragment.content = [HTMLTag(bold, [('style', 'white-space: pre')], ['source']),
                            HTMLTag(italic, [], ['other'])]
        wrong = '<xml><fragment id="1"><i/><b>a b</b></fragment></xml>'
        translator, runtime = _repairing_translator([wrong])
        translator._translate_text_async = AsyncMock(return_value='a  b')
        target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        self.assertEqual(runtime.context_value.calls, 1)
        result = target.flow_items[0]
        assert isinstance(result, TextFlowItem)
        result_fragment = result.children[0]
        assert isinstance(result_fragment, SourceTextFragment)
        b = result_fragment.content[1]
        assert isinstance(b, HTMLTag)
        self.assertEqual(b.attributes, [('style', 'white-space: pre')])
        self.assertEqual(b.children, ['a  b'])

    async def test_normal_tail_adjacent_to_pre_needs_no_extra_fill(self):
        for gap, inside in ((' ', ''), ('  ', ''), ('', '  '), (' ', ' ')):
            source = source_chapter()
            item = source.flow_items[0]
            assert isinstance(item, TextFlowItem)
            fragment = item.children[0]
            assert isinstance(fragment, SourceTextFragment)
            span = tag_definition('span')
            assert span is not None
            fragment.content = [HTMLTag(span, [('style', 'white-space: pre')], ['source']), ' tail']
            response = f'<xml><fragment id="1"><span>a{inside}</span>{gap}b</fragment></xml>'
            translator, runtime = _repairing_translator([response])
            translator._translate_text_async = AsyncMock(return_value='a  b')
            target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
            self.assertEqual(runtime.context_value.calls, 1)
            result = target.flow_items[0]
            assert isinstance(result, TextFlowItem)
            result_fragment = result.children[0]
            assert isinstance(result_fragment, SourceTextFragment)
            content = result_fragment.content
            self.assertEqual(content[1], ' b')
            first = content[0]
            assert isinstance(first, HTMLTag)
            self.assertEqual(first.children, ['a'])
            self.assertEqual(first.attributes, [('style', 'white-space: pre')])

    async def test_source_preserved_inline_context_survives_the_whole_fill_path(self):
        source = source_chapter()
        item = source.flow_items[0]
        assert isinstance(item, TextFlowItem)
        fragment = item.children[0]
        assert isinstance(fragment, SourceTextFragment)
        definition = tag_definition('span')
        assert definition is not None
        fragment.content = [HTMLTag(definition, [('style', 'white-space: pre')], ['source  text'])]
        wrong = '<xml><fragment id="1"><span>Hello world</span></fragment></xml>'
        translator, runtime = _repairing_translator([wrong])
        translator._translate_text_async = AsyncMock(return_value='Hello  world')
        target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        self.assertEqual(runtime.context_value.calls, 1)
        self.assertIn('white-space="pre"', runtime.context_value.messages[0][0][1].message)
        text = target.flow_items[0]
        assert isinstance(text, TextFlowItem)
        result = text.children[0]
        assert isinstance(result, SourceTextFragment)
        span = result.content[0]
        assert isinstance(span, HTMLTag)
        self.assertEqual(span.children, ['Hello  world'])
        self.assertEqual(span.attributes, [('style', 'white-space: pre')])

    async def test_recoverable_normal_text_costs_one_fill_request(self):
        translator, runtime = _repairing_translator([
            '<xml><fragment id="1">  Helloworld. \n</fragment></xml>',
        ])
        translator._translate_text_async = AsyncMock(return_value='Hello world.')
        target = await NarrativeXMLTransformer(translator).transform(source_chapter(), error_policy=False)
        self.assertEqual(runtime.context_value.calls, 1)
        item = target.flow_items[0]
        assert isinstance(item, TextFlowItem)
        fragment = item.children[0]
        assert isinstance(fragment, SourceTextFragment)
        self.assertEqual(fragment.content, ['Hello world.'])

    async def test_ambiguous_boundary_retries_then_accepts_explicit_mapping(self):
        wrong = '<xml><fragment id="1"><span id="2">Hello</span><span id="3">world</span><span id="4"/></fragment></xml>'
        good = wrong.replace('Hello</span>', 'Hello </span>')
        translator, runtime = _repairing_translator([wrong, good])
        translator._translate_text_async = AsyncMock(return_value='Hello world')
        await NarrativeXMLTransformer(translator).transform(deepcopy(source_chapter(styled=True)), error_policy=False)
        self.assertEqual(runtime.context_value.calls, 2)
