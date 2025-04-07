
from dataclasses import dataclass


@dataclass
class LLMWindowTokens:
  main_texts: int
  citations: int

def parse_window_tokens(window_tokens: LLMWindowTokens | int | None) -> LLMWindowTokens:
  if isinstance(window_tokens, LLMWindowTokens):
    return window_tokens

  main_texts = 4400
  citations = 3300

  if isinstance(window_tokens, int):
    main_texts = window_tokens
    citations = window_tokens

  return LLMWindowTokens(
    main_texts=main_texts,
    citations=citations,
  )