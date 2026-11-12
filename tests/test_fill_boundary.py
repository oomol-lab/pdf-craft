"""Final delivery and request contracts for the simplified XML fill boundary."""
# pylint: disable=protected-access
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring, tostring
from zipfile import ZipFile

from pdf_craft import AsyncPDFCraft, SubmitKind, RetryPolicy
from pdf_craft.llm.runtime import LLMRuntime
from pdf_craft.transformer.fill_text import plain_translation, comparison_text, text_difference, comparison_distance
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from pdf_craft.transformer.xml_translator.xml.friendly import decode_friendly
from pdf_craft.extractor.chapter.chapter import encode, decode
from tests.fill_fixtures import chapter, expression
from tests.fill_fixtures import model_replies
from tests.test_epub_optional_cfi import book
from tests.translation_model import translator_fixture


class ComparisonTests(TestCase):
    def test_shared_normalization_ignores_only_agreed_representation(self):
        for left, right in [('e\u0301\tA\u3000B\x08', 'éAB'), ('你\u200b 好\ufeff', '你好'),
                            ('a\n\n b', 'ab')]:
            with self.subTest(left=left):
                self.assertEqual(comparison_text(left), comparison_text(right))
        for left, right in [('x²', 'x2'), ('1.5', '15'), ('-1', '1'), ('A', 'a'),
                            ('a\u200db', 'ab'), ('a\u200cb', 'ab'), ('ё', 'е')]:
            with self.subTest(left=left):
                self.assertNotEqual(comparison_text(left), comparison_text(right))

    def test_repair_distance_uses_the_same_full_comparison_as_feedback(self):
        for expected, actual, distance in [('Echo. ' * 3, 'Echo. ' * 2, 5),
                                           ('Echo. ' * 2, 'Echo. ' * 3, 5),
                                           ('ab', 'ac', 2), ('a\u200b b', 'ab', 0),
                                           (r'$20$', r'\( 20 \)', 0)]:
            with self.subTest(expected=expected, actual=actual):
                self.assertEqual(comparison_distance(expected, actual), distance)
                self.assertEqual(text_difference(expected, actual) is None, distance == 0)

    def test_diagnostics_quote_literal_backslashes_and_original_context_without_repr(self):
        expected = r'Prefix \plain\name & <value> suffix'
        actual = r'Prefix \plain\na & <value> suffix'
        block = fromstring('<p id="1"/>')
        block.text = actual
        original = tostring(block)
        feedback = text_difference(expected, actual, [block])
        assert feedback is not None
        expected_context = next(decode_friendly(feedback, tags='expected_context'))
        actual_context = next(decode_friendly(feedback, tags='candidate_context'))
        self.assertEqual(expected_context.text, expected)
        self.assertEqual(actual_context.text, actual)
        field = next(decode_friendly(feedback, tags='field_excerpt'))
        self.assertEqual(field.text, actual)
        self.assertNotIn(r'\\plain', feedback)
        self.assertIn('&amp; &lt;value&gt;', feedback)
        self.assertEqual(tostring(block), original)

    def test_context_is_not_synthesized_from_a_noninvertible_comparison_view(self):
        with patch('pdf_craft.transformer.fill_text.normalize_latex', side_effect=lambda value: value.replace('LONG', 'q')):
            feedback = text_difference('LONG B', 'wrong B')
        assert feedback is not None
        self.assertNotIn('<expected_context', feedback)
        self.assertNotIn('PAIRED ORIGINAL CONTEXT', feedback)

    def test_markdown_has_no_custom_formula_recognition(self):
        for raw, expected in [('**bold** and *emphasis*', 'bold and emphasis'),
                              ('# Heading\n\n[text](https://example.org)', 'Headingtext'),
                              ('```python\nprint(1)\n```', 'print(1)'),
                              ('a * b costs $20 or £5', 'a * b costs $20 or £5'),
                              (r'\(x=1\) then $$x=0$$', r'\(x=1\) then $$x=0$$'),
                              ('<free>literal & text</free>', '<free>literal & text</free>')]:
            with self.subTest(raw=raw):
                self.assertEqual(comparison_text(plain_translation(raw)), comparison_text(expected))

    def test_optional_markdown_cleanup_failure_does_not_reject(self):
        with patch('pdf_craft.transformer.fill_text.MarkdownIt', side_effect=ValueError('unavailable')):
            self.assertEqual(plain_translation('**raw**'), '**raw**')

    def test_diff_reports_missing_added_characters(self):
        diff = text_difference('correct', 'wrong')
        assert diff is not None
        self.assertIn('Missing', diff)
        self.assertIn('added', diff)
        self.assertIn('expected[', diff)

    def test_detached_submission_cannot_replace_document_root(self):
        from copy import deepcopy
        from pdf_craft.transformer.xml_translator.xml_translator.submitter import submit
        from pdf_craft.transformer.xml_translator.segment import search_text_segments
        source = fromstring('<body><p>Before.</p><math><mi>x</mi></math><p>After.</p></body>')
        detached = deepcopy(source[1])
        original = tostring(source)
        with self.assertRaisesRegex(ValueError, 'detached'):
            submit(source, SubmitKind.REPLACE, [(detached, list(search_text_segments(detached)))])
        self.assertEqual(tostring(source), original)

    def test_source_projection_does_not_retain_completed_document_roots(self):
        import gc
        import weakref
        from pdf_craft.transformer.xml_translator.segment import search_text_segments
        adapter = XMLInterrupter()
        source = fromstring('<body><p>Before <math><mi>x</mi></math> after.</p></body>')
        reference = weakref.ref(source)
        parts = list(adapter.interrupt_source_text_segments(search_text_segments(source)))
        del parts, source
        gc.collect()
        self.assertIsNone(reference())


class FillBoundaryTests(IsolatedAsyncioTestCase):
    async def test_repeated_math_in_last_existing_slot_survives_actual_delivery(self):
        from pdf_craft.transformer.chapter_formula_interrupter import ChapterFormulaInterrupter
        translator, model = translator_fixture()
        artist = r'\(x\) \(x\) \(x\)'
        model_replies(model, artist, [
            r'<xml><fragment id="1"><expression id="2"/><expression id="3">\(x\) \(x\) \(x\)</expression></fragment></xml>'])
        target = await NarrativeXMLTransformer(translator).transform(
            chapter([expression('x'), ' ', expression('x')]), error_policy=False)
        delivered = encode(decode(encode(target)))
        self.assertIsNone(text_difference(artist, ChapterFormulaInterrupter().visible_text(delivered)))
        self.assertEqual(len(model.wire_calls), 2)

    async def translate(self, raw, responses, *, source=None, formulas=None):
        translator, model = translator_fixture()
        model_replies(model, raw, responses)
        result, _ = await StructuredTranslation(formulas=formulas).translate(
            translator, TranslationTask(source if source is not None else fromstring('<body><p>Original text.</p></body>'),
                                       SubmitKind.REPLACE, None), error_policy=False, retry_policy=RetryPolicy(fill=1))
        return result, model

    async def test_plain_view_is_same_in_request_and_comparison(self):
        raw = '# Title\n\n**Bold** and [link](https://example.org). Costs $20.'
        target, model = await self.translate(raw, ['<xml><p id="1">Title Bold and link. Costs $20.</p></xml>'])
        self.assertEqual(target[0].text, 'Title Bold and link. Costs $20.')
        payload = next(decode_friendly(model.wire_calls[1][1].message.split('Translated text:',1)[1], tags='translation'))
        self.assertEqual(''.join(payload.itertext()), plain_translation(raw))
        self.assertEqual(list(payload), [])
        for forbidden in ('math-ref', 'source-math', 'source-formulas'):
            self.assertNotIn(forbidden, model.wire_calls[1][1].message)
        self.assertEqual(len(model.wire_calls), 2)

    async def test_boundary_whitespace_is_not_an_allocation_problem(self):
        source = fromstring('<body><p><b>One</b> two.</p><pre>code  x\n y</pre></body>')
        target, model = await self.translate('One two.\n\ncode  x\n y', [
            '<xml><p id="1"><b>One </b>two.</p><pre id="2">code x y</pre></xml>'], source=source)
        self.assertEqual(target[1].text, 'code x y')
        self.assertEqual(len(model.wire_calls), 2)

    async def test_text_edits_repair_without_new_artist_request(self):
        target, model = await self.translate('Correct.', ['<xml><p id="1">Wrong.</p></xml>', '<xml><p id="1">Correct.</p></xml>'])
        self.assertEqual(target[0].text, 'Correct.')
        self.assertEqual(sum(c[0].message.startswith('You are a translator.') for c in model.wire_calls), 1)
        self.assertIn('expected[', model.wire_calls[-1][-1].message)

    async def test_numeric_conversion_pcex_and_epub(self):
        for epub in (False, True):
            with self.subTest(epub=epub):
                translator, model = translator_fixture()
                tag = 'p' if epub else 'fragment'
                model_replies(model, '费用超过40亿。', [f'<xml><{tag} id="1">费用超过<expression>40</expression>亿。</{tag}></xml>'])
                if epub:
                    source = fromstring('<body><p>Over <math id="amount"><mn>4</mn></math> billion.</p></body>')
                    target, _ = await StructuredTranslation(formulas=XMLInterrupter()).translate(
                        translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False)
                    formula, = target.iter('math')
                    self.assertEqual(formula.get('id'), 'amount')
                    self.assertEqual(formula.findtext('semantics/annotation'), '40')
                    self.assertEqual(formula.tail, '亿。')
                else:
                    value = await NarrativeXMLTransformer(translator).transform(chapter(['Over ', expression('4'), ' billion.']), error_policy=False)
                    target = encode(value)
                    self.assertEqual(decode(target), value)
                    self.assertEqual(target.findtext('.//inline_expr'), '40')
                self.assertEqual(len(model.wire_calls), 2)

    async def test_reordered_formula_ids_retain_real_attributes(self):
        source = fromstring('<body><p><math id="one"><mn>1</mn></math> then <math id="zero"><mn>0</mn></math>.</p></body>')
        target, _ = await self.translate('$0$ then $1$.', [
            '<xml><p id="1"><expression id="3">$0$</expression> then <expression id="2">$1$</expression>.</p></xml>'],
            source=source, formulas=XMLInterrupter())
        self.assertEqual([n.get('id') for n in target.iter('math')], ['zero', 'one'])
        self.assertEqual([n.findtext('semantics/annotation') for n in target.iter('math')], ['$0$', '$1$'])

    async def test_merged_formula_and_plain_artist_math_have_no_range_gate(self):
        source = fromstring('<body><p><math><mi>x</mi></math> <math><mi>y</mi></math>.</p></body>')
        target, model = await self.translate('$xy$ and $q$.', [
            '<xml><p id="1"><expression id="2">$xy$</expression><expression id="3"/> and $q$.</p></xml>'],
            source=source, formulas=XMLInterrupter())
        self.assertEqual(XMLInterrupter().visible_text(target), '$xy$ and $q$.')
        self.assertEqual(len(model.wire_calls), 2)

    async def test_one_source_formula_can_contain_multiple_artist_expressions(self):
        source = fromstring('<body><p><math><mi>x</mi></math>.</p></body>')
        target, _ = await self.translate('$x$ and $y$.', [
            '<xml><p id="1"><expression>$x$ and $y$</expression>.</p></xml>'], source=source, formulas=XMLInterrupter())
        self.assertEqual(XMLInterrupter().visible_text(target), '$x$ and $y$.')

    async def test_materialization_changes_are_checked_before_adoption(self):
        class BrokenAdapter(XMLInterrupter):
            def materialize(self, root):
                result = super().materialize(root)
                result.text = 'INVENTED'
                return result
        with self.assertRaisesRegex(Exception, 'Visible text differs'):
            await self.translate('Complete.', ['<xml><p id="1">Complete.</p></xml>'] * 2,
                                 formulas=BrokenAdapter())

    async def test_root_replacement_regression_through_public_epub_api(self):
        translator, model = translator_fixture()
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                text = messages[1]['content'].split('Text to translate:\n',1)[1].strip()
                return text
            from tests.translation_model import EchoModel
            root = next(decode_friendly(EchoModel.answer(messages), tags='xml'))
            paragraphs = [node for node in root if node.tag == 'p']
            displays = [node for node in root if node.tag == 'expression' and node.get('display') == 'block']
            if paragraphs and displays:
                inline = next(paragraphs[0].iter('expression'))
                value = inline.text or ''
                inline.text = ''
                old_tail, inline.tail = inline.tail or '', ''
                displays[0].text = value + old_tail + (displays[0].text or '')
            from pdf_craft.transformer.xml_translator.xml.friendly import encode_friendly
            return encode_friendly(root)
        model.answer = answer
        async def invoke(_runtime, messages, *args, _model=model, **kwargs):
            return await _model.invoke(messages, *args, **kwargs)
        with TemporaryDirectory() as folder:
            source, target = Path(folder)/'source.epub', Path(folder)/'target.epub'
            book(source, [('c.xhtml', 'application/xhtml+xml', '<html xmlns="http://www.w3.org/1999/xhtml"><body><article><p>Before <math xmlns="http://www.w3.org/1998/Math/MathML"><mi>y</mi></math> equation.</p><math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><mi>x</mi><mo>=</mo><mn>1</mn></math><p>After equation.</p></article></body></html>')])
            with patch.object(LLMRuntime, '_invoke_async', invoke):
                await AsyncPDFCraft(error_policy=False, retry_policy=RetryPolicy(fill=1)).translate_epub(
                    source, target, llm=translator._translation_llm, executor=translator._translation_runtime.executor,
                    target_language='Chinese', max_group_tokens=2600, window=1, submit=SubmitKind.REPLACE)
            with ZipFile(target) as archive:
                root = fromstring(archive.read('EPUB/c.xhtml'))
            self.assertEqual(sum(n.tag.split('}')[-1] == 'body' for n in root.iter()), 1, tostring(root, encoding='unicode'))
            self.assertEqual(sum(n.tag.split('}')[-1] == 'p' for n in root.iter()), 2)
            self.assertEqual(sum(n.tag.split('}')[-1] == 'math' for n in root.iter()), 2)
            self.assertIn('Before', tostring(root, encoding='unicode'))
            self.assertIn('After equation.', tostring(root, encoding='unicode'))

    async def test_epub_metadata_quotes_survive_final_publication(self):
        translator, model = translator_fixture()
        async def invoke(_runtime, messages, *args, _model=model, **kwargs):
            return await _model.invoke(messages, *args, **kwargs)
        with TemporaryDirectory() as folder:
            source, target = Path(folder)/'source.epub', Path(folder)/'target.epub'
            book(source, [('c.xhtml', 'application/xhtml+xml', '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Text.</p></body></html>')])
            with ZipFile(source) as archive:
                contents = {name: archive.read(name) for name in archive.namelist()}
            contents['EPUB/book.opf'] = contents['EPUB/book.opf'].replace(b'Book title', '《Book title》'.encode())
            with ZipFile(source, 'w') as archive:
                for name, data in contents.items():
                    archive.writestr(name, data)
            with patch.object(LLMRuntime, '_invoke_async', invoke):
                await AsyncPDFCraft(error_policy=False).translate_epub(
                    source, target, llm=translator._translation_llm, executor=translator._translation_runtime.executor,
                    target_language='Chinese', max_group_tokens=2600, window=1, submit=SubmitKind.REPLACE)
            with ZipFile(target) as archive:
                metadata = fromstring(archive.read('EPUB/book.opf'))
            self.assertIn('《Book title》', ''.join(metadata.itertext()))

    async def test_extra_tail_wrapper_repairs_without_inventing_an_identity(self):
        source = fromstring('<body><p><span class="a">First</span><span class="b">Second</span></p></body>')
        bad = '<xml><p id="1"><span id="2">A</span><span id="3">B</span><span id="4">tail</span></p></xml>'
        good = '<xml><p id="1"><span id="2">A</span><span id="3">B</span>tail</p></xml>'
        target, model = await self.translate('ABtail', [bad, good], source=source)
        self.assertEqual(''.join(target.itertext()), 'ABtail')
        self.assertEqual([node.get('class') for node in target.iter('span')], ['a', 'b'])
        self.assertEqual(len(model.wire_calls), 3)
        self.assertIn('EXTRA formatting wrapper', model.wire_calls[-1][-1].message)
        self.assertIn('Required structure', model.wire_calls[-1][-1].message)

    async def test_formatting_does_not_authorize_removing_literal_punctuation(self):
        source = fromstring('<body><p>EUR<sup>2</sup></p></body>')
        bad = '<xml><p id="1">EUR<sup>2</sup></p></xml>'
        good = '<xml><p id="1">EUR<sup>$^{2}$</sup></p></xml>'
        target, model = await self.translate('EUR$^{2}$', [bad, good], source=source, formulas=XMLInterrupter())
        self.assertEqual(''.join(target.itertext()), 'EUR$^{2}$')
        self.assertEqual(len(model.wire_calls), 3)
        self.assertIn('expected context=', model.wire_calls[-1][-1].message)

    async def test_final_format_check_matches_actual_source_order(self):
        translator, model = translator_fixture()
        model_replies(model, 'Second. First.', [
            '<xml><p id="2">Second.</p><p id="1">First.</p></xml>',
            '<xml><p id="1">Second.</p><p id="2">First.</p></xml>'])
        tasks = [TranslationTask(fromstring(f'<body><p>{word} original.</p></body>'), SubmitKind.REPLACE, word)
                 for word in ('First', 'Second')]
        results = await StructuredTranslation(formulas=XMLInterrupter()).translate_many(translator, tasks,
            error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual([(root[0].text, key) for root, key in results], [('Second.', 'First'), ('First.', 'Second')])
        self.assertEqual(len(model.wire_calls), 3)

    async def test_plain_percent_is_not_eaten_as_a_tex_comment(self):
        source = fromstring('<body><p><math><mn>4</mn></math></p></body>')
        target, _ = await self.translate('94%', ['<xml><p id="1"><expression>94%</expression></p></xml>'],
                                         source=source, formulas=XMLInterrupter())
        self.assertEqual(target.findtext('.//mtext'), '94%')
        self.assertEqual(target.findtext('.//annotation'), '94%')

    async def test_formula_at_end_of_chapter_is_not_replayed_in_next_chapter(self):
        translator, model = translator_fixture()
        tasks = [TranslationTask(fromstring(f'<body><p>Compute <math><mi>{value}</mi></math></p></body>'),
                                 SubmitKind.REPLACE, value) for value in ('x', 'y')]
        results = await StructuredTranslation(formulas=XMLInterrupter()).translate_many(translator, tasks, error_policy=False)
        self.assertEqual([key for _, key in results], ['x', 'y'])
        self.assertEqual([sum(node.tag == 'math' for node in root.iter()) for root, _ in results], [1, 1])
        self.assertEqual(len(model.wire_calls), 2)

    async def test_literal_and_mixed_expression_display_is_not_only_in_annotation(self):
        for text in ('costs $20 today', 'A & B', '$x$ and $y$', r'plain \name'):
            with self.subTest(text=text):
                from pdf_craft.transformer.xml_translator.xml.friendly import encode_friendly
                candidate = fromstring('<xml><p id="1"><expression/></p></xml>')
                candidate[0][0].text = text
                target, _ = await self.translate(text, [encode_friendly(candidate)],
                    source=fromstring('<body><p><math><mi>x</mi></math></p></body>'), formulas=XMLInterrupter())
                self.assertEqual(target.findtext('.//mtext'), text)
                target.find('.//annotation').text = 'hidden text must not win'
                self.assertEqual(XMLInterrupter().visible_text(target).strip(), text)

    async def test_actual_epub_and_pcex_order_is_checked_before_adoption(self):
        from pdf_craft.extractor.chapter.chapter import Chapter, SourceTextFragment, TextFlowItem
        for epub in (False, True):
            with self.subTest(epub=epub):
                translator, model = translator_fixture()
                tag = 'p' if epub else 'fragment'
                bad = f'<xml><{tag} id="2">Second.</{tag}><{tag} id="1">First.</{tag}></xml>'
                good = f'<xml><{tag} id="1">Second.</{tag}><{tag} id="2">First.</{tag}></xml>'
                model_replies(model, 'Second. First.', [bad, good])
                if not epub:
                    source = Chapter(None, -1, [TextFlowItem('body', i, [SourceTextFragment(
                        1, i, (0, i*20, 100, i*20+18), [f'{word} original.'])])
                        for i, word in enumerate(('First', 'Second'))])
                    target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False,
                        retry_policy=RetryPolicy(fill=1))
                    target = decode(encode(target))
                    contents = []
                    for item in target.flow_items:
                        assert isinstance(item, TextFlowItem)
                        fragment = item.children[0]
                        assert isinstance(fragment, SourceTextFragment)
                        contents.append(fragment.content)
                    self.assertEqual(contents, [['Second.'], ['First.']])
                else:
                    async def invoke(_runtime, messages, *args, _model=model, **kwargs):
                        return await _model.invoke(messages, *args, **kwargs)
                    with TemporaryDirectory() as folder:
                        source, target = Path(folder)/'source.epub', Path(folder)/'target.epub'
                        book(source, [('c.xhtml', 'application/xhtml+xml', '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>First original.</p><p>Second original.</p></body></html>')])
                        with ZipFile(source) as archive:
                            contents = {name: archive.read(name) for name in archive.namelist()}
                        contents['EPUB/book.opf'] = contents['EPUB/book.opf'].replace(b'<title>Book title</title>', b'')
                        contents['EPUB/toc.ncx'] = b'<ncx><navMap/></ncx>'
                        with ZipFile(source, 'w') as archive:
                            for name, data in contents.items():
                                archive.writestr(name, data)
                        with patch.object(LLMRuntime, '_invoke_async', invoke):
                            await AsyncPDFCraft(error_policy=False, retry_policy=RetryPolicy(fill=1)).translate_epub(
                                source, target, llm=translator._translation_llm, executor=translator._translation_runtime.executor,
                                target_language='Chinese', max_group_tokens=2600, window=1, submit=SubmitKind.REPLACE)
                        with ZipFile(target) as archive:
                            root = fromstring(archive.read('EPUB/c.xhtml'))
                        self.assertEqual([''.join(node.itertext()) for node in root.iter() if node.tag.split('}')[-1] == 'p'], ['Second.', 'First.'])
                self.assertEqual(len(model.wire_calls), 3)

    async def test_public_epub_keeps_displayed_currency_and_ampersand(self):
        from pdf_craft.transformer.xml_translator.xml.friendly import encode_friendly
        for text in ('costs $20 today', 'A & B'):
            with self.subTest(text=text):
                translator, model = translator_fixture()
                answer = fromstring('<xml><p id="1"><expression/></p></xml>')
                answer[0][0].text = text
                model_replies(model, text, [encode_friendly(answer)])
                async def invoke(_runtime, messages, *args, _model=model, **kwargs):
                    return await _model.invoke(messages, *args, **kwargs)
                with TemporaryDirectory() as folder:
                    source, target = Path(folder)/'source.epub', Path(folder)/'target.epub'
                    book(source, [('c.xhtml', 'application/xhtml+xml', '<html xmlns="http://www.w3.org/1999/xhtml"><body><p><math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math></p></body></html>')])
                    with ZipFile(source) as archive:
                        contents = {name: archive.read(name) for name in archive.namelist()}
                    contents['EPUB/book.opf'] = contents['EPUB/book.opf'].replace(b'<title>Book title</title>', b'')
                    contents['EPUB/toc.ncx'] = b'<ncx><navMap/></ncx>'
                    with ZipFile(source, 'w') as archive:
                        for name, data in contents.items():
                            archive.writestr(name, data)
                    with patch.object(LLMRuntime, '_invoke_async', invoke):
                        await AsyncPDFCraft(error_policy=False).translate_epub(source, target,
                            llm=translator._translation_llm, executor=translator._translation_runtime.executor,
                            target_language='Chinese', max_group_tokens=2600, window=1, submit=SubmitKind.REPLACE)
                    with ZipFile(target) as archive:
                        reopened = fromstring(archive.read('EPUB/c.xhtml'))
                    visible = [node.text for node in reopened.iter() if node.tag.split('}')[-1] == 'mtext']
                    self.assertEqual(visible, [text])
