"""Marginal folios are reversible exclusions, never deleted OCR observations."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
from copy import deepcopy
from xml.etree.ElementTree import fromstring

from pdf_craft.pdf import decode
from pdf_craft.extractor.refinement import _project_window
from pdf_craft.extractor.chapter.stream import resolve_to_disk


def raw_page(*, number='28', box='10,910,50,940', body='A reading passage about a journey.', extra=''):
    return decode(fromstring('<page index="1"><body>'
        f'<layout ref="text" det="100,100,900,880">{body}</layout>{extra}</body><footnotes>'
        f'<layout ref="text" det="{box}">{number}</layout></footnotes></page>'))


class MarginalNoteNumberTests(unittest.TestCase):
    def test_centered_folio_needs_an_isolated_single_numeric_line(self):
        raw = raw_page(number='28', box='480,970,520,990')
        page = _project_window([None, raw, None], True)[1]
        assert page is not None
        self.assertFalse(page.citations)
        self.assertEqual(page.layouts[-1].ownership, 'ignored')
        self.assertEqual(raw.footnotes_layouts[0].text, '28')

    def test_center_alignment_alone_cannot_exclude_notes_or_wide_ocr_frames(self):
        for box, body in (('480,900,520,920', 'A reading passage.'),
                          ('600,970,640,990', 'A reading passage.'),
                          ('0,950,1000,990', 'A reading passage.'),
                          ('480,970,520,990', 'A statement²⁸.')):
            with self.subTest(box=box, body=body):
                page = _project_window([None, raw_page(number='28', box=box, body=body), None], True)[1]
                assert page is not None
                self.assertTrue(page.citations)

    def test_measured_multiline_prose_supports_a_short_reading_band(self):
        raw = raw_page(number='8', body='A complete reading passage ends here.')
        raw.body_layouts[0].det = (100, 100, 900, 240)
        raw.footnotes_layouts[0].det = (10, 900, 50, 940)
        unknown = _project_window([None, raw, None], True)[1]
        assert unknown is not None
        self.assertTrue(unknown.citations)
        raw.body_layouts[0].first_line_inset = 0.0
        observed = _project_window([None, raw, None], True)[1]
        assert observed is not None
        self.assertFalse(observed.citations)
        self.assertEqual(observed.layouts[-1].ownership, 'ignored')

    def test_chinese_side_folios_need_dominant_observed_vertical_columns(self):
        for vertical in (False, True):
            with self.subTest(vertical=vertical):
                raw = decode(fromstring('<page index="1"><body>' + ''.join(
                    f'<layout ref="text" det="{x},100,{x+50},900" '
                    f'vertical_columns="{str(vertical).lower()}">这是一段竖排正文，沿着文字列阅读。</layout>'
                    for x in (100, 200, 300)) + '</body><footnotes>'
                    '<layout ref="text" det="350,600,380,640">二五</layout>'
                    '</footnotes></page>'))
                page = _project_window([None, raw, None], True)[1]
                assert page is not None
                candidate = next(item for item in page.layouts if item.source_order == 3)
                self.assertEqual(candidate.ownership, 'ignored' if vertical else 'citation')

    def test_a_vertical_sidebar_does_not_make_horizontal_body_vertical(self):
        raw = raw_page(number='二五', box='910,500,940,540',
            extra='<layout ref="text" det="40,100,70,300" vertical_columns="true">侧栏。</layout>')
        page = _project_window([None, raw, None], True)[1]
        assert page is not None
        self.assertTrue(page.citations)

    def test_outside_both_reading_axes_is_retained_as_an_ignored_source(self):
        raw = raw_page()
        before = deepcopy(raw)
        page = _project_window([None, raw, None], True)[1]
        assert page is not None
        self.assertEqual(len(page.layouts), 2)
        excluded = next(item for item in page.layouts if item.source_order == 1)
        self.assertEqual(excluded.ownership, 'ignored')
        assert excluded.source.origin is not None
        self.assertEqual(excluded.source.origin.text, '28')
        self.assertEqual(excluded.source.bbox, (10, 910, 50, 940))
        self.assertFalse(page.citations)
        self.assertEqual(raw, before)

    def test_position_alone_cannot_remove_reading_content(self):
        for kwargs in (dict(number='28 A real note.'), dict(number='28', box='100,910,150,940'),
                       dict(number='28', box='10,800,50,830'), dict(number='①'),
                       dict(body='The reference is 28.'), dict(body='A statement²⁸.'),
                       dict(body='A statement㉘.'), dict(number='2024'),
                       dict(body='A statement&lt;sup&gt;28&lt;/sup&gt;.')):
            with self.subTest(kwargs=kwargs):
                page = _project_window([None, raw_page(**kwargs), None], True)[1]
                assert page is not None
                self.assertEqual(next(item for item in page.layouts if item.source_order == 1).ownership, 'citation')

    def test_asset_labels_and_sparse_title_pages_are_not_folio_evidence(self):
        for raw in (raw_page(extra='<layout ref="image" det="0,900,100,970" hash="fixture"/>'),
                    raw_page(body='A title.', box='10,200,50,230')):
            page = _project_window([None, raw, None], True)[1]
            assert page is not None
            self.assertTrue(any(item.ownership == 'citation' for item in page.layouts))

    def test_long_numeric_noise_is_not_treated_as_a_short_folio(self):
        page = _project_window([None, raw_page(number='123456789'), None], True)[1]
        assert page is not None
        self.assertTrue(page.citations)

    def test_real_note_continuation_bypasses_folios_without_renumbering_sources(self):
        pages = []
        for index, note in ((1, '① The note continues'), (2, 'into this page.')):
            pages.append(decode(fromstring(f'<page index="{index}"><body>'
                '<layout ref="text" det="100,100,900,700">A reading passage①.</layout>'
                '</body><footnotes>'
                f'<layout ref="text" det="100,750,900,850">{note}</layout>'
                f'<layout ref="text" det="10,910,50,940">{27+index}</layout>'
                '</footnotes></page>')))
        first, second, _ = _project_window([*pages, None], True)
        assert first is not None and second is not None
        self.assertEqual(first.citations[0].index, 1)
        self.assertEqual(second.citations[0].citation_id, first.citations[0].citation_id)
        self.assertIsNone(second.citations[0].index)
        for page in (first, second):
            self.assertEqual([item.source_order for item in page.layouts], [0, 1, 2])
            self.assertEqual(page.layouts[2].ownership, 'ignored')
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'stream.sqlite'
            notes = []
            resolve_to_disk(lambda: iter(pages), database, on_footnote=notes.append)
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(connection.execute('SELECT page,number FROM refs').fetchall(), [(1, 1)])
            finally:
                connection.close()
            self.assertEqual(len(notes), 1)
            text = ''.join(notes[0].itertext())
            self.assertIn('into this page.', text)
            self.assertNotIn('28', text)
            self.assertNotIn('29', text)

    def test_local_window_keeps_a_known_origin_without_needing_a_folio(self):
        pages = []
        for index, note in ((1, '① The note continues'), (2, 'into this page.')):
            pages.append(decode(fromstring(f'<page index="{index}"><body>'
                '<layout ref="text" det="100,100,900,700">A reading passage①.</layout>'
                '</body><footnotes>'
                f'<layout ref="text" det="100,750,900,850">{note}</layout>'
                '</footnotes></page>')))
        first, second, _ = _project_window([*pages, None], True)
        assert first is not None and second is not None
        self.assertEqual(second.citations[0].origin_page_index, 1)
        self.assertEqual(second.citations[0].citation_id, first.citations[0].citation_id)
