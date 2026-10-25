"""Count original XML text using stable paragraph/field boundaries."""
from ..common.words import WordCounter
from .xml_translator.xml import is_inline_element


def _source_parts(element, stack=()):
    if element.tag in ('translation-context', 'formula_context'):
        return
    stack = (*stack, element)
    pcex = stack[0].tag == 'chapter'

    def is_source_space(value):
        # PCEX encoding inserts line indentation around inline reference tokens.
        return not pcex or ('\n' not in value and '\r' not in value)

    block = next((parent for parent in reversed(stack) if not is_inline_element(parent)), element)
    owner = next((parent for parent in stack if parent.tag == 'text'), block)
    if element.text and (element.text.strip() or (is_inline_element(element) and is_source_space(element.text))):
        yield block, owner, element.text
    for child in element:
        yield from _source_parts(child, stack)
        # Formatting between structural XML blocks is not author text. Spaces
        # between inline siblings ARE source text, even when segmentation would
        # drop that whitespace-only slot from the translation template.
        if child.tail and (child.tail.strip() or (is_inline_element(child) and is_source_space(child.tail))):
            yield block, owner, child.tail


def xml_block_word_counts(element):
    """Yield scalar weights per transport block, retaining semantic word state.

    PCEX fragments share their author paragraph's counter. Inline XML tags
    never separate words. Ordinary EPUB blocks and metadata fields do.
    """
    counter = WordCounter()
    previous_owner = previous_block = None
    block_words = 0
    for block, owner, text in _source_parts(element):
        if block is not previous_block:
            if previous_block is not None:
                yield previous_block, block_words
            previous_block, block_words = block, 0
        if owner is not previous_owner:
            counter.boundary()
            previous_owner = owner
        before = counter.words
        if element.tag == 'furniture-position':
            text = text.replace('__PDF_CRAFT_FOLIO__', ' ')
        counter.feed(text)
        block_words += counter.words - before
    if previous_block is not None:
        yield previous_block, block_words


def count_xml_words(element) -> int:
    return sum(words for _, words in xml_block_word_counts(element))
