"""PCEX mixed content is data, including absent and whitespace-only tails."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft.document.content import ChapterWriter, ContentItem
from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.chapter import (
    BlockMember, Chapter, Content, DisplayFormula, InlineExpression, Reference, SourceAsset,
    SourceTextFragment, TextFlowItem, decode, encode,
)
from pdf_craft.markdown.paragraph import HTMLTag, tag_definition


class ChapterSerializationTests(TestCase):
    def test_both_writers_preserve_all_mixed_content_owners(self):
        first = InlineExpression(ExpressionKind.INLINE_PAREN, 'x')
        second = InlineExpression(ExpressionKind.INLINE_PAREN, 'y')
        emphasis = tag_definition('em')
        assert emphasis is not None
        contents: list[Content] = [[first], [first, second],
                                   [' \t', first, '\n ', second, ' '],
                                   [HTMLTag[BlockMember](emphasis, [], [first, second, ' \n'])]]
        for content in contents:
            with self.subTest(content=content), TemporaryDirectory() as folder:
                reference = Reference(1, 0, '1', [TextFlowItem('body', 0, [
                    SourceTextFragment(1, 1, (0, 20, 10, 30), deepcopy(content)),
                ])])
                source = Chapter(None, -1, [
                    TextFlowItem('body', 0, [
                        SourceTextFragment(1, 0, (0, 0, 10, 10), deepcopy(content) + [reference]),
                        SourceAsset(1, 'image', (0, 10, 10, 20),
                                    title=deepcopy(content), content=deepcopy(content), caption=deepcopy(content)),
                    ]),
                    DisplayFormula(SourceAsset(1, 'formula', (0, 30, 10, 40), content=deepcopy(content))),
                ], [reference])
                encoded = tostring(encode(source))
                self.assertEqual(decode(fromstring(encoded), allow_legacy=False), source)
                # Formatting still separates structural nodes for readability.
                self.assertIn(b'\n  <flow>', encoded)
                writer = ChapterWriter(Path(folder))
                for index, item in enumerate([*source.flow_items, reference]):
                    writer.append(ContentItem('chapter_head.xml', None, -1, index, item))
                writer.finish()
                self.assertEqual(decode(fromstring((Path(folder) / 'chapter_head.xml').read_bytes()),
                                        allow_legacy=False), source)
