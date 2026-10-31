"""Synthetic, redacted regressions retaining the production Cyrillic marks."""
# pylint: disable=protected-access
import asyncio
import unicodedata
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import AsyncMock
from tiktoken import Encoding
from xml.etree.ElementTree import Element
from pdf_craft.common import read_xml, save_xml
from pdf_craft.transformer.xml_translator.xml.friendly import encode_friendly, decode_friendly

from pdf_craft.extractor.chapter.chapter import Chapter, TextFlowItem, SourceTextFragment, encode, decode
from pdf_craft.markdown.paragraph import HTMLTag, tag_definition, flatten
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from tests.test_anchored_content_translation import _repairing_translator


def local_encoding() -> Encoding:
    # No downloadable tokenizer tables in public regression tests.
    return Encoding(name='fill-regression-bytes', pat_str=r'(?s).',
                    mergeable_ranks={bytes([value]): value for value in range(256)}, special_tokens={})


def chapter(parts=1):
    return Chapter(1, 0, [TextFlowItem('body', 0, [
        SourceTextFragment(21, i + 2, (0, i * 20, 100, i * 20 + 18), ['source'])
        for i in range(parts)])])


def fragments(value: Chapter) -> list[SourceTextFragment]:
    text = value.flow_items[0]
    assert isinstance(text, TextFlowItem)
    return [fragment for fragment in text.children if isinstance(fragment, SourceTextFragment)]


def visible(value: Chapter) -> str:
    return ''.join(part for fragment in fragments(value) for part in flatten(fragment.content)
                   if isinstance(part, str))


class FillAcceptanceRegressions(TestCase):
    def fill(self, canonical, response, parts=1, source=None):
        translator, runtime = _repairing_translator([response] * 2)
        translator._fill_llm._encoding = local_encoding()
        translator._translate_text_async = AsyncMock(return_value=canonical)
        target = asyncio.run(NarrativeXMLTransformer(translator).transform(
            source or chapter(parts), error_policy=False))
        return target, runtime.context_value.calls

    def test_redacted_russian_production_marks_accept_on_first_attempt(self):
        # Preserve actual decomposed code points; no customer wording or links.
        canonical = 'Все\u0308 хорошо, мои\u0306 друг.'
        self.assertIn('\u0308', canonical)
        self.assertIn('\u0306', canonical)
        response = '<xml><fragment id="1">Всё хорошо, мой друг.</fragment></xml>'
        target, calls = self.fill(canonical, response)
        self.assertEqual(calls, 1)
        self.assertEqual(unicodedata.normalize('NFC', visible(target)), unicodedata.normalize('NFC', canonical))
        fragment = fragments(target)[0]
        self.assertEqual((fragment.page_index, fragment.source_order, fragment.bbox), (21, 2, (0, 0, 100, 18)))

    def test_equivalence_across_scripts_and_mark_order(self):
        for canonical, actual in [('cafe\u0301', 'café'), ('\u1100\u1161', '가'),
                                  ('a\u0301\u0323', 'a\u0323\u0301')]:
            with self.subTest(canonical=canonical):
                target, calls = self.fill(canonical, f'<xml><fragment id="1">{actual}</fragment></xml>')
                self.assertEqual(calls, 1)
                self.assertEqual(unicodedata.normalize('NFC', visible(target)), unicodedata.normalize('NFC', canonical))

    def test_combining_marks_cross_fragments(self):
        for canonical, left, right in [('ё', 'е', '\u0308'), ('й', 'и', '\u0306'),
                                       ('é', 'e', '\u0301'), ('가', '\u1100', '\u1161')]:
            with self.subTest(canonical=canonical):
                target, calls = self.fill(canonical,
                    f'<xml><fragment id="1">{left}</fragment><fragment id="2">{right}</fragment></xml>', 2)
                self.assertEqual(calls, 1)
                self.assertEqual(unicodedata.normalize('NFC', visible(target)), canonical)
                self.assertEqual([f.source_order for f in fragments(target)], [2, 3])

    def test_combining_sequence_can_cross_inline_tail(self):
        source = chapter()
        definition = tag_definition('b')
        assert definition is not None
        fragments(source)[0].content = [HTMLTag(definition, [], ['source']), ' tail']
        target, calls = self.fill('ё', '<xml><fragment id="1"><b>е</b>\u0308</fragment></xml>', source=source)
        content = fragments(target)[0].content
        self.assertEqual(calls, 1)
        self.assertIsInstance(content[0], HTMLTag)
        self.assertEqual(unicodedata.normalize('NFC', visible(target)), 'ё')

    def persisted_equivalent(self, source, target, canonical):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chapter.xml'
            save_xml(encode(target), path)
            restored = decode(read_xml(path))
        self.assertEqual(unicodedata.normalize('NFC', visible(restored)),
                         unicodedata.normalize('NFC', canonical))
        self.assertEqual([(f.page_index, f.source_order, f.bbox) for f in fragments(restored)],
                         [(f.page_index, f.source_order, f.bbox) for f in fragments(source)])

    def test_marks_cannot_reorder_across_canonical_whitespace(self):
        cases = [('a\u0301 \u0323', 'a\u0323\u0301'),
                 ('a\u0301 \u0323', 'a\u0323 \u0301'),
                 ('a\u0301 \u0323', 'ạ\u0301'),
                 # These pass the preliminary normalized-character filter;
                 # only checking the final rebuilt text can reject them.
                 ('a\u0323 \u0301', 'a\u0301\u0323'),
                 ('a\u0323 \u0301', 'á\u0323')]
        for canonical, actual in cases:
            with self.subTest(canonical=canonical, actual=actual), self.assertRaisesRegex(Exception, 'canonical translation'):
                self.fill(canonical, f'<xml><fragment id="1">{actual}</fragment></xml>')

    def test_whitespace_barriers_survive_fragment_text_and_tail_boundaries(self):
        definition = tag_definition('b')
        assert definition is not None
        inline_after = chapter()
        fragments(inline_after)[0].content = ['source ', HTMLTag(definition, [], ['tail'])]
        inline_before = chapter()
        fragments(inline_before)[0].content = [HTMLTag(definition, [], ['source']), ' tail']
        cases = [
            (chapter(2), '<fragment id="1">a</fragment><fragment id="2">\u0323\u0301</fragment>'),
            (chapter(2), '<fragment id="1">a\u0323</fragment><fragment id="2"> \u0301</fragment>'),
            (inline_after, '<fragment id="1">a<b>\u0323\u0301</b></fragment>'),
            (inline_after, '<fragment id="1">a\u0323<b> \u0301</b></fragment>'),
            (inline_before, '<fragment id="1"><b>a</b>\u0323\u0301</fragment>'),
            (inline_before, '<fragment id="1"><b>a\u0323</b> \u0301</fragment>'),
        ]
        for source, response in cases:
            for swap in (False, True):
                canonical = 'a\u0301 \u0323'
                candidate = response
                if swap:
                    exchange = str.maketrans({'\u0301': '\u0323', '\u0323': '\u0301'})
                    canonical = canonical.translate(exchange)
                    candidate = response.translate(exchange)
                with self.subTest(response=candidate), self.assertRaisesRegex(Exception, 'canonical translation'):
                    self.fill(canonical, f'<xml>{candidate}</xml>', source=source)

    def test_decomposition_preserves_character_offsets_when_restoring_gaps(self):
        for canonical, actual in [('a\u0301 \u0323', 'á\u0323'),
                                  ('a\u0301\u0323 b', 'a\u0323\u0301 b'),
                                  ('á \u0323', 'a\u0301 \u0323')]:
            with self.subTest(canonical=canonical, actual=actual):
                source = chapter()
                target, calls = self.fill(canonical, f'<xml><fragment id="1">{actual}</fragment></xml>', source=source)
                self.assertEqual(calls, 1)
                self.persisted_equivalent(source, target, canonical)

    def test_correct_mark_and_space_recovery_across_owned_boundaries(self):
        source = chapter(2)
        fragments(source)[0].content = ['source ']
        canonical = 'a\u0301 \u0323'
        response = '<xml><fragment id="1">á</fragment><fragment id="2">\u0323</fragment></xml>'
        target, calls = self.fill(canonical, response, source=source)
        self.assertEqual(calls, 1)
        self.persisted_equivalent(source, target, canonical)
        definition = tag_definition('b')
        assert definition is not None
        source = chapter()
        fragments(source)[0].content = [HTMLTag(definition, [], ['source ']), 'tail']
        target, calls = self.fill(canonical, '<xml><fragment id="1"><b>á</b>\u0323</fragment></xml>', source=source)
        self.assertEqual(calls, 1)
        self.persisted_equivalent(source, target, canonical)

    def test_equivalent_mark_reordering_before_spaces_can_cross_slots(self):
        canonical = 'a\u0301\u0323 b'
        source = chapter(2)
        target, calls = self.fill(canonical,
            '<xml><fragment id="1">a\u0301</fragment><fragment id="2">\u0323 b</fragment></xml>', source=source)
        self.assertEqual(calls, 1)
        self.persisted_equivalent(source, target, canonical)
        definition = tag_definition('b')
        assert definition is not None
        source = chapter()
        fragments(source)[0].content = [HTMLTag(definition, [], ['source']), ' tail']
        target, calls = self.fill(canonical,
            '<xml><fragment id="1"><b>a\u0301</b>\u0323 b</fragment></xml>', source=source)
        self.assertEqual(calls, 1)
        self.persisted_equivalent(source, target, canonical)

    def test_meaningful_differences_are_not_normalized_away(self):
        for canonical, actual in [('е', 'ё'), ('и', 'й'), ('e', 'é'), ('A', 'a'),
                                  ('①', '1'), ('²', '2'), ('a\u00a0b', 'a b'),
                                  ('a\u202fb', 'a b'), ('word.', 'word'), ('12', '13')]:
            with self.subTest(canonical=canonical):
                with self.assertRaisesRegex(Exception, 'canonical translation'):
                    self.fill(canonical, f'<xml><fragment id="1">{actual}</fragment></xml>')

    def test_numeric_id_whitespace_never_skips_canonical_validation(self):
        target, calls = self.fill('RIGHT', '<xml><fragment id=" 1 ">RIGHT</fragment></xml>')
        self.assertEqual((visible(target), calls), ('RIGHT', 1))
        for identifier in (' 1 ', '01', '&#49;', '&#x31;'):
            with self.subTest(identifier=identifier), self.assertRaisesRegex(Exception, 'canonical translation'):
                self.fill('RIGHT', f'<xml><fragment id="{identifier}">WRONG</fragment></xml>')

    def test_duplicate_ids_rejected_before_adoption(self):
        for first, second in [('WRONG', 'RIGHT'), ('RIGHT', 'WRONG'), ('RIGHT', 'RIGHT')]:
            for identifier in ('1', '01', ' 1 ', '&#49;'):
                with self.subTest(first=first, identifier=identifier):
                    with self.assertRaisesRegex(Exception, 'Duplicate ID'):
                        self.fill('RIGHT', f'<xml><fragment id="1">{first}</fragment>'
                                  f'<fragment id="{identifier}">{second}</fragment></xml>')

    def test_nested_inline_ids_share_the_request_namespace(self):
        source = chapter()
        definition = tag_definition('b')
        assert definition is not None
        fragments(source)[0].content = [HTMLTag(definition, [('class', 'a')], ['first']),
                                        HTMLTag(definition, [('class', 'b')], ['second'])]
        for second_id in ('2', '02', ' 2 ', '1'):
            with self.subTest(second_id=second_id), self.assertRaises(Exception):
                self.fill('RIGHT', '<xml><fragment id="1"><b id="2">RIGHT</b>'
                          f'<b id="{second_id}"/></fragment></xml>', source=source)

    def test_conflicting_response_does_not_poison_next_candidate(self):
        translator, runtime = _repairing_translator([
            '<xml><fragment id="1">WRONG</fragment><fragment id="1">RIGHT</fragment></xml>',
            '<xml><fragment id="1">RIGHT</fragment></xml>'])
        translator._fill_llm._encoding = local_encoding()
        translator._translate_text_async = AsyncMock(return_value='RIGHT')
        target = asyncio.run(NarrativeXMLTransformer(translator).transform(chapter(), error_policy=False))
        self.assertEqual((visible(target), runtime.context_value.calls), ('RIGHT', 2))

    def test_legal_xml_attribute_forms_do_not_spend_repairs(self):
        for attribute in ("id='1'", 'id = "1"', 'id\t=\r\n"1"', 'id="&#49;"', 'id="&#x31;"'):
            with self.subTest(attribute=attribute):
                target, calls = self.fill('RIGHT', f'<xml><fragment\t{attribute}>RIGHT</fragment></xml>')
                self.assertEqual((visible(target), calls), ('RIGHT', 1))

    def test_ambiguous_or_double_encoded_attributes_are_not_guessed(self):
        for attributes in ('id="2" id="1"', 'id="&amp;#49;"', 'id="unknown"'):
            with self.subTest(attributes=attributes), self.assertRaises(Exception):
                self.fill('RIGHT', f'<xml><fragment {attributes}>RIGHT</fragment></xml>')

    def test_attribute_entities_round_trip_once(self):
        for value in ('A&B', '&amp;', '&#49;', '&lt;span&gt;', 'a"b', "a'b"):
            with self.subTest(value=value):
                root = Element('span', {'data-hint': value})
                encoded = encode_friendly(root)
                decoded = next(decode_friendly(encoded, tags='span'))
                self.assertEqual(decoded.get('data-hint'), value)

    def test_text_entities_decode_once_without_creating_structure(self):
        for canonical, encoded in [('A & B', 'A &amp; B'), ('café', 'caf&#233;'),
                                   ('<span>', '&lt;span&gt;'), ('&lt;span&gt;', '&amp;lt;span&amp;gt;')]:
            target, calls = self.fill(canonical, f'<xml><fragment id="1">{encoded}</fragment></xml>')
            self.assertEqual((visible(target), calls), (canonical, 1))
