"""PCEX formula interruption tests without network-backed language models."""

from __future__ import annotations

import unittest
from typing import cast
from xml.etree.ElementTree import Element, tostring

from tiktoken import get_encoding

from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.chapter import (
    AssetLayout,
    BlockLayout,
    BlockMember,
    Chapter,
    InlineExpression,
    ParagraphLayout,
    encode,
)
from pdf_craft.markdown.paragraph import HTMLTag, tag_definition
from pdf_craft.transformer.chapter_xml import ChapterXMLTransformer
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.xml_translator.xml_translator.callbacks import warp_callbacks
from pdf_craft.transformer.xml_translator.xml_translator.stream_mapper import XMLStreamMapper
from pdf_craft.transformer.xml_translator.xml_translator.submitter import submit


class _FormulaAwareTranslator:
    """A deterministic XMLTranslator stand-in that deliberately corrupts formulas."""

    def __init__(self, max_group_score: int = 10_000) -> None:
        self.model_sources: list[str] = []
        self._max_group_score = max_group_score

    def translate_element(self, task, **kwargs):
        source_hook = kwargs["interrupt_source_text_segments"]

        def record_source(segments):
            interrupted = list(source_hook(segments))
            return iter(interrupted)

        callbacks = warp_callbacks(
            interrupt_source_text_segments=record_source,
            interrupt_translated_text_segments=kwargs["interrupt_translated_text_segments"],
            interrupt_block_element=kwargs["interrupt_block_element"],
            on_fill_failed=None,
        )
        mapper = XMLStreamMapper(get_encoding("cl100k_base"), max_group_score=self._max_group_score)

        def translate_group(inline_segments):
            self.model_sources.append(
                "\n\n".join("".join(segment.text for segment in inline) for inline in inline_segments)
            )
            mappings = []
            for inline in inline_segments:
                template = inline.create_element()
                self._corrupt_formula_text_and_move_it(template)
                assigned = inline.assign_attributes(template)
                mappings.append((inline.parent, list(search_text_segments(assigned))))
            return mappings

        translated = task.element
        for element, mappings in mapper.map_stream(
            elements=iter((task.element,)), callbacks=callbacks, map=translate_group, concurrency=1,
        ):
            translated = submit(element, task.action, mappings)
        return translated, task.payload

    def _corrupt_formula_text_and_move_it(self, element: Element) -> None:
        formulas = [child for child in element if child.tag == "expression"]
        if formulas:
            # The expression content is deliberately changed. The interrupter
            # must recover the source formula rather than accepting this text.
            formula = formulas[0]
            formula.text = "MODEL_CHANGED_FORMULA"
            formula.tail = "译文"
            return
        for node in element.iter():
            if node.text:
                node.text = "译:" + node.text
            if node.tail:
                node.tail = "译:" + node.tail


class _ReorderingFormulaTranslator(_FormulaAwareTranslator):
    """Simulate a fill model that moves one frozen token before natural text."""

    def _corrupt_formula_text_and_move_it(self, element: Element) -> None:
        formulas = [child for child in element if child.tag == "expression"]
        if len(formulas) == 1:
            formula = formulas[0]
            element.text = None
            formula.text = "MODEL_CHANGED_FORMULA"
            formula.tail = "译文后"
            return
        super()._corrupt_formula_text_and_move_it(element)


class TestChapterFormulaTranslation(unittest.TestCase):
    def test_inline_formulas_are_visible_to_translation_and_restored_after_reordering(self):
        chapter = Chapter(None, 0, [
            ParagraphLayout("text", 0, [BlockLayout(
                1, 1, (1, 1, 100, 30), [
                    "Before ",
                    InlineExpression(ExpressionKind.INLINE_DOLLAR, "x^2"),
                    " and ",
                    InlineExpression(ExpressionKind.INLINE_PAREN, r"\chi"),
                    ".",
                ],
            )]),
        ])
        translator = _FormulaAwareTranslator()

        translated = ChapterXMLTransformer(translator).transform(chapter)

        source = "\n".join(translator.model_sources)
        self.assertIn("$x^2$", source)
        self.assertIn(r"\(\chi\)", source)
        layout = translated.layouts[0]
        self.assertIsInstance(layout, ParagraphLayout)
        assert isinstance(layout, ParagraphLayout)
        content = layout.blocks[0].content
        formulas: list[InlineExpression] = [
            item for item in content if isinstance(item, InlineExpression)
        ]
        self.assertEqual(
            [(formula.kind, formula.content) for formula in formulas],
            [
                (ExpressionKind.INLINE_DOLLAR, "x^2"),
                (ExpressionKind.INLINE_PAREN, r"\chi"),
            ],
        )
        self.assertNotIn("MODEL_CHANGED_FORMULA", tostring(encode(translated), encoding="unicode"))
        self.assertNotIn("__PDF_CRAFT_CHAPTER_FORMULA_ID", tostring(encode(translated), encoding="unicode"))

    def test_inline_formula_is_restored_when_the_model_moves_its_token(self):
        chapter = Chapter(None, 0, [
            ParagraphLayout("text", 0, [BlockLayout(
                1, 1, (1, 1, 100, 30), [
                    "Before ",
                    InlineExpression(ExpressionKind.INLINE_PAREN, r"\alpha"),
                    " after.",
                ],
            )]),
        ])

        translated = ChapterXMLTransformer(_ReorderingFormulaTranslator()).transform(chapter)

        layout = translated.layouts[0]
        self.assertIsInstance(layout, ParagraphLayout)
        assert isinstance(layout, ParagraphLayout)
        content = layout.blocks[0].content
        self.assertIsInstance(content[0], InlineExpression)
        assert isinstance(content[0], InlineExpression)
        self.assertEqual((content[0].kind, content[0].content), (ExpressionKind.INLINE_PAREN, r"\alpha"))
        self.assertIn("译文后", "".join(item for item in content if isinstance(item, str)))

    def test_inline_formulas_survive_html_and_cross_page_paragraph_blocks(self):
        emphasis = tag_definition("em")
        assert emphasis is not None
        chapter = Chapter(None, 0, [
            ParagraphLayout("text", 0, [
                BlockLayout(1, 1, (1, 1, 100, 30), [
                    "First ", HTMLTag(
                        definition=emphasis,
                        attributes=[],
                        children=cast(
                            list[str | BlockMember | HTMLTag[BlockMember]],
                            ["wrapped ", InlineExpression(ExpressionKind.INLINE_DOLLAR, "x")],
                        ),
                    ),
                ]),
                BlockLayout(2, 2, (1, 1, 100, 30), [
                    "Second ", InlineExpression(ExpressionKind.INLINE_PAREN, r"\beta"), ".",
                ]),
            ]),
        ])

        translated = ChapterXMLTransformer(_FormulaAwareTranslator()).transform(chapter)

        layout = translated.layouts[0]
        self.assertIsInstance(layout, ParagraphLayout)
        assert isinstance(layout, ParagraphLayout)
        self.assertEqual([block.page_index for block in layout.blocks], [1, 2])
        encoded = tostring(encode(translated), encoding="unicode")
        self.assertIn('<inline_expr kind="$">x</inline_expr>', encoded)
        self.assertIn(r'<inline_expr kind="\(">\beta</inline_expr>', encoded)
        self.assertIn("<em>", encoded)

    def test_equation_asset_is_context_and_is_never_rewritten(self):
        equation = AssetLayout(
            page_index=1,
            ref="equation",
            det=(10, 40, 90, 60),
            title=["Equation title"],
            content=[r"\int_0^1 x^2 dx"],
            caption=["Equation caption"],
            hash="equation-image",
        )
        chapter = Chapter(None, 0, [
            ParagraphLayout("text", 0, [BlockLayout(1, 1, (1, 1, 100, 30), ["Before equation."])]),
            equation,
            ParagraphLayout("text", 0, [BlockLayout(1, 2, (1, 70, 100, 100), ["After equation."])]),
        ])
        translator = _FormulaAwareTranslator(max_group_score=1)

        translated = ChapterXMLTransformer(translator).transform(chapter)

        self.assertTrue(any(
            "Before equation." in source and r"$$\int_0^1 x^2 dx$$" in source
            for source in translator.model_sources
        ))
        self.assertTrue(any(
            r"$$\int_0^1 x^2 dx$$" in source and "After equation." in source
            for source in translator.model_sources
        ))
        restored = translated.layouts[1]
        self.assertIsInstance(restored, AssetLayout)
        assert isinstance(restored, AssetLayout)
        self.assertEqual(restored, equation)

    def test_equation_asset_with_title_or_caption_is_frozen_as_a_whole(self):
        for title, caption in [(["Title"], []), ([], ["Caption"]), (["Title"], ["Caption"])]:
            with self.subTest(title=title, caption=caption):
                equation = AssetLayout(
                    page_index=1,
                    ref="equation",
                    det=(10, 40, 90, 60),
                    title=title,
                    content=[r"x^2"],
                    caption=caption,
                    hash="equation-metadata",
                )
                chapter = Chapter(None, 0, [
                    ParagraphLayout("text", 0, [BlockLayout(
                        1, 1, (1, 1, 100, 30), ["Before."],
                    )]),
                    equation,
                    ParagraphLayout("text", 0, [BlockLayout(
                        1, 2, (1, 70, 100, 100), ["After."],
                    )]),
                ])

                translated = ChapterXMLTransformer(_FormulaAwareTranslator()).transform(chapter)

                self.assertEqual(translated.layouts[1], equation)

    def test_equation_only_chapter_keeps_the_asset_and_does_not_skip_translation(self):
        equation = AssetLayout(
            page_index=1,
            ref="equation",
            det=(10, 40, 90, 60),
            title=[],
            content=[r"x^2 + y^2 = z^2"],
            caption=[],
            hash="equation-only",
        )
        translator = _FormulaAwareTranslator(max_group_score=1)

        translated = ChapterXMLTransformer(translator).transform(Chapter(None, 0, [equation]))

        self.assertTrue(any(r"$$x^2 + y^2 = z^2$$" in source for source in translator.model_sources))
        self.assertEqual(translated.layouts, [equation])

    def test_chapter_without_formula_keeps_normal_xml_translation(self):
        chapter = Chapter(None, 0, [
            ParagraphLayout("text", 0, [BlockLayout(1, 1, (1, 1, 100, 30), ["plain text"])]),
        ])

        translated = ChapterXMLTransformer(_FormulaAwareTranslator()).transform(chapter)

        layout = translated.layouts[0]
        self.assertIsInstance(layout, ParagraphLayout)
        assert isinstance(layout, ParagraphLayout)
        content = layout.blocks[0].content
        self.assertEqual(content, ["译:plain text"])


if __name__ == "__main__":
    unittest.main()
