"""Compact fill responses preserve the complete immutable artist response."""
import json
from types import SimpleNamespace
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from pdf_craft._policy import policy_operation
from pdf_craft.concurrency import ConcurrentExecutor, FixedCapacity
from pdf_craft.llm import LLM, Message, MessageRole
from pdf_craft.llm.runtime import runtime_for
from pdf_craft.transformer.free_translation import allocate, fill_translation_group


class AllocationBoundaryTests(TestCase):
    def test_reordered_owners_and_empty_owner_keep_every_character(self):
        raw = '  第二段。\n\n第一段。\t'
        result = allocate(json.dumps([dict(id='1', end='第二段。'), dict(id='2', text=''),
                                      dict(id='0', end='第一段。')]),
                          [SimpleNamespace(source_math=None)]*3, raw)
        self.assertEqual(result, {'0': '\n\n第一段。\t', '1': '  第二段。', '2': ''})
        self.assertEqual(result['1'] + result['2'] + result['0'], raw)

    def test_invalid_ambiguous_incomplete_boundaries_and_identity_are_rejected(self):
        for units in ([dict(id='0', end='same')], [dict(id='0', end='absent')],
                      [dict(id='0', end='first same')], [dict(id='0', end='')],
                      [dict(id='0', end='last same', text='changed')],
                      [dict(id='1', end='last same')]):
            with self.subTest(units=units), self.assertRaises(ValueError):
                allocate(json.dumps(units), [SimpleNamespace(source_math=None)], 'first same; last same')

    def test_repeated_passages_need_longer_anchor_not_deduplication(self):
        raw = 'Same.\n\nSame. End.'
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            allocate(json.dumps([dict(id='0', end='Same.'), dict(id='1', end='End.')]),
                     [SimpleNamespace(source_math=None)]*2, raw)
        result = allocate(json.dumps([dict(id='0', end='Same.', after='\n\nSame.'), dict(id='1', end='End.')]),
                          [SimpleNamespace(source_math=None)]*2, raw)
        self.assertEqual(result['0'] + result['1'], raw)

    def test_heading_repeated_in_body_uses_exact_following_context(self):
        raw = 'Dataset.\n\nDataset. This is the body.'
        result = allocate(json.dumps([dict(id='0', end='Dataset.', after='\n\nDataset.'),
                                      dict(id='1', end='the body.')]),
                          [SimpleNamespace(source_math=None)]*2, raw)
        self.assertEqual(result, {'0': 'Dataset.', '1': '\n\nDataset. This is the body.'})
        for after in ('invented context', 1):
            with self.subTest(after=after), self.assertRaises(ValueError):
                allocate(json.dumps([dict(id='0', end='Dataset.', after=after),
                                     dict(id='1', end='the body.')]),
                         [SimpleNamespace(source_math=None)]*2, raw)

    def test_normalized_end_and_after_keep_original_offsets_and_reordered_owners(self):
        raw = '甲\u3000\u3000乙\n\nHello\u200bworld.\ufeff'
        calls = [SimpleNamespace(source_math=None, preserve_space=False)]*2
        result = allocate(json.dumps([
            dict(id='1', end='甲 乙', after=' Helloworld.'),
            dict(id='0', end='Helloworld.'),
        ]), calls, raw)
        self.assertEqual(result, {'1': '甲\u3000\u3000乙', '0': '\n\nHello\u200bworld.\ufeff'})
        self.assertEqual(result['1'] + result['0'], raw)

    def test_new_ambiguity_after_normalization_is_not_resolved_by_exact_match(self):
        calls = [SimpleNamespace(source_math=None)]*2
        raw = '甲\u3000乙。甲 乙。结束。'
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            allocate(json.dumps([dict(id='0', end='甲 乙。'), dict(id='1', end='结束。')]), calls, raw)
        result = allocate(json.dumps([
            dict(id='0', end='甲 乙。', after='甲 乙。'), dict(id='1', end='结束。'),
        ]), calls, raw)
        self.assertEqual(result['0'] + result['1'], raw)

    def test_preserved_owner_and_math_anchors_still_need_exact_content(self):
        for raw, end, preserve in (
                ('code\t  line', 'code line', True),
                (r'prefix \(a  b\).', r'\(a b\).', False),
        ):
            calls = [SimpleNamespace(source_math=None, preserve_space=preserve)]
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                allocate(json.dumps([dict(id='0', end=end)]), calls, raw)
            result = allocate(json.dumps([dict(id='0', end=raw)]), calls, raw)
            self.assertEqual(result, {'0': raw})

    def test_normalized_anchor_cannot_accept_changed_words(self):
        with self.assertRaises(ValueError):
            allocate(json.dumps([dict(id='0', end='甲 丙')]),
                     [SimpleNamespace(source_math=None)], '甲\u3000乙')

    def test_after_context_does_not_normalize_preserved_owner_or_math(self):
        for raw, first, exact_after, second, preserve in (
                ('甲\u3000乙 code\t  line', '甲 乙', ' code\t  line', 'code\t  line', True),
                (r'甲乙 \(a  b\).', '甲乙', r' \(a  b\).', r'\(a  b\).', False),
        ):
            calls = [SimpleNamespace(source_math=None, preserve_space=False),
                     SimpleNamespace(source_math=None, preserve_space=preserve)]
            with self.subTest(raw=raw):
                units = [dict(id='0', end=first, after=exact_after.replace('  ', ' ')),
                         dict(id='1', end=second)]
                with self.assertRaises(ValueError):
                    allocate(json.dumps(units), calls, raw)
                units[0]['after'] = exact_after
                result = allocate(json.dumps(units), calls, raw)
                self.assertEqual(result['0'] + result['1'], raw)


class AllocationBoundaryRequestTests(IsolatedAsyncioTestCase):
    @policy_operation
    async def test_real_fill_accepts_normalized_anchors_without_a_repair(self):
        runtime = runtime_for(LLM('offline', 'https://example.invalid', 'offline', 'cl100k_base'),
                              ConcurrentExecutor(FixedCapacity(1)))
        raw = '甲\u3000\u3000乙\n\nHello\u200bworld.\ufeff'
        calls = [SimpleNamespace(source_math=None, preserve_space=False,
                                 messages=[Message(MessageRole.USER, text)]) for text in ('First.', 'Second.')]
        response = json.dumps([dict(id='1', end='甲 乙', after=' Helloworld.'),
                               dict(id='0', end='Helloworld.')])
        transport = AsyncMock(return_value=response)
        with patch.object(runtime, '_invoke', transport, create=True):
            answers = await fill_translation_group(runtime, calls, raw)
        self.assertEqual(transport.await_count, 1)
        self.assertEqual(answers['1'] + answers['0'], raw)
        assert transport.await_args is not None
        payload = json.loads(transport.await_args.args[0][1].message)
        self.assertEqual(payload['translation'], raw)
