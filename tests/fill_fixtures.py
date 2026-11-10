"""Small public source documents and deterministic model replies."""
from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.chapter import Chapter, InlineExpression, SourceTextFragment, TextFlowItem

def expression(value):
    return InlineExpression(ExpressionKind.INLINE_PAREN, value)

def chapter(content):
    return Chapter(None, -1, [TextFlowItem('body', -1, [
        SourceTextFragment(2, 0, (0, 0, 90, 20), content),
    ])])

def content_of(value):
    text = value.flow_items[0]
    assert isinstance(text, TextFlowItem)
    fragment = text.children[0]
    assert isinstance(fragment, SourceTextFragment)
    return fragment.content

def source():
    return Chapter(None, -1, [TextFlowItem('body', -1, [
        SourceTextFragment(1, 0, (0, 0, 100, 20), [
            'Take ', InlineExpression(ExpressionKind.INLINE_PAREN, 'x ='), ' ',
            InlineExpression(ExpressionKind.INLINE_PAREN, 'y'), '.',
        ]),
    ])])

def model_replies(model, raw, replies):
    answers = iter(replies)
    def answer(messages):
        return raw if messages[0]['content'].startswith('You are a translator.') else next(answers)
    model.answer = answer

