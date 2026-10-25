"""Streaming source-word accounting shared by estimates and progress.

Matches Wiki Graph's Han/Letter/Number/Mark rule, with bounded long words.
Combining marks extend a word but do not consume its length allowance.
"""
import unicodedata

MAX_WORD_LENGTH = 20


def _is_han(character: str) -> bool:
    # Unicode Script=Han includes radicals, iteration marks and compatibility
    # ideographs, not just the unified CJK blocks. Keep this independent of the
    # language detector, whose narrower heuristic has a different purpose.
    code = ord(character)
    return (
        code in (0x3005, 0x3007)
        or 0x2E80 <= code <= 0x2E99 or 0x2E9B <= code <= 0x2EF3
        or 0x2F00 <= code <= 0x2FD5 or 0x3021 <= code <= 0x3029
        or 0x3038 <= code <= 0x303B or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFA6D
        or 0xFA70 <= code <= 0xFAD9 or 0x16FE2 <= code <= 0x16FE3
        or 0x16FF0 <= code <= 0x16FF1
        or 0x20000 <= code <= 0x2A6DF or 0x2A700 <= code <= 0x2B739
        or 0x2B740 <= code <= 0x2B81D or 0x2B820 <= code <= 0x2CEA1
        or 0x2CEB0 <= code <= 0x2EBE0 or 0x2EBF0 <= code <= 0x2EE5D
        or 0x2F800 <= code <= 0x2FA1D or 0x30000 <= code <= 0x3134A
        or 0x31350 <= code <= 0x323AF
    )


class WordCounter:
    """Feed arbitrary text chunks; call boundary() only at semantic boundaries."""

    def __init__(self, max_word_length: int = MAX_WORD_LENGTH) -> None:
        if isinstance(max_word_length, bool) or not isinstance(max_word_length, int) or max_word_length < 1:
            raise ValueError('max_word_length must be a positive integer')
        self.max_word_length = max_word_length
        self.words = 0
        self._length = 0

    def feed(self, text: str) -> int:
        """Return cumulative words, independent of chunk boundaries."""
        for character in text:
            if _is_han(character):
                self.words += 1
                self._length = 0
            else:
                category = unicodedata.category(character)[0]
                if category in ('L', 'N'):
                    if self._length % self.max_word_length == 0:
                        self.words += 1
                    self._length = self._length % self.max_word_length + 1
                elif category != 'M' or not self._length:
                    self._length = 0
        return self.words

    def boundary(self) -> int:
        """End a paragraph/field without adding a word."""
        self._length = 0
        return self.words


def count_text_words(text: str) -> int:
    """Count one complete string using the streaming implementation."""
    return WordCounter().feed(text)
