"""Independent extraction capabilities share one incremental refinement pipeline."""
# pylint: disable=protected-access,cell-var-from-loop
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

from pdf_craft import (
    AsyncPDFCraft, ConcurrentExecutor, ExtractionOptions, FixedCapacity, JEV, LLM,
    Refinement, RetryPolicy,
)
from pdf_craft.extractor.chapter.page_repair import repair_page_with_llm
from pdf_craft.extractor.chapter.primary import PrimaryPages, project_page
from pdf_craft.extractor.chapter.page_analysis import PageStructureError
from pdf_craft.jev import JEVRuntime
from pdf_craft.llm.guaranteed import GuaranteedBusinessError
from pdf_craft.pdf import decode
from pdf_craft.transform import PDFExtractionEngine
from tests.test_page_repair import _response_from_message
from tests.test_toc_identity import IdentityOCR, refinement


class RefinementConfigurationTests(TestCase):
    def test_empty_partial_and_invalid_configuration_is_rejected(self):
        with self.assertRaises(TypeError):
            # pylint: disable=no-value-for-parameter
            Refinement()  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            Refinement(jev=JEV('unused'))  # type: ignore[call-arg] # pylint: disable=no-value-for-parameter
        with self.assertRaises(ValueError):
            Refinement(None, None)  # type: ignore[arg-type]
        llm = LLM('unused', 'https://example.invalid', 'unused', 'o200k_base')
        executor = ConcurrentExecutor(FixedCapacity(1))
        for jev, model in ((None, llm), (JEV('unused'), None)):
            with self.subTest(jev=jev is not None), self.assertRaises(ValueError):
                Refinement(jev, model, executor=executor)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, 'JEV executor'):
            Refinement(JEV('unused'), llm, llm_executor=executor)
        with self.assertRaisesRegex(ValueError, 'LLM executor'):
            Refinement(JEV('unused'), llm, jev_executor=executor)
        with self.assertRaises(TypeError):
            ExtractionOptions(footnotes=None)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ExtractionOptions(refinement={})  # type: ignore[arg-type]


class RefinementModeTests(IsolatedAsyncioTestCase):
    async def test_four_modes_including_old_cache_keep_only_enabled_capabilities(self):
        for footnotes in (False, True):
            for refined in (False, True):
                for cached in (False, True):
                    with self.subTest(footnotes=footnotes, refined=refined, cached=cached):
                        await self._extract(footnotes, refined, cached)

    async def test_recovery_cannot_restore_footnotes_from_disabled_cache(self):
        for refined in (False, True):
            with self.subTest(refined=refined):
                await self._extract(False, refined, True, fail_commit=True)

    async def test_body_only_review_pass_avoids_llm_requests(self):
        await self._extract(False, True, True, pass_review=True)

    async def _extract(self, footnotes, refined, cached, fail_commit=False, pass_review=False):
        packets, prompts, bodies, notes, flags = [], [], [], [], []

        class OCR(IdentityOCR):
            def recognize(self, **kwargs):
                flags.append(kwargs['includes_footnotes'])
                # Deliberately retain old footnote evidence, even on a body-only
                # request. Cache projection must not rely on the OCR adapter.
                events = super().recognize(**{**kwargs, 'includes_footnotes': True})
                if cached:
                    for _ in events:
                        pass
                else:
                    yield from events

        async def evaluate(_runtime, _index, packet, **_kwargs):
            packets.append(packet)
            return 0.99 if pass_review else 0.1

        class Runtime:
            async def request(self, messages, **_kwargs):
                prompts.append(messages)
                result = _response_from_message(messages[1])
                if result['page_index'] == 1:
                    # A visible body change proves this is real refinement,
                    # not a no-op body-only route that merely reports calls.
                    result['layouts'][1].update(ownership='ignored', paragraph_role=None, paragraph_level=None)
                return json.dumps(result)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            engine = PDFExtractionEngine('ignored')
            engine._ocr = cast(Any, OCR())
            commit = engine._commit_extraction
            commits = []
            async def commit_with_failure(*args, **kwargs):
                commits.append(kwargs)
                if fail_commit and len(commits) == 1:
                    raise PageStructureError('forced recovery boundary', 1)
                return await commit(*args, **kwargs)
            engine._commit_extraction = commit_with_failure
            with patch.object(JEVRuntime, 'evaluate', evaluate), patch(
                    'pdf_craft.transform.runtime_for', return_value=Runtime()) as runtime:
                await AsyncPDFCraft.from_engine(engine).extract_pdf(
                    root/'source.pdf', root/'book.pcex', ExtractionOptions(
                        includes_furniture=False, footnotes=footnotes,
                        refinement=refinement() if refined else None,
                        on_body=bodies.append, on_footnote=notes.append),
                    analysing_path=root/'analysis', error_policy=fail_commit)
                self.assertEqual(runtime.call_count, int(refined))
            self.assertEqual(flags, [footnotes])
            self.assertEqual(len(packets), 6 if refined else 0)
            self.assertEqual(len(prompts), 6 if refined and not pass_review else 0)
            self.assertEqual(len(notes), 6 if footnotes else 0)
            self.assertTrue(bodies)
            with ZipFile(root/'book.pcex') as archive:
                chapters = [fromstring(archive.read(name)) for name in archive.namelist()
                            if name.startswith('chapters/') and name.endswith('.xml')]
            content = ''.join(''.join(chapter.itertext()) for chapter in chapters)
            self.assertEqual('Note.' in content, footnotes)
            self.assertEqual('Second chapter' in content, (not refined or pass_review) and not fail_commit)
            if fail_commit:
                self.assertEqual(len(commits), 2)
            self.assertIn('Note.', (root/'analysis/ocr/page_1.xml').read_text(), 'do not rewrite raw cache')
            if not footnotes:
                self.assertFalse(any(list(chapter.iter('ref')) for chapter in chapters))
                for packet in packets:
                    state = packet['state']
                    target = state['target_page']
                    self.assertEqual(target['ocr_footnote_layout_count'], 0)
                    self.assertEqual(target['citations'], [])
                    for name in ('previous_page', 'next_page'):
                        neighbor = state[name]
                        if neighbor is not None:
                            self.assertEqual(neighbor['citation_boundary'], [])
                    instructions = packet['questions']['page_passes_strict_standard']['instructions']
                    self.assertNotIn('citation_standard', instructions)
                    self.assertNotIn('reference_standard', instructions)
                for messages in prompts:
                    self.assertIn('本次关闭脚注', messages[0].message)
                    self.assertNotIn('ref 规则：', messages[0].message)
                    self.assertEqual(json.loads(messages[1].message)['target_page']['citations'], [])

    async def test_body_only_candidate_cannot_reintroduce_notes_or_references(self):
        raw = decode(fromstring('<page index="1"><body><layout ref="text" det="0,0,100,20">'
                                'Body.</layout></body><footnotes/></page>'))
        page = PrimaryPages().append(project_page(raw, False))
        for kind in ('citation', 'reference', 'ownership', 'citation_id', 'citation_index'):
            with self.subTest(kind=kind):
                async def request(messages, _index, _maximum):
                    result = _response_from_message(messages[1])
                    if kind == 'citation':
                        result['citations'] = [dict(citation_id='new-1-1', index=1, mark='1', stream_order=0)]
                    elif kind == 'reference':
                        result['references'] = [dict(layout_id=result['layouts'][0]['layout_id'], citation_index=1,
                                                    anchor=dict(before='', mark='B', after='ody.'))]
                    elif kind == 'ownership':
                        result['layouts'][0]['ownership'] = 'citation'
                    elif kind == 'citation_id':
                        result['layouts'][0]['citation_id'] = 'new-1-1'
                    else:
                        result['layouts'][0]['citation_index'] = 1
                    return json.dumps(result)
                with self.assertRaises(GuaranteedBusinessError) as caught:
                    await repair_page_with_llm(
                        previous_page=None, target_page=page, next_page=None,
                        previous_pass_probability=None, next_pass_probability=None,
                        includes_footnotes=False, request=request,
                        retry_policy=RetryPolicy(analysis=0), error_policy=False)
                self.assertIn("FOOTNOTES_DISABLED", str(caught.exception.__cause__))
                self.assertEqual(page.citations, [])
                self.assertEqual(page.layouts[0].ownership, 'paragraph')
