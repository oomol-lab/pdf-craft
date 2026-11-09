"""EPUB archive finalization preserves original resource identities."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from pdf_craft.pipeline.epub.adapter.zip import Zip


class EpubZipTests(unittest.TestCase):
    def test_finalize_preserves_resource_names_content_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'source.epub', root / 'target.epub'
            resources = {
                './OEBPS/override_v1.css': b'body { color: black; }',
                'OEBPS/override_v1.css': b'body { color: blue; }',
                'OEBPS//images/cover.png': b'cover image',
                'OEBPS/body.xhtml': b'<html/>',
            }
            with ZipFile(source, 'w') as archive:
                archive.writestr('OEBPS/', b'')
                for index, (name, content) in enumerate(resources.items()):
                    info = ZipInfo(name, date_time=(2026, 11, 8, 3, 24, 0))
                    info.compress_type = ZIP_DEFLATED if index % 2 else ZIP_STORED
                    info.comment = b'resource comment'
                    info.extra = b'\xfe\xca\x02\x00ab'
                    info.external_attr = 0o100644 << 16
                    archive.writestr(info, content)

            with Zip(source, target):
                pass

            with ZipFile(source) as original, ZipFile(target) as result:
                self.assertIsNone(result.testzip())
                self.assertEqual(result.namelist(), list(resources))
                for name, content in resources.items():
                    self.assertEqual(result.read(name), content)
                    before, after = original.getinfo(name), result.getinfo(name)
                    for attribute in ('date_time', 'compress_type', 'comment', 'extra', 'external_attr'):
                        self.assertEqual(getattr(after, attribute), getattr(before, attribute))

    def test_explicit_migration_and_replacement_are_not_copied_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'source.epub', root / 'target.epub'
            with ZipFile(source, 'w') as archive:
                archive.writestr('mimetype', b'application/epub+zip', compress_type=ZIP_STORED)
                archive.writestr('./OEBPS/body.xhtml', b'old body')
                archive.writestr('OEBPS/content.opf', b'old metadata')
                archive.writestr('./OEBPS/override_v1.css', b'original style')

            with Zip(source, target) as archive:
                archive.migrate(Path('mimetype'))
                for name in ('OEBPS/body.xhtml', 'OEBPS/content.opf'):
                    with archive.replace(Path(name)) as output:
                        output.write(b'replacement')

            with ZipFile(target) as result:
                self.assertIsNone(result.testzip())
                self.assertEqual(result.namelist(), [
                    'mimetype', 'OEBPS/body.xhtml', 'OEBPS/content.opf', './OEBPS/override_v1.css',
                ])
                self.assertEqual(result.getinfo('mimetype').compress_type, ZIP_STORED)
                self.assertEqual(result.read('mimetype'), b'application/epub+zip')
                self.assertEqual(result.read('OEBPS/body.xhtml'), b'replacement')
                self.assertEqual(result.read('OEBPS/content.opf'), b'replacement')
                self.assertEqual(result.read('./OEBPS/override_v1.css'), b'original style')

    def test_failed_finalization_keeps_existing_target_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'source.epub', root / 'target.epub'
            with ZipFile(source, 'w') as archive:
                archive.writestr('./OEBPS/style.css', b'original style')
            target.write_bytes(b'existing target')
            with self.assertRaisesRegex(OSError, 'copy failed'):
                with patch.object(ZipFile, 'open', side_effect=OSError('copy failed')):
                    with Zip(source, target):
                        pass
            self.assertEqual(target.read_bytes(), b'existing target')
            self.assertEqual(set(root.iterdir()), {source, target})
