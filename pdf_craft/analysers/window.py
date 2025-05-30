from dataclasses import dataclass

@dataclass
class LLMWindowTokens:
  max_request_data_tokens: int = 4096
  max_verify_paragraph_tokens: int = 512,
  max_verify_paragraphs_count: int = 8,

_MIN_TOKENS = 20

def parse_window_tokens(window_tokens: LLMWindowTokens | int | None) -> LLMWindowTokens:
  if window_tokens is None:
    window_tokens = 4096

  if isinstance(window_tokens, int):
    if window_tokens <= _MIN_TOKENS:
      raise ValueError(f"window_tokens must be greater than {_MIN_TOKENS}.")

    max_verify_paragraph_tokens = max(_MIN_TOKENS, window_tokens // 8)
    max_verify_paragraphs_count = max(2, window_tokens // max_verify_paragraph_tokens)
    window_tokens = LLMWindowTokens(
      max_request_data_tokens=window_tokens,
      max_verify_paragraph_tokens=max_verify_paragraph_tokens,
      max_verify_paragraphs_count=max_verify_paragraphs_count,
    )

  if isinstance(window_tokens, LLMWindowTokens):
    if window_tokens.max_request_data_tokens <= _MIN_TOKENS:
      raise ValueError(f"max_request_data_tokens must be greater than {_MIN_TOKENS}.")
    if window_tokens.max_verify_paragraph_tokens <= _MIN_TOKENS:
      raise ValueError(f"max_verify_paragraph_tokens must be greater than {_MIN_TOKENS}.")
    if window_tokens.max_verify_paragraphs_count < 1:
      raise ValueError("max_verify_paragraphs_count must be at least 1.")

  return window_tokens