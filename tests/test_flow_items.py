from xml.etree.ElementTree import fromstring, tostring
from inspect import signature
from typing import cast
import pytest

import pdf_craft.extractor.chapter as chapter_module
from pdf_craft.extractor.chapter import (
    Chapter, DisplayFormula, FlowAssetRef, InlineExpression, SourceAsset, SourceTextFragment,
    StandaloneAsset, TextFlowItem, Reference, decode, encode, search_references_in_chapter,
)
from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.generation import _assemble_flow_items
from pdf_craft.pipeline.pdf.pipeline import _chapter_obstacle_regions
from pdf_craft.extractor.chapter.reference import References
from pdf_craft.extractor.chapter.mark import transform2mark


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


def test_nested_asset_caption_reference_is_written_and_restored():
    reference = Reference(1, 7, "①", [TextFlowItem("body", 0, [_fragment(8, "footnote")])])
    image = SourceAsset(1, "image", (10, 22, 100, 60), caption=["caption ", reference], asset_hash="a" * 64)
    chapter = Chapter(None, -1, [TextFlowItem("body", 0, [_fragment(1, "before"), image])])
    restored = decode(encode(chapter))
    restored_reference = next(search_references_in_chapter(restored))
    assert restored_reference.id == (1, 7)


def test_formula_is_boundary_and_legacy_flat_body_migrates():
    legacy = fromstring("""<chapter><body>
      <paragraph ref="text"><block page_index="1" order="1" det="0,0,9,9">before</block></paragraph>
      <asset ref="formula" page_index="1" det="0,10,9,19"><content>x^2</content></asset>
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


def test_text_flow_item_rejects_formula_child():
    equation = SourceAsset(1, "formula", (0, 0, 10, 10), content=["x^2"])
    with pytest.raises(ValueError, match="DisplayFormula"):
        TextFlowItem("body", 0, [_fragment(1, "before"), equation])


def test_source_asset_flow_positions_reject_unknown_or_formula_children():
    with pytest.raises(ValueError, match="ref"):
        SourceAsset(1, cast(FlowAssetRef, "other"), (0, 0, 10, 10))
    formula = SourceAsset(1, "formula", (0, 0, 10, 10), content=["x"])
    with pytest.raises(ValueError, match="image/table"):
        StandaloneAsset(formula)
    with pytest.raises(ValueError, match="image/table"):
        TextFlowItem("body", 0, [_fragment(1, "body"), formula])


def test_v3_public_model_exposes_only_flow_names_and_fields():
    assert not hasattr(chapter_module, "ParagraphLayout")
    assert not hasattr(chapter_module, "BlockLayout")
    assert not hasattr(chapter_module, "AssetLayout")
    assert "order" not in signature(SourceTextFragment).parameters
    assert "det" not in signature(SourceTextFragment).parameters
    assert "det" not in signature(SourceAsset).parameters
    assert "hash" not in signature(SourceAsset).parameters


def test_v3_codec_rejects_legacy_body_and_invalid_role_when_strict():
    with pytest.raises(ValueError, match="role"):
        TextFlowItem("unsupported", 0, [])
    legacy = fromstring("<chapter><body><paragraph ref='text'/></body></chapter>")
    with pytest.raises(ValueError, match="PCEX v3"):
        decode(legacy, allow_legacy=False)


def test_v3_codec_rejects_legacy_reference_subtrees_when_strict():
    legacy_asset = fromstring("""<chapter><flow/><references><ref id="1-1"><mark>①</mark><flow>
      <display-formula><asset ref="equation" page_index="1" bbox="0,0,1,1"/></display-formula>
    </flow></ref></references></chapter>""")
    with pytest.raises(ValueError, match="formula"):
        decode(legacy_asset, allow_legacy=False)
    legacy_body = fromstring("""<chapter><flow/><references><ref id="1-1"><mark>①</mark>
      <body><paragraph role="body"/></body>
    </ref></references></chapter>""")
    with pytest.raises(ValueError, match="unsupported children"):
        decode(legacy_body, allow_legacy=False)


def test_v3_codec_rejects_legacy_asset_attributes_when_strict():
    legacy_attributes = fromstring("""<chapter><flow><standalone-asset>
      <asset ref="image" page_index="1" det="0,0,1,1" hash="a"/>
    </standalone-asset></flow></chapter>""")
    with pytest.raises(ValueError, match="unsupported attributes"):
        decode(legacy_attributes, allow_legacy=False)


@pytest.mark.parametrize("source", [
    """<chapter unexpected="x"><flow><text role="body">
      <fragment page_index="1" source_order="2" bbox="0,0,1,1">x</fragment>
    </text></flow></chapter>""",
    """<chapter><flow unexpected="x"><text role="body">
      <fragment page_index="1" source_order="2" bbox="0,0,1,1">x</fragment>
    </text></flow></chapter>""",
    """<chapter><flow><text role="body" extra="x">
      <fragment page_index="1" source_order="2" bbox="0,0,1,1">x</fragment>
    </text></flow></chapter>""",
    """<chapter><flow><text role="body">
      <fragment page_index="1" source_order="2" order="9" bbox="0,0,1,1" det="0,0,9,9">x</fragment>
    </text></flow></chapter>""",
])
def test_v3_codec_rejects_unknown_and_legacy_fragment_attributes(source):
    with pytest.raises(ValueError, match="unsupported attributes"):
        decode(fromstring(source), allow_legacy=False)


def test_v3_codec_validates_reference_flow_attributes_when_strict():
    source = fromstring("""<chapter><flow/><references><ref id="1-1"><mark>①</mark>
      <flow unexpected="x"/>
    </ref></references></chapter>""")
    with pytest.raises(ValueError, match="unsupported attributes"):
        decode(source, allow_legacy=False)


def test_v3_codec_rejects_text_level_inline_expression():
    source = fromstring("""<chapter><flow><text role="body">
      <fragment page_index="1" source_order="0" bbox="0,0,10,10">before</fragment>
      <inline_expr kind="inline_dollar">x</inline_expr>
    </text></flow></chapter>""")
    with pytest.raises(ValueError, match="unknown element: <inline_expr>"):
        decode(source, allow_legacy=False)


def test_pdf_obstacles_include_asset_nested_in_text_flow_item():
    image = SourceAsset(1, "image", (11, 22, 77, 88), asset_hash="d" * 64)
    chapter = Chapter(None, -1, [TextFlowItem("body", 0, [_fragment(1, "before"), image])])
    obstacles = _chapter_obstacle_regions(chapter, {1: (100, 100)}, 300)
    assert [(item.page_index, item.bbox) for item in obstacles] == [(1, image.bbox)]


def test_reference_assembly_writes_real_flow_items_not_legacy_projection():
    paragraph = TextFlowItem("body", 0, [SourceTextFragment(
        1, 0, (0, 0, 10, 10), ["① Footnote body"],
    )])
    mark = transform2mark("①")
    assert mark is not None
    reference = References(1, [paragraph]).get(mark)
    assert reference is not None
    assert len(reference.flow_items) == 1
    assert isinstance(reference.flow_items[0], TextFlowItem)
    assert reference.flow_items[0].children[0].content == ["Footnote body"]


def test_children_are_mutated_for_cross_page_fragment_aggregation():
    paragraph = TextFlowItem("body", 0, [_fragment(1, "first")])
    paragraph.children.extend([_fragment(2, "second")])
    assert [fragment.source_order for fragment in paragraph.children if isinstance(fragment, SourceTextFragment)] == [1, 2]


def test_extraction_anchors_figure_but_never_joins_across_display_formula():
    first = TextFlowItem("body", 0, [_fragment(1, "a sentence continues")])
    second = TextFlowItem("body", 0, [_fragment(2, "with its ending.")])
    image = SourceAsset(1, "image", (0, 21, 10, 30), asset_hash="c" * 64)
    equation = SourceAsset(1, "formula", (0, 31, 10, 40), content=["x^2"])
    third = TextFlowItem("body", 0, [_fragment(3, "after formula")])

    result = list(_assemble_flow_items(iter([first, image, second, equation, third])))
    assert [type(item) for item in result] == [TextFlowItem, DisplayFormula, TextFlowItem]
    assert isinstance(result[0], TextFlowItem)
    assert [type(item) for item in result[0].children] == [SourceTextFragment, SourceAsset, SourceTextFragment]
