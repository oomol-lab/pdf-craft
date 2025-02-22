from .llm import LLM
from .secondary import PageInfo, TextInfo
from .segment import allocate_segments


def analyse_citations(llm: LLM, pages: list[PageInfo], request_max_tokens: int):
  prompt_name = "citation"
  prompt_tokens = llm.prompt_tokens_count(prompt_name)
  data_max_tokens = request_max_tokens - prompt_name
  if data_max_tokens <= 0:
    raise ValueError(f"Request max tokens is too small (less than system prompt tokens count {prompt_tokens})")

  citations = [p.citation for p in pages if p.citation is not None]
  _split_into_task(citations, data_max_tokens)


def _split_into_task(citations: list[TextInfo], data_max_tokens: int):
  for _ in allocate_segments(citations, data_max_tokens):
    pass
