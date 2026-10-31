"""Opt-in replay of locally retained private evidence; no OCR or model calls.

Set PDF_CRAFT_FILL_REPLAY to an evidence directory containing initial-state/
plus run-20261031-094432/. Private documents never become repository fixtures.
"""
# pylint: disable=protected-access
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unicodedata
from unittest import TestCase, skipUnless
from unittest.mock import AsyncMock

from pdf_craft import RetryPolicy
from pdf_craft.common import read_xml, save_xml
from pdf_craft.extractor.chapter.chapter import decode, encode
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from tests.test_anchored_content_translation import _repairing_translator
from tests.test_fill_acceptance_regressions import fragments, visible

_CHECKSUMS = {
    'initial-state/initial-request.json': '21319703fa4bbdcf86bd1f3941ccecd4221b2f043531fac3f8b50794a982dfae',
    'initial-state/source-unit.xml': '9ff987b53f34021225189ef4e1b13177440a4d448d67cc10f4e33228f6981eee',
    'initial-state/canonical-translation.txt': 'e74b88d43a7c324250edfa69dc86cc3f6f814c2e1e43c27ae8df2c2cb37d23a4',
    'run-20261031-094432/attempt-03.json': '603ab5e02ebd4ae939bf4478917d8d4556cc6116dee707cee1039ffa47beae3b',
}


@skipUnless(os.environ.get('PDF_CRAFT_FILL_REPLAY'), 'private replay not requested')
class RecordedFillReplay(TestCase):
    def test_recorded_responses_pass_actual_fill_and_persistence(self):
        base = Path(os.environ['PDF_CRAFT_FILL_REPLAY'])
        for name, digest in _CHECKSUMS.items():
            self.assertEqual(hashlib.sha256((base/name).read_bytes()).hexdigest(), digest)
        canonical = (base/'initial-state/canonical-translation.txt').read_text()
        initial = json.loads((base/'initial-state/initial-request.json').read_text())
        responses = [json.loads((base/f'run-20261031-094432/attempt-{index:02d}.json').read_text())['response']
                     for index in (1, 2, 3)]
        for sequence in ([responses[2]], responses):
            source = decode(read_xml(base/'initial-state/source-unit.xml'))
            expected_regions = [(f.page_index, f.source_order, f.bbox) for f in fragments(source)]
            translator, runtime = _repairing_translator(sequence)
            translator._retry_policy = RetryPolicy(fill=12)
            translator._fill_llm.token_encoding = 'o200k_base'
            translator._max_group_score = 2600
            translator._max_fill_displaying_errors = 10
            translator._translate_text_async = AsyncMock(return_value=canonical)
            target = asyncio.run(NarrativeXMLTransformer(translator).transform(source, error_policy=False))
            count = runtime.context_value.calls
            self.assertEqual(count, 1 if len(sequence) == 1 else 3)
            messages = runtime.context_value.messages[0][0]
            self.assertEqual([{'role':m.role.name.lower(), 'content':m.message} for m in messages], initial['messages'])
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'chapter.xml'
                save_xml(encode(target), path)
                restored = decode(read_xml(path))
            actual = visible(restored)
            self.assertEqual(unicodedata.normalize('NFC', actual), unicodedata.normalize('NFC', canonical))
            self.assertEqual([(f.page_index, f.source_order, f.bbox) for f in fragments(restored)], expected_regions)
