from dataclasses import dataclass

@dataclass
class LLMWindowTokens:
  main_texts: int | None = None
  citations: int | None = None