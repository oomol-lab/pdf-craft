"""Physical request gates through production PCEX and EPUB entry points."""
# pylint: disable=protected-access
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch
from pdf_craft.llm.runtime import LLMRuntime
from tests.test_epub_optional_cfi import book
from pdf_craft.document.stream_view import content_view
from pdf_craft._policy import policy_operation
from xml.etree.ElementTree import fromstring, tostring
from zipfile import ZipFile

from pdf_craft import AsyncPDFCraft, SubmitKind
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import Chapter, encode
from tests.test_pdf_craft_extraction import make_extraction
from tests.translation_model import paragraph, translator_fixture


def assert_free_artist_prompts(test, model):
    artists = [call for call in model.wire_calls if call[0].message.startswith('You are a translator.')]
    test.assertTrue(artists)
    for call in artists:
        prompt = '\n'.join(message.message for message in call)
        for forbidden in ('source-math', 'JSON', 'placeholder', '"messages"', '"id"'):
            test.assertNotIn(forbidden, prompt)


class TranslationGroupingTests(IsolatedAsyncioTestCase):
    async def test_pcex_production_fills_groups_without_readiness_barriers(self):
        for width in (24, 4, 1):
            with self.subTest(width=width), TemporaryDirectory() as directory:
                root = Path(directory)
                source = make_extraction(root / 'source', page_pixel_sizes={i: (100, 100) for i in range(1, 25)})
                for path in (root / 'source/chapters').glob('*.xml'):
                    path.unlink()
                for start in range(0, 24, width):
                    save_xml(encode(Chapter(start + 1, 1, [paragraph(i + 1) for i in range(start, start + width)])),
                             root / f'source/chapters/chapter_{start + 1}.xml')
                translator, model = translator_fixture(max_group_score=2600)
                answer = model.answer
                model.answer = lambda messages, source_answer=answer: source_answer(messages).replace('Paragraph', 'Translated')
                result = await asyncio.wait_for(AsyncPDFCraft().translate_extraction(
                    source, root / 'translated.pcex', translator, target_language='English', with_anchored=False), 20)
                self.assertLessEqual(len(model.wire_calls), 4, [len(batch) for batch in model.calls])
                assert_free_artist_prompts(self, model)
                self.assertTrue(any(message.role.name == 'USER' and message.message.count('Paragraph') > 4
                                    for call in model.wire_calls for message in call))
                with result._materialize() as paths:
                    assert paths.translations is not None
                    entries = list(paths.translations.glob('*/coverage.xml'))
                    coverage = fromstring(entries[0].read_text()).findall('./narrative/paragraph')
                    self.assertEqual(len(coverage), 24)
                    self.assertTrue(all(entry.get('state') == 'translated' for entry in coverage))
                    translated = list(paths.translations.glob('*/chapters/*.xml'))
                    self.assertEqual(len(translated), 24 // width)
                    contents = ''.join(path.read_text() for path in translated)
                    for index in range(1, 25):
                        self.assertIn(f'Translated {index}.', contents)
                    self.assertNotIn('Paragraph', contents)


    async def test_epub_production_fills_groups_across_chapter_roots(self):
        for width in (24, 4, 1):
            with self.subTest(width=width), TemporaryDirectory() as directory:
                root = Path(directory)
                documents = []
                for start in range(0, 24, width):
                    body = ''.join(f'<p>Paragraph {i}.</p>' for i in range(start, start + width))
                    documents.append((f'c{start}.xhtml', 'application/xhtml+xml',
                        '<html xmlns="http://www.w3.org/1999/xhtml"><body>' + body + '</body></html>'))
                book(root / 'source.epub', documents)
                translator, model = translator_fixture(max_group_score=2600)
                answer = model.answer
                model.answer = lambda messages, source_answer=answer: source_answer(messages).replace('Paragraph', 'Translated')
                received = []
                async def invoke(_runtime, messages, *args, provider=model, **kwargs):
                    return await provider.invoke(messages, *args, **kwargs)
                with patch.object(LLMRuntime, '_invoke_async', invoke):
                    await AsyncPDFCraft().translate_epub(root / 'source.epub', root / 'out.epub',
                        llm=translator._translation_llm, target_language='English', window=1,
                        max_group_tokens=2600, submit=SubmitKind.REPLACE, executor=translator._translation_runtime.executor, on_body=received.append)
                self.assertLessEqual(len(model.wire_calls), 6, [len(batch) for batch in model.calls])
                assert_free_artist_prompts(self, model)
                self.assertTrue(any(message.role.name == 'USER' and message.message.count('Paragraph') > 4
                                    for call in model.wire_calls for message in call))
                self.assertEqual([value.original for value in received], [f'Paragraph {i}.' for i in range(24)])
                self.assertEqual([value.translated for value in received], [f'Translated {i}.' for i in range(24)])
                self.assertTrue(all(value.epubcfi is not None for value in received))
                with ZipFile(root / 'out.epub') as archive:
                    for start in range(0, 24, width):
                        document = fromstring(archive.read(f'EPUB/c{start}.xhtml'))
                        paragraphs = document.findall('.//{http://www.w3.org/1999/xhtml}p')
                        self.assertEqual([''.join(node.itertext()) for node in paragraphs],
                                         [f'Translated {i}.' for i in range(start, start + width)])

    @skipUnless(Path('/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/pdf-craft-output/issue-932-1bc9aef5-investigation/source.pcex').exists(),
                'Private production fixture is available only in the investigation workspace')
    @policy_operation
    async def test_private_production_case_includes_metadata_and_furniture(self):
        source = Path('/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/pdf-craft-output/issue-932-1bc9aef5-investigation/source.pcex')
        translator, model = translator_fixture(max_group_score=2600, encoding='o200k_base')
        # Exercise production deterministic adoption for unchanged prose and
        # furniture too; formula-bearing groups still require real XML fill.
        translator._direct_fill_candidates = type(translator)._direct_fill_candidates
        original_answer = model.answer
        def answer(messages):
            value = original_answer(messages)
            if value.startswith('<xml') and '<formula_context' in value:
                element = fromstring(value)
                for context in element.iter('formula_context'):
                    context.text = None
                    for child in context.iter():
                        child.text = None
                        child.tail = None
                return tostring(element, encoding='unicode')
            return value
        model.answer = answer
        extraction = await AsyncPDFCraft().open_extraction(source)
        from pdf_craft.document.content import read_content
        from pdf_craft.transformer.content import _unit
        expected = {}
        with extraction._materialize() as paths:
            async for item in read_content(paths.chapters):
                if item.value is not None:
                    expected[(item.filename, item.index)] = tostring(encode(_unit(item)))
        values = []
        async with asyncio.timeout(20), content_view(extraction, translator=translator, window=6,
                                with_anchored=False, with_furniture=True, deferred={}) as (_, _, _, items):
            async for item in items:
                values.append(item)
        self.assertEqual(sum(item.value is not None for item in values), 62)
        self.assertLessEqual(len(model.wire_calls), 15, [len(batch) for batch in model.calls])
        for item in values:
            if item.value is not None:
                self.assertEqual(tostring(encode(_unit(item))), expected[(item.filename, item.index)])
        print('PRIVATE_PCEX_REQUESTS', len(model.wire_calls), 'including translation, fill, metadata, furniture')

    @policy_operation
    async def test_staggered_input_and_slow_single_provider_use_shared_groups(self):
        from pdf_craft.transformer.structured_translation import StructuredTranslation
        from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
        translator, model = translator_fixture(max_group_score=2600)
        original = model.invoke
        async def invoke(*args, **kwargs):
            await asyncio.sleep(0.04)
            return await original(*args, **kwargs)
        translator._translation_runtime.__dict__['_invoke'] = invoke
        translator._fill_runtime.__dict__['_invoke'] = invoke
        async def roots():
            for index in range(6):
                await asyncio.sleep(0.02)  # Genuine input pacing, no request barrier.
                yield TranslationTask(fromstring(f'<body><p>Paragraph {index}.</p></body>'), SubmitKind.REPLACE, index)
        results = await asyncio.wait_for(translator._translate_elements(
            roots(), **StructuredTranslation().arguments(), window=1), 5)
        self.assertEqual([index for _, index in results], list(range(6)))
        self.assertEqual([len(batch) for batch in model.calls], [1, 1])
        self.assertEqual(model.peak, 1)

    @policy_operation
    async def test_group_is_adopted_atomically_after_complete_fill_repair(self):
        from pdf_craft.transformer.structured_translation import StructuredTranslation
        from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
        translator, model = translator_fixture(max_group_score=2600)
        original_invoke, original_answer = model.invoke, model.answer
        repairing, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        rejected = False
        complete = None
        def answer(messages):
            nonlocal rejected, complete
            if rejected and messages[1]['content'].startswith('The immediately preceding XML answer'):
                assert complete is not None
                return complete
            value = original_answer(messages)
            if 'XML template:' in messages[1]['content'] and 'Paragraph 1.' in value and not rejected:
                rejected = True
                complete = value
                return value.replace('Paragraph 1.', 'wrong')
            return value
        async def invoke(messages, *args, **kwargs):
            if rejected and messages[0].message.startswith('You are an XML structure engineer.'):
                repairing.set()
                await release.wait()
            return await original_invoke(messages, *args, **kwargs)
        model.answer = answer
        translator._translation_runtime.__dict__['_invoke'] = invoke
        translator._fill_runtime.__dict__['_invoke'] = invoke
        async def roots():
            yield TranslationTask(fromstring('<body><p>Paragraph 0.</p><p>Paragraph 1.</p></body>'), SubmitKind.REPLACE, 0)
        values = []
        def notify(mapping):
            values.append(''.join(text.text for text in mapping[1]))
            delivered.set()
        task = asyncio.create_task(translator._translate_elements(roots(),
            **StructuredTranslation().arguments(), on_mapping=notify))
        try:
            await asyncio.wait_for(repairing.wait(), 3)
            self.assertFalse(delivered.is_set())
            self.assertEqual(values, [])
            self.assertFalse(task.done())
        finally:
            release.set()
            await asyncio.wait_for(task, 5)
        self.assertEqual([len(batch) for batch in model.calls], [1, 1, 1])
        self.assertEqual(values, ['Paragraph 0.', 'Paragraph 1.'])

    @policy_operation
    async def test_paused_source_flushes_first_result_and_cancellation_closes_read(self):
        from pdf_craft.transformer.structured_translation import StructuredTranslation
        from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
        translator, _model = translator_fixture(max_group_score=2600)
        delivered, closed, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def roots():
            try:
                yield TranslationTask(fromstring('<body><p>First.</p></body>'), SubmitKind.REPLACE, 0)
                await release.wait()
                yield TranslationTask(fromstring('<body><p>Last.</p></body>'), SubmitKind.REPLACE, 1)
            finally:
                closed.set()
        task = asyncio.create_task(translator._translate_elements(roots(),
            **StructuredTranslation().arguments(), on_mapping=lambda _value: delivered.set()))
        try:
            await asyncio.wait_for(delivered.wait(), 2)
            self.assertFalse(task.done())
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(closed.is_set())

    @policy_operation
    async def test_busy_request_slot_and_bounded_tail_keep_all_units(self):
        from pdf_craft.transformer.structured_translation import StructuredTranslation
        from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
        for dense in (True, False):
            with self.subTest(dense=dense):
                translator, model = translator_fixture(max_group_score=2600)
                original = model.invoke
                async def invoke(*args, provider=original, **kwargs):
                    await asyncio.sleep(0.02)
                    return await provider(*args, **kwargs)
                translator._translation_runtime.__dict__['_invoke'] = invoke
                translator._fill_runtime.__dict__['_invoke'] = invoke
                async def roots(dense=dense):
                    indexes = [range(137)] if dense else ([index] for index in range(137))
                    for index, group in enumerate(indexes):
                        body = ''.join(f'<p>Paragraph {i}.</p>' for i in group)
                        yield TranslationTask(fromstring('<body>' + body + '</body>'), SubmitKind.REPLACE, index)
                results = await asyncio.wait_for(translator._translate_elements(roots(),
                    **StructuredTranslation().arguments(), window=1), 8)
                self.assertEqual([node.text for root, _ in results for node in root.findall('p')],
                                 [f'Paragraph {index}.' for index in range(137)])
                self.assertLessEqual(len(model.wire_calls), 9, [len(batch) for batch in model.calls])
                self.assertTrue(all(len(batch) <= 64 for batch in model.calls))
                self.assertEqual(model.peak, 1)

    @policy_operation
    async def test_blocked_provider_bounds_source_admission_and_cancel_joins_transport(self):
        from pdf_craft.transformer.structured_translation import StructuredTranslation
        from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
        translator, _model = translator_fixture(max_group_score=2600)
        entered, closed, source_closed, release = (asyncio.Event() for _ in range(4))
        produced = 0
        async def invoke(*_args, **_kwargs):
            entered.set()
            try:
                await release.wait()
                raise AssertionError('cancelled request must not resume')
            finally:
                closed.set()
        translator._translation_runtime.__dict__['_invoke'] = invoke
        async def roots():
            nonlocal produced
            try:
                for index in range(1000):
                    produced += 1
                    yield TranslationTask(fromstring(f'<body><p>Paragraph {index}.</p></body>'), SubmitKind.REPLACE, index)
            finally:
                source_closed.set()
        task = asyncio.create_task(translator._translate_elements(roots(),
            **StructuredTranslation().arguments(), window=1))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            self.assertLessEqual(produced, 64)
            self.assertFalse(source_closed.is_set())
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
        self.assertTrue(closed.is_set())
        self.assertTrue(source_closed.is_set())

    async def test_fragmented_book_profile_has_full_request_gate_without_private_data(self):
        from pdf_craft import BookMeta
        from pdf_craft.extractor.chapter.chapter import TextFlowItem, SourceTextFragment
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / 'source', page_pixel_sizes={i: (100, 100) for i in range(1, 7)},
                                     book_meta=BookMeta(title='Synthetic source book'))
            for path in (root / 'source/chapters').glob('*.xml'):
                path.unlink()
            units = []
            for index in range(57):
                text = f'Item number {index}' if index < 40 else 'segment ' * 120 + str(index)
                units.append(TextFlowItem('body', 0, [SourceTextFragment(index // 10 + 1, index % 10,
                    (0, (index % 10) * 8, 90, (index % 10) * 8 + 7), [text])]))
            for chapter in range(11):
                start, end = chapter * 57 // 11, (chapter + 1) * 57 // 11
                save_xml(encode(Chapter(chapter + 1, 0, units[start:end])),
                         root / f'source/chapters/chapter_{chapter + 1}.xml')
            save_xml(fromstring('<furnitures><patterns><pattern id="1" kind="universal">'
                '<position id="1">Running heading</position><position id="2">Running footer</position>'
                '</pattern></patterns><pages/></furnitures>'), root / 'source/furnitures.xml')
            translator, model = translator_fixture(max_group_score=2600, encoding='o200k_base')
            result = await asyncio.wait_for(AsyncPDFCraft().translate_extraction(source, root / 'result.pcex',
                translator, with_anchored=False, with_furniture=True, target_language='English', window=6), 15)
            self.assertTrue(result._validate())
            with result._materialize() as paths:
                assert paths.translations is not None
                coverage = next(paths.translations.glob('*/coverage.xml'))
                entries = fromstring(coverage.read_text()).findall('./narrative/paragraph')
                self.assertEqual(len(entries), 57)
                self.assertTrue(all(node.get('state') == 'translated' for node in entries))
            self.assertLessEqual(len(model.wire_calls), 15, [len(batch) for batch in model.calls])

    @policy_operation
    async def test_one_large_chapter_does_not_expand_all_work_before_first_request(self):
        from pdf_craft.transformer.structured_translation import StructuredTranslation
        from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
        translator, _model = translator_fixture(max_group_score=2600)
        entered, release = asyncio.Event(), asyncio.Event()
        expanded = 0
        def observe(segments):
            nonlocal expanded
            for segment in segments:
                expanded += 1
                yield segment
        async def invoke(*_args, **_kwargs):
            entered.set()
            await release.wait()
            raise AssertionError('request should be cancelled')
        translator._translation_runtime.__dict__['_invoke'] = invoke
        async def roots():
            body = ''.join(f'<p>Paragraph {index}.</p>' for index in range(1000))
            yield TranslationTask(fromstring('<body>' + body + '</body>'), SubmitKind.REPLACE, 0)
        task = asyncio.create_task(translator._translate_elements(roots(),
            **StructuredTranslation().arguments(), interrupt_source_text_segments=observe, window=1))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            self.assertLess(expanded, 256, 'mapper must not materialize an entire large chapter of work')
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)

    @policy_operation
    async def test_one_repair_does_not_reserve_a_whole_admission_group(self):
        from pdf_craft.transformer.structured_translation import StructuredTranslation
        from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
        translator, model = translator_fixture(max_group_score=2600)
        original_invoke, original_answer = model.invoke, model.answer
        repairing, later, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        rejected = False
        def answer(messages):
            nonlocal rejected
            value = original_answer(messages)
            if not rejected and 'XML template:' in messages[1]['content'] and 'Paragraph 0.' in value:
                rejected = True
                return value.replace('Paragraph 0.', 'wrong')
            return value
        async def invoke(messages, *args, **kwargs):
            text = '\n'.join(message.message for message in messages)
            if rejected and len(messages) > 2 and 'Paragraph 0.' in text and 'XML template:' in text:
                repairing.set()
                await release.wait()
            if 'Paragraph 200.' in text:
                later.set()
            return await original_invoke(messages, *args, **kwargs)
        model.answer = answer
        translator._translation_runtime.__dict__['_invoke'] = invoke
        translator._fill_runtime.__dict__['_invoke'] = invoke
        async def roots():
            for index in range(273):
                yield TranslationTask(fromstring(f'<body><p>Paragraph {index}.</p></body>'), SubmitKind.REPLACE, index)
        task = asyncio.create_task(translator._translate_elements(roots(),
            **StructuredTranslation().arguments(), window=2, collect_results=False))
        try:
            await asyncio.wait_for(repairing.wait(), 3)
            await asyncio.wait_for(later.wait(), 5)
            self.assertFalse(task.done())
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)

    @policy_operation
    async def test_artist_receives_shared_background_once_outside_grouped_source(self):
        from pdf_craft.transformer.structured_translation import StructuredTranslation
        from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
        translator, model = translator_fixture(max_group_score=2600)
        original = model.answer
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                return messages[1]['content'].split('Text to translate:\n', 1)[1].strip('\n')
            return original(messages)
        model.answer = answer
        tasks = [TranslationTask(fromstring(f'<p>Paragraph {i}.</p>'), SubmitKind.REPLACE, i)
                 for i in range(6)]
        result = await translator._translate_elements(tasks,
            **StructuredTranslation(context=lambda _segments: 'Background evidence only.').arguments(), window=1)
        self.assertEqual(len(result), 6)
        artists = [call for call in model.wire_calls if call[0].message.startswith('You are a translator.')]
        self.assertEqual(len(artists), 1)
        user = artists[0][1].message
        self.assertEqual(user.count('Background evidence only.'), 1)
        self.assertEqual(user.count('Text to translate:'), 1)
        self.assertEqual(user.count('Paragraph '), 6)
        assert_free_artist_prompts(self, model)
