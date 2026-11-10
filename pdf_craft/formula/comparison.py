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
_TEX_SYNTAX = frozenset(r'\{}_^&#$~')


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
                    # Currency can precede OR follow an amount. Both sides
                    # must use the same conservative numeric-context check.
                    if (_numeric_neighbor(text, start-1, -1)
                            or _numeric_neighbor(text, end, 1)):
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


def _numeric_neighbor(text: str, position: int, direction: int) -> bool:
    """Signs/punctuation/padding do not disambiguate currency from math."""
    while 0 <= position < len(text):
        char = text[position]
        if char in '\\$':  # Do not look through another LaTeX construct.
            return False
        if not (is_whitespace(char) or unicodedata.category(char)[0] in 'PS'):
            return char.isnumeric()
        position += direction
    return False


def _render(node, converter) -> str:
    if isinstance(node, LatexCharsNode):
        return node.chars
    if isinstance(node, LatexCommentNode):
        raise ValueError('Comments cannot be treated as visible text')
    if isinstance(node, LatexSpecialsNode):
        return node.latex_verbatim()
    if isinstance(node, LatexGroupNode):
        return node.delimiters[0] + _children(node, converter) + node.delimiters[1]
    if isinstance(node, LatexMathNode):
        if (node.delimiters[0] == '$'
                and not any(char.isalnum() for char in node.latex_verbatim()[1:-1])):
            # Punctuation/blank space between two dollars does not establish
            # math: the dollar signs themselves may be the literal content.
            raise ValueError('No reliable math content between dollar signs')
        # A pair of currency dollars is syntactically valid TeX too. Plain
        # prose words are not enough evidence to strip those dollar signs.
        if any(isinstance(child, LatexCharsNode) and _WORD.search(child.chars)
               for child in node.nodelist):
            raise ValueError('Ambiguous prose inside math delimiters')
        value = _children(node, converter)
        if node.delimiters[0] == '$' and all(is_whitespace(char) for char in value):
            raise ValueError('Empty layout is not reliable dollar math content')
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
        if node.macroname == '%':
            return '%'
        if not isinstance(spec.simplify_repl, str):
            raise ValueError('Context-dependent LaTeX replacement')
        value = converter.nodelist_to_text([node])
        # Keep literal glyph commands distinct from grouping/script/column
        # syntax. Otherwise \{1\} collides with {1}, and x\_2 with x_2.
        if any(char in _TEX_SYNTAX for char in value):
            return node.latex_verbatim()
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
