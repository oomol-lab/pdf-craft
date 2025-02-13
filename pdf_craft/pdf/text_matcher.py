import re
import io

from enum import Enum
from unicodedata import category
from alphabet_detector import AlphabetDetector


def check_texts_matching_rank(text1: str, text2: str):
  words1: list[str] = list(split_into_words(text1))
  words2: list[str] = list(split_into_words(text2))

  if len(words1) > len(words2):
    words1, words2 = words2, words1



class _Phase(Enum):
  Init = 0,
  Letter = 1,
  Character = 2,
  Number = 3,
  Space = 4,

def split_into_words(text: str):
  space_pattern = re.compile(r"\s")
  number_pattern = re.compile(r"\d")
  number_signs_pattern = re.compile(r"[\.,']")
  word_buffer = io.StringIO()
  phase: _Phase = _Phase.Init

  for char in text:
    if _is_letter(char):
      if phase == _Phase.Number:
        yield word_buffer.getvalue()
        word_buffer = io.StringIO()
      word_buffer.write(char)
      phase = _Phase.Letter

    elif number_pattern.match(char):
      if phase == _Phase.Letter:
        yield word_buffer.getvalue()
        word_buffer = io.StringIO()
      word_buffer.write(char)
      phase = _Phase.Letter

    elif phase == _Phase.Number and \
         number_signs_pattern.match(char):
      word_buffer.write(char)

    else:
      if phase == _Phase.Letter or \
        phase == _Phase.Number:
        yield word_buffer.getvalue()
        word_buffer = io.StringIO()

      if space_pattern.match(char):
        phase = _Phase.Space
      else: # others (like Chinese character)
        yield char
        phase = _Phase.Character

  if phase == _Phase.Letter:
    yield word_buffer.getvalue()

ad = AlphabetDetector()

# check is Latin, Cyrillic, Greek, or Hebrew letter
def _is_letter(char: str):
  if not category(char).startswith("L"):
    return False

  # AlphabetDetector unable to process punctuation
  return ad.is_latin(char) or \
         ad.is_cyrillic(char) or \
         ad.is_greek(char) or \
         ad.is_hebrew(char)