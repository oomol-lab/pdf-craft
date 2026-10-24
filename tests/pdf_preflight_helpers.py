"""Keep orchestration tests' fictional PDF sources outside real backend I/O.

Actual PDFs still run mandatory checks. Backend failures and gate ordering are
covered without this substitute in test_pdf_preflight.
"""
from pathlib import Path
from unittest.mock import patch

from pdf_craft.pdf.poppler import PageGeometry
from pdf_craft.pdf.preflight import PreflightResult, check_pdf


def mock_fictional_pdf_preflight(test):
    def check(source, aborted=lambda: False):
        source = Path(source)
        if source.is_file():
            with source.open('rb') as stream:
                if stream.read(5) == b'%PDF-':
                    return check_pdf(source, aborted)
        return PreflightResult({1: PageGeometry((0, 0, 100, 100), (0, 0, 100, 100), 0)}, {})
    substitution = patch('pdf_craft.pdf.preflight.check_pdf', side_effect=check)
    substitution.start()
    test.addCleanup(substitution.stop)
