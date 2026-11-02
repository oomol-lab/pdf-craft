"""Flow packaging must not undo the joiner's observed paragraph boundaries."""
from copy import deepcopy
import unittest
from pdf_craft.pdf import Page, PageLayout
from pdf_craft.extractor.chapter.primary import project_window
from pdf_craft.extractor.chapter.jointer import Jointer
from pdf_craft.extractor.chapter.generation import _assemble_flow_items
from pdf_craft.extractor.chapter.chapter import TextFlowItem, StandaloneAsset


def table_page():
    return Page(1, None, [
        PageLayout('text', (180, 100, 700, 150), 'Tabulate these values:', 0, None),
        PageLayout('table', (180, 230, 700, 350), '<table><tr><td>A</td></tr></table>', 1, 'fixture'),
        PageLayout('text', (180, 430, 700, 480), 'Plot these values as a new test.', 2, None),
        PageLayout('text', (100, 500, 900, 750), 'A complete wider paragraph establishes the column margin. ' * 4, 3, None),
    ], [], 0, 0)


class AssetParagraphDecisionTests(unittest.TestCase):
    def test_indented_post_table_line_is_not_rejoined_by_flow_packaging(self):
        raw = table_page()
        before = deepcopy(raw)
        page = project_window([raw], True)[0]
        assert page is not None
        text = next(l for l in page.layouts if l.source_order == 2)
        self.assertFalse(text.continues_from_previous)
        self.assertEqual(raw, before)

    def test_joiner_owns_embedding_while_packager_only_preserves_its_output(self):
        raw = table_page()
        joined = list(Jointer([(1, raw.body_layouts)]).execute())
        self.assertEqual([type(v).__name__ for v in joined],
                         ['TextFlowItem', 'SourceAsset', 'TextFlowItem', 'TextFlowItem'])
        flow = list(_assemble_flow_items(joined))
        self.assertIsInstance(flow[1], StandaloneAsset)
        self.assertEqual(len(flow), 4)

    def test_unfinished_prose_can_still_embed_an_image_without_waiting_for_another_page(self):
        layouts = [PageLayout('text', (100, 100, 900, 200), 'A sentence continues with', 0, None),
                   PageLayout('image', (100, 230, 900, 400), '', 1, 'fixture'),
                   PageLayout('text', (100, 430, 900, 530), 'its actual ending.', 2, None)]
        flow = list(_assemble_flow_items(Jointer([(1, layouts)]).execute()))
        self.assertEqual(len(flow), 1)
        self.assertIsInstance(flow[0], TextFlowItem)
        assert isinstance(flow[0], TextFlowItem)
        self.assertEqual(len(flow[0].children), 3)

    def test_packaging_cannot_reinfer_a_deliberate_split(self):
        from pdf_craft.extractor.chapter.chapter import SourceTextFragment, SourceAsset
        before = TextFlowItem('body', -1, [SourceTextFragment(1, 0, (0, 0, 100, 20), ['unfinished'])])
        after = TextFlowItem('body', -1, [SourceTextFragment(1, 2, (0, 60, 100, 80), ['but independently adopted'])])
        asset = SourceAsset(1, 'image', (0, 25, 100, 55), asset_hash='fixture')
        result = list(_assemble_flow_items([before, asset, after]))
        self.assertEqual(len(result), 3)
        self.assertIs(result[0], before)
        self.assertIs(result[2], after)
