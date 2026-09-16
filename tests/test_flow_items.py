from xml.etree.ElementTree import fromstring, tostring

from pdf_craft.extractor.chapter import (
    Chapter, DisplayFormula, InlineExpression, SourceAsset, SourceTextFragment,
    StandaloneAsset, TextFlowItem, decode, encode,
)
from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.generation import _assemble_flow_items


def _fragment(order: int, text: str) -> SourceTextFragment:
    return SourceTextFragment(1, order, (10, 10 * order, 100, 10 * order + 8), [text])


def test_v3_round_trip_keeps_anchor_between_text_fragments():
    image = SourceAsset(1, "image", (10, 22, 100, 60), asset_hash="a" * 64)
    source = Chapter(None, -1, [TextFlowItem("body", 0, [_fragment(1, "before"), image, _fragment(2, "after")])])
    xml = encode(source)
    serialized = tostring(xml, encoding="unicode")
    assert "<flow>" in serialized and "<text role=\"body\"" in serialized
    assert serialized.index("before") < serialized.index("<asset") < serialized.index("after")
    restored = decode(xml).flow_items[0]
    assert isinstance(restored, TextFlowItem)
    assert [type(v) for v in restored.children] == [SourceTextFragment, SourceAsset, SourceTextFragment]


def test_formula_is_boundary_and_legacy_flat_body_migrates():
    legacy = fromstring("""<chapter><body>
      <paragraph ref="text"><block page_index="1" order="1" det="0,0,9,9">before</block></paragraph>
      <asset ref="equation" page_index="1" det="0,10,9,19"><content>x^2</content></asset>
      <paragraph ref="text"><block page_index="1" order="2" det="0,20,9,29">after</block></paragraph>
    </body></chapter>""")
    restored = decode(legacy)
    assert [type(v) for v in restored.flow_items] == [TextFlowItem, DisplayFormula, TextFlowItem]


def test_inline_formula_is_not_a_display_formula():
    source = Chapter(None, -1, [
        StandaloneAsset(SourceAsset(1, "image", (0, 0, 10, 10), asset_hash="b" * 64)),
        TextFlowItem("body", 0, [_fragment(1, "x"), SourceTextFragment(
            1, 2, (0, 11, 10, 20), [InlineExpression(ExpressionKind.INLINE_DOLLAR, "x^2")]
        )]),
    ])
    restored = decode(encode(source))
    assert isinstance(restored.flow_items[0], StandaloneAsset)
    assert isinstance(restored.flow_items[1], TextFlowItem)


def test_extraction_anchors_figure_but_never_joins_across_display_formula():
    first = TextFlowItem("body", 0, [_fragment(1, "a sentence continues")])
    second = TextFlowItem("body", 0, [_fragment(2, "with its ending.")])
    image = SourceAsset(1, "image", (0, 21, 10, 30), asset_hash="c" * 64)
    equation = SourceAsset(1, "equation", (0, 31, 10, 40), content=["x^2"])
    third = TextFlowItem("body", 0, [_fragment(3, "after formula")])

    result = list(_assemble_flow_items(iter([first, image, second, equation, third])))
    assert [type(item) for item in result] == [TextFlowItem, DisplayFormula, TextFlowItem]
    assert isinstance(result[0], TextFlowItem)
    assert [type(item) for item in result[0].children] == [SourceTextFragment, SourceAsset, SourceTextFragment]
