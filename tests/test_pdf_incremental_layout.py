"""Real Qt layout stays deterministic while page dependencies drain locally."""
# Each generator/mock is consumed before its loop iteration ends.
# pylint: disable=protected-access,cell-var-from-loop
from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from pdf_craft.pipeline.pdf import PDFReplacement, PDFReplacementRegion, PatchTextOptions, QTextParagraphFiller
from pdf_craft.pipeline.pdf.incremental_layout import plan_ready_pages
from pdf_craft.pipeline.pdf.text_layout import WindowedParagraphPlanner


class IncrementalLayoutTests(TestCase):
    def test_local_pages_match_batch_geometry_without_refitting_paragraphs(self):
        sizes: dict[int, tuple[float, float]] = {i: (200, 200) for i in range(1, 5)}
        values = []
        for slot in range(3):
            regions = (PDFReplacementRegion(slot+1, (10,110,180,160), (200,200), 72),
                       PDFReplacementRegion(slot+2, (10,20,180,70), (200,200), 72))
            values.append(PDFReplacement(slot+1, regions[0].bbox, 'A continuous paragraph. '*15,
                                         (200,200), 72, regions=regions))
        obstacles = tuple(region for value in values for region in value.source_regions())
        values = [replace(value, obstacle_regions=obstacles) for value in values]
        options = PatchTextOptions()
        baseline = WindowedParagraphPlanner(QTextParagraphFiller(options), sizes, options)._plan_window(1, 4, values)
        try:
            expected = {page: [placement for item in baseline.page_contributions(page)
                               for placement in item.placements] for page in sizes}
        finally:
            baseline.close()
        for order in ((0,1,2), (2,1,0)):
            with self.subTest(order=order):
                pages = []
                def items():
                    for slot in order:
                        if slot == order[-1]:
                            self.assertTrue(pages, 'ready pages waited for the whole connected chain')
                        yield slot, values[slot]
                feed = SimpleNamespace(spans=((0,1,2),(1,2,3),(2,3,4)), ready_items=items)
                filler = QTextParagraphFiller(options)
                with patch.object(filler, 'fit', wraps=filler.fit) as fit:
                    for plan in plan_ready_pages(feed, filler, sizes, options, iter,
                                                 lambda _values, error: self.fail(str(error))):
                        try:
                            page = plan.first_page_index
                            pages.append(page)
                            actual = [placement for item in plan.page_contributions(page) for placement in item.placements]
                            self.assertEqual(actual, expected[page])
                        finally:
                            plan.close()
                    self.assertEqual(fit.call_count, 3)
                self.assertEqual(sorted(pages), [1,2,3,4])

    def test_skipped_and_failed_slots_release_only_their_page_dependencies(self):
        sizes = {1: (200, 200)}
        value = PDFReplacement(1, (10,10,180,80), 'Text', (200,200), 72)
        for failing in (False, True):
            with self.subTest(failing=failing):
                feed = SimpleNamespace(spans=((0,1,1),(1,1,1)),
                                       ready_items=lambda: iter(((0, value if failing else None), (1, value))))
                filler = QTextParagraphFiller()
                original = filler.fit
                failures = []
                calls = 0
                def fit(*args):
                    nonlocal calls
                    calls += 1
                    if failing and calls == 1:
                        raise ValueError('first-pass failure')
                    return original(*args)
                with patch.object(filler, 'fit', side_effect=fit):
                    plans = list(plan_ready_pages(feed, filler, sizes, filler.options, iter,
                                                  lambda _values, error: failures.append(error)))
                try:
                    self.assertEqual(len(plans), 1)
                    self.assertEqual(len(failures), int(failing))
                finally:
                    for plan in plans:
                        plan.close()
