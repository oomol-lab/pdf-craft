"""Bounded mathematical expressions, not the grammar of a TeX document.

An unsupported construct is ordinary text to callers. Nothing is evaluated;
command arguments and literal escapes retain their structure.
"""
import re
import unicodedata

from pylatexenc.latex2text import LatexNodes2Text

from ..common.whitespace import is_whitespace

MAX_LENGTH = 4096
MAX_DEPTH = 16
_NUMBER = re.compile(r'(?:\d+(?:\.\d+)?|\.\d+)')
_NAME = re.compile(r'[A-Za-z]+')
_COMMAND = re.compile(r'\\([A-Za-z]+|[^\n])')
_FUNCTIONS = frozenset(('sin', 'cos', 'tan', 'log', 'ln', 'exp', 'lim', 'max', 'min'))
_ARGUMENTS = {'frac': 2, 'dfrac': 2, 'tfrac': 2, 'sqrt': 1,
              'mathrm': 1, 'mathbf': 1, 'mathit': 1, 'mathbb': 1, 'mathcal': 1,
              'hat': 1, 'bar': 1, 'vec': 1, 'overline': 1, 'underline': 1}
_OPERATORS = frozenset('+-−*/=<>≤≥≠≈×÷·⋅±∓∈∉⊂⊆∪∩:;,')
_LITERAL = frozenset('{}_^&#$%\\')


class MathSyntaxError(ValueError):
    """The optional mathematical subset cannot represent this expression."""


class MathParser:
    """Consume one mathematical prefix without searching for a closing dollar."""

    def __init__(self, text: str, converter: LatexNodes2Text):
        self.text = text[:MAX_LENGTH]
        self.pos = 0
        self.converter = converter

    def whitespace(self) -> None:
        while self.pos < len(self.text) and is_whitespace(self.text[self.pos]):
            self.pos += 1

    def expression(self, closing: str = '', depth: int = 0) -> str:
        if depth > MAX_DEPTH:
            raise MathSyntaxError('Expression nesting exceeds the optional parser bound')
        parts, atom, pending = [], False, False
        while self.pos < len(self.text):
            self.whitespace()
            if self.pos == len(self.text):
                break
            char = self.text[self.pos]
            if char in '$)}]' or self.text.startswith(('\\)', '\\]'), self.pos):
                break
            if char in _OPERATORS:
                if not atom and char not in '+-−':
                    raise MathSyntaxError('Missing mathematical operand')
                parts.append(char)
                self.pos += 1
                pending = True
            elif char in '^_':
                if not atom or pending:
                    raise MathSyntaxError('Script without a mathematical base')
                self.pos += 1
                self.whitespace()
                argument = self.atom(depth + 1, single=True)
                parts.append(char + (argument if argument.startswith('{') else '{' + argument + '}'))
            else:
                parts.append(self.atom(depth + 1))
                atom, pending = True, False
        if not atom or pending:
            raise MathSyntaxError('Incomplete mathematical expression')
        if closing:
            if self.pos == len(self.text) or self.text[self.pos] != closing:
                raise MathSyntaxError('Unclosed mathematical group')
            self.pos += 1
        return ''.join(parts)

    def atom(self, depth: int, *, single: bool = False) -> str:
        if depth > MAX_DEPTH or self.pos >= len(self.text):
            raise MathSyntaxError('Missing or excessively nested mathematical atom')
        char = self.text[self.pos]
        if char in '{([':
            self.pos += 1
            return char + self.expression({'{': '}', '(': ')', '[': ']'}[char], depth) + {'{': '}', '(': ')', '[': ']'}[char]
        if char == '\\':
            return self.command(depth)
        match = _NUMBER.match(self.text, self.pos)
        if match is not None:
            value = match[0][:1] if single else match[0]
            self.pos += len(value)
            return value
        match = _NAME.match(self.text, self.pos)
        if match is not None:
            value = match[0]
            if len(value) > 1 and value not in _FUNCTIONS:
                raise MathSyntaxError('Plain prose is not a mathematical expression')
            self.pos += len(value)
            return '\\' + value + '{}' if value in _FUNCTIONS else value
        code = ord(char)
        if (0x370 <= code <= 0x3ff or 0x2100 <= code <= 0x214f
                or 0x1d400 <= code <= 0x1d7ff or unicodedata.category(char) == 'Sm'):
            self.pos += 1
            return char
        if char == '%':
            self.pos += 1
            return r'\%'  # No TeX comment grammar: a percent never eats prose.
        raise MathSyntaxError('Unsupported mathematical atom')

    def command(self, depth: int) -> str:
        match = _COMMAND.match(self.text, self.pos)
        if match is None:
            raise MathSyntaxError('Incomplete mathematical command')
        name = match[1]
        self.pos = match.end()
        if name in _LITERAL:
            return '\\' + name
        if name in _FUNCTIONS:
            if self.text.startswith('{}', self.pos):
                self.pos += 2
            return '\\' + name + '{}'
        if name in _ARGUMENTS:
            arguments = []
            self.whitespace()
            if name == 'sqrt' and self.pos < len(self.text) and self.text[self.pos] == '[':
                arguments.append(self.atom(depth + 1))
            for _ in range(_ARGUMENTS[name]):
                self.whitespace()
                if self.pos == len(self.text) or self.text[self.pos] != '{':
                    raise MathSyntaxError('Expected a complete mathematical argument')
                arguments.append(self.atom(depth + 1))
            return '\\' + ({'dfrac': 'frac', 'tfrac': 'frac'}.get(name, name)) + ''.join(arguments)
        spec = self.converter.latex_context.get_macro_spec(name)
        value = spec.simplify_repl if spec is not None else None
        if (not isinstance(value, str) or not value
                or not all(unicodedata.category(c) == 'Sm' or 0x370 <= ord(c) <= 0x3ff
                           or 0x2100 <= ord(c) <= 0x214f for c in value)):
            raise MathSyntaxError('Not a supported mathematical symbol command')
        return value
