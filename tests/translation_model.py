"""Offline provider for production translation pipeline tests."""
# pylint: disable=protected-access
import asyncio
import json
import re
from pdf_craft.concurrency import ConcurrentExecutor, FixedCapacity
from pdf_craft.extractor.chapter.chapter import TextFlowItem, SourceTextFragment
from pdf_craft.llm import LLM
from pdf_craft.transformer.xml_translator.xml_translator import XMLTranslator

class EchoModel:
    """Exercise real runtime, cache and fill validation, replacing only HTTP."""
    def __init__(self):
        self.calls = []
        self.wire_calls = []
        self.active = self.peak = 0

    async def invoke(self, messages, *_args, **_kwargs):
        self.wire_calls.append(messages)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0)
            if messages[0].message.startswith('Execute each independent conversation'):
                units = json.loads(messages[-1].message)
                for unit in units:
                    if unit['messages'][0]['role'] != 'system':
                        unit['messages'].insert(0, {'role': 'system', 'content': messages[0].message})
                self.calls.append([unit['messages'] for unit in units])
                return json.dumps([{'id': unit['id'], 'text': self.answer(unit['messages'])}
                                   for unit in reversed(units)])
            units = [[{'role': message.role.name.lower(), 'content': message.message} for message in messages]]
            self.calls.append(units)
            return self.answer(units[0])
        finally:
            self.active -= 1

    @staticmethod
    def answer(messages):
        user = messages[1 if len(messages) > 1 else 0]['content']
        template = re.search(r'XML template:\n```XML\n(.*?)\n```', user, re.S)
        return template[1] if template else user.split('Text to translate:\n', 1)[-1]

def paragraph(index):
    return TextFlowItem('body', 0, [SourceTextFragment(index, index, (0, 0, 100, 30), [f'Paragraph {index}.'])])


def translator_fixture(*, max_group_score=10000, encoding='cl100k_base'):
    model = EchoModel()
    config = LLM('test', 'https://example.invalid', 'offline', encoding, retry_times=0)
    translator = XMLTranslator(config, config, 'English', None, 10000, max_group_score,
                               executor=ConcurrentExecutor(FixedCapacity(2)))
    translator._translation_runtime.__dict__['_invoke'] = model.invoke
    translator._fill_runtime.__dict__['_invoke'] = model.invoke
    return translator, model
