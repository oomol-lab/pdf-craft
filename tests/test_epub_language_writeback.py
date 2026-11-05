"""Strict XML round trips for EPUB language metadata without model requests."""
import tempfile
import unittest
from pathlib import Path
from xml.dom import minidom
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

from pdf_craft.pipeline.epub.adapter.metadata import read_metadata, write_metadata
from pdf_craft.pipeline.epub.adapter.zip import Zip

DC = 'http://purl.org/dc/elements/1.1/'
OPF = 'http://www.idpf.org/2007/opf'


class EpubLanguageWritebackTests(unittest.TestCase):
    def test_language_round_trip_preserves_metadata_and_namespace_semantics(self):
        for metadata in (
            '<metadata><d:title id="title">A title</d:title><d:creator>A writer</d:creator>'
            '<meta property="custom">Keep me</meta></metadata>',
            '<metadata><d:title>A title</d:title><d:language>en</d:language>'
            '<d:language>de</d:language></metadata>',
            '<metadata/>',
            '',
        ):
            for target in ('zh-CN', None):
                with self.subTest(metadata=metadata, target=target), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    source = root / 'source.epub'
                    package = (f'<package xmlns="{OPF}" xmlns:d="{DC}" version="3.0">'
                               f'{metadata}<manifest><item id="body" href="body.xhtml" '
                               'media-type="application/xhtml+xml"/></manifest><spine/></package>')
                    original = fromstring(package)
                    with ZipFile(source, 'w') as archive:
                        archive.writestr('META-INF/container.xml',
                            '<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
                        archive.writestr('content.opf', package)
                        archive.writestr('body.xhtml', '<html/>')
                    # A second write must remain valid and must update the inserted field.
                    for index in range(2):
                        language = 'fr' if index and target is not None else target
                        output = root / f'output-{index}.epub'
                        with Zip(source, output) as archive:
                            fields, context = read_metadata(archive)
                            write_metadata(archive, fields, context, target_language=language)
                        with ZipFile(output) as archive:
                            raw = archive.read('content.opf')
                            minidom.parseString(raw)
                            result = fromstring(raw)
                            self.assertEqual(archive.read('body.xhtml'), b'<html/>')
                        self.assertEqual(result.tag, f'{{{OPF}}}package')
                        for tag in ('title', 'creator'):
                            self.assertEqual(result.findtext(f'.//{{{DC}}}{tag}'),
                                             original.findtext(f'.//{{{DC}}}{tag}'))
                        self.assertEqual(result.findtext(f'.//{{{OPF}}}meta'),
                                         original.findtext(f'.//{{{OPF}}}meta'))
                        result_item = result.find(f'{{{OPF}}}manifest/{{{OPF}}}item')
                        original_item = original.find(f'{{{OPF}}}manifest/{{{OPF}}}item')
                        assert result_item is not None and original_item is not None
                        self.assertEqual(result_item.attrib, original_item.attrib)
                        languages = result.findall(f'.//{{{DC}}}language')
                        expected = original.findall(f'.//{{{DC}}}language')
                        self.assertEqual([node.text for node in languages],
                                         [language] * max(1, len(expected)) if language is not None
                                         else [node.text for node in expected])
                        source = output
