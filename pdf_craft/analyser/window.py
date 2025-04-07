from dataclasses import dataclass


@dataclass
class LLMWindowTokens:
  main_texts: int | None = None
  citations: int | None = None

def parse_window_tokens(window_tokens: LLMWindowTokens | int | None) -> LLMWindowTokens:
  main_texts = 4400
  citations = 3300

  if isinstance(window_tokens, int):
    main_texts = window_tokens
    citations = window_tokens

  elif isinstance(window_tokens, LLMWindowTokens):
    if window_tokens.main_texts is not None:
      main_texts = window_tokens.main_texts
    if window_tokens.citations is not None:
      citations = window_tokens.citations

  return LLMWindowTokens(
    main_texts=main_texts,
    citations=citations,
  )