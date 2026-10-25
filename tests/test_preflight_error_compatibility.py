"""Keep the published 2.6.2 error import and wire representation usable."""
import pickle
import unittest

from pdf_craft import NonContinuableError, PDFPreflightError
from pdf_craft.error import PDFPreflightError as ModuleError


class PreflightErrorCompatibilityTests(unittest.TestCase):
    def test_public_import_constructor_and_catch(self):
        self.assertIs(PDFPreflightError, ModuleError)
        for page in (None, 3):
            with self.subTest(page=page):
                error = PDFPreflightError('source.pdf', 'pypdf', 'page_read', page, 'invalid')
                with self.assertRaises(NonContinuableError) as raised:
                    raise error
                self.assertIs(raised.exception, error)
                self.assertEqual(
                    (error.source, error.backend, error.stage, error.page_index, error.reason),
                    ('source.pdf', 'pypdf', 'page_read', page, 'invalid'),
                )
                location = '' if page is None else ' on page 3'
                self.assertEqual(str(error), f'PDF preflight failed (pypdf/page_read){location}: invalid')

    def test_pickle_preserves_fields_and_original_cause(self):
        error = PDFPreflightError('source.pdf', 'poppler', 'render', 2, 'invalid')
        error.__cause__ = ValueError('backend failure')
        restored = pickle.loads(pickle.dumps(error))
        self.assertIs(type(restored), PDFPreflightError)
        self.assertEqual(
            (restored.source, restored.backend, restored.stage, restored.page_index, restored.reason),
            ('source.pdf', 'poppler', 'render', 2, 'invalid'),
        )
        self.assertEqual(str(restored), str(error))
        self.assertIsInstance(restored.__cause__, ValueError)
        self.assertEqual(str(restored.__cause__), 'backend failure')
