"""Whole-group XML regression: ownership, final reading order and one repair stage."""
# pylint: disable=protected-access
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import fromstring
from pdf_craft import RetryPolicy, SubmitKind
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from tests.fill_fixtures import model_replies
from tests.translation_model import translator_fixture


class SingleXMLFillTests(IsolatedAsyncioTestCase):
    async def test_paragraph_boundaries_need_no_end_after_locator(self):
        for first, second in [('小王子', '第一章'), ('0.3', '未知'), ('896', 'A following paragraph')]:
            with self.subTest(first=first):
                translator, model = translator_fixture()
                raw = first+'\n\n'+second
                model_replies(model, raw, [f'<xml><p id="1">{first}</p><p id="2">{second}</p></xml>'])
                tasks = [TranslationTask(fromstring(f'<body><p>Original {i}.</p></body>'), SubmitKind.REPLACE, i) for i in range(2)]
                result = await StructuredTranslation().translate_many(translator, tasks, error_policy=False)
                self.assertEqual([''.join(root.itertext()) for root, _ in result], [first, second])
                self.assertEqual(len(model.wire_calls), 2)
                self.assertNotIn('"end"', model.wire_calls[-1][0].message)

    async def test_final_source_order_is_repaired_before_adoption(self):
        translator, model = translator_fixture()
        model_replies(model, 'Second translated.\n\nFirst translated.', [
            '<xml><p id="2">Second translated.</p><p id="1">First translated.</p></xml>',
            '<xml><p id="1">Second translated.</p><p id="2">First translated.</p></xml>'])
        tasks = [TranslationTask(fromstring(f'<body><p>{text} original.</p></body>'), SubmitKind.REPLACE, text) for text in ('First', 'Second')]
        result = await StructuredTranslation().translate_many(translator, tasks, error_policy=False)
        self.assertEqual([(root[0].text, key) for root, key in result], [('Second translated.', 'First'), ('First translated.', 'Second')])
        self.assertEqual(len(model.wire_calls), 3)


    async def test_diff_reports_actual_missing_and_added_text_without_retranslation(self):
        translator, model = translator_fixture()
        model_replies(model, 'A correct sentence.', ['<xml><p id="1">A wrong sentence.</p></xml>', '<xml><p id="1">A correct sentence.</p></xml>'])
        target, _ = await StructuredTranslation().translate(translator, TranslationTask(fromstring('<body><p>Original.</p></body>'), SubmitKind.REPLACE, None), error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(target[0].text, 'A correct sentence.')
        self.assertEqual(len(model.wire_calls), 3)
        feedback = model.wire_calls[-1][-1].message
        self.assertIn('Missing', feedback)
        self.assertIn('added', feedback)
        self.assertIn('candidate[', feedback)
        self.assertIn('rrect', feedback)
        self.assertIn('wr', feedback)

    async def test_duplicate_unknown_missing_block_ids_repair_atomically(self):
        valid = '<xml><p id="1">First.</p><p id="2">Second.</p></xml>'
        for bad in (valid.replace('id="2"', 'id="1"'), valid.replace('id="1"', 'id="bad"'), valid.replace('<p id="2">Second.</p>', '')):
            with self.subTest(bad=bad):
                translator, model = translator_fixture()
                model_replies(model, 'First.\n\nSecond.', [bad, valid])
                target, _ = await StructuredTranslation().translate(translator, TranslationTask(fromstring('<body><p>One.</p><p>Two.</p></body>'), SubmitKind.REPLACE, None), error_policy=False, retry_policy=RetryPolicy(fill=1))
                self.assertEqual([node.text for node in target], ['First.', 'Second.'])
                self.assertEqual(len(model.wire_calls), 3)

    async def test_arbitrary_tag_names_do_not_grant_read_only_content_privileges(self):
        translator, model = translator_fixture()
        model_replies(model, 'Translated.', [
            '<xml><p id="1"><formula_context><expression>Translated.</expression></formula_context></p></xml>',
            '<xml><p id="1"><formula_context><expression/></formula_context>Translated.</p></xml>'])
        source = fromstring('<body><p><formula_context display="inline"><expression display="inline"/></formula_context>Original.</p></body>')
        target, _ = await StructuredTranslation().translate(translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(''.join(target.itertext()), 'Translated.')
        self.assertEqual(''.join(target[0][0].itertext()), 'Translated.')
        self.assertEqual(len(model.wire_calls), 2)

    async def test_protected_code_preserves_exact_spaces_and_line_breaks(self):
        translator, model = translator_fixture()
        model_replies(model, 'a  b\n\tc', ['<xml><pre id="1">a  b\n\tc</pre></xml>'])
        source = fromstring('<body><pre>original  code\nline</pre></body>')
        target, _ = await StructuredTranslation().translate(translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False)
        self.assertEqual(target[0].text, 'a  b\n\tc')
        self.assertEqual(len(model.wire_calls), 2)
