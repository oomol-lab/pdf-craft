"""Best-effort LaTeX spelling normalization, solely for string comparison."""
import re
import unicodedata

from pylatexenc.latex2text import LatexNodes2Text
from pylatexenc.latexwalker import (
    LatexWalker, LatexCharsNode, LatexGroupNode, LatexMacroNode,
    LatexMathNode, LatexEnvironmentNode, LatexCommentNode, LatexSpecialsNode,
)

from ..common.whitespace import is_whitespace

_START = re.compile(r'(?<!\\)\\(?:[A-Za-z]+|[([%&#_$])|(?<!\\)\$')
_WORD = re.compile(r'\b[A-Za-z]{2,}\b')


def normalize_latex(text: str) -> str:
    """Return strings only; unrecognized syntax remains ordinary literal text.

    No source identity, XML, model protocol or output mutation is involved.
    Parse possible constructs independently; unknown constructs retain their
    complete spelling. If no end can be parsed, preserve the remaining text.
    """
    if not _START.search(text):
        return text
    try:
        converter = LatexNodes2Text()
        walker = LatexWalker(text, tolerant_parsing=False)
        parts, cursor = [], 0
        while match := _START.search(text, cursor):
            start = match.start()
            parts.append(text[cursor:start])
            try:
                nodes, _, length = walker.get_latex_nodes(pos=start, read_max_nodes=1)
            except Exception:
                # The parser could not establish the construct's end. Preserve
                # the remaining text rather than normalize inside broken syntax.
                parts.append(text[start:])
                return ''.join(parts)
            end = start + length
            value = text[start:end]
            try:
                # Remember the whole construct before conversion: a failure in
                # any nested macro must preserve its braces and delimiters too.
                node = nodes[0]
                if isinstance(node, LatexMathNode) and node.delimiters[0] == '$':
                    # A numeric tail can have padding, signs or punctuation
                    # (decimal amounts, accounting parentheses, etc.). None
                    # establishes that the preceding dollars were math. Stop
                    # at another possible LaTeX construct, rather than look
                    # through an independent following formula.
                    following = end
                    while following < len(text):
                        char = text[following]
                        if char in '\\$' or not (is_whitespace(char) or unicodedata.category(char)[0] in 'PS'):
                            break
                        following += 1
                    if text[following:following+1].isnumeric():
                        raise ValueError('Possible adjacent currency amounts')
                value = _render(node, converter)
            except Exception:  # A parse/conversion failure is not a fill failure.
                pass
            parts.append(value)
            cursor = end
        parts.append(text[cursor:])
        return ''.join(parts)
    except Exception:  # Even parser/converter initialization is optional.
        return text


def _render(node, converter) -> str:
    if isinstance(node, LatexCharsNode):
        return node.chars
    if isinstance(node, (LatexCommentNode, LatexSpecialsNode)):
        return node.latex_verbatim()
    if isinstance(node, LatexGroupNode):
        return node.delimiters[0] + _children(node, converter) + node.delimiters[1]
    if isinstance(node, LatexMathNode):
        # A pair of currency dollars is syntactically valid TeX too. Plain
        # prose words are not enough evidence to strip those dollar signs.
        if any(isinstance(child, LatexCharsNode) and _WORD.search(child.chars)
               for child in node.nodelist):
            raise ValueError('Ambiguous prose inside math delimiters')
        value = _children(node, converter)
        # Braces around ONE script character are only a spelling difference.
        return re.sub(r'([_^])\{([A-Za-z0-9])\}', r'\1\2', value)
    if isinstance(node, LatexMacroNode):
        if node.macroname == '\\':
            return node.latex_verbatim()  # Matrix row separators are content.
        spec = converter.latex_context.get_macro_spec(node.macroname)
        if spec is None or node.macroname == 'today':
            raise ValueError('Unknown or context-dependent LaTeX command')
        arguments = node.nodeargd.argnlist if node.nodeargd is not None else []
        if arguments:
            # Preserve command/argument structure instead of the lossy plain
            # text approximation: frac{1}{2+3} must differ from frac{1}{2}+3.
            return '\\' + node.macroname + ''.join(
                _render(argument, converter) for argument in arguments if argument is not None)
        if node.macroname in '%&#_$':
            return node.macroname
        if not isinstance(spec.simplify_repl, str):
            raise ValueError('Context-dependent LaTeX replacement')
        value = converter.nodelist_to_text([node])
        if not value and node.macroname not in (',', ';', ':', '!', ' '):
            raise ValueError('Unrepresentable LaTeX command')
        return value
    if isinstance(node, LatexEnvironmentNode):
        if converter.latex_context.get_environment_spec(node.environmentname) is None:
            raise ValueError('Unknown LaTeX environment')
        arguments = node.nodeargd.argnlist if node.nodeargd is not None else []
        return ('\\begin{' + node.environmentname + '}'
                + ''.join(_render(argument, converter) for argument in arguments if argument is not None)
                + _children(node, converter) + '\\end{' + node.environmentname + '}')
    raise ValueError('Unsupported LaTeX syntax')


def _children(node, converter) -> str:
    return ''.join(_render(child, converter) for child in node.nodelist)
