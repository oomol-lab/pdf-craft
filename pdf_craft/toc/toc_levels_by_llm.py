import json

from ..llm import LLM
from .toc_levels import Ref2Level
from .toc_pages import PageRef


def analyse_toc_levels_by_llm(toc_pages: list[PageRef], llm: LLM) -> Ref2Level:
    title_items: list[tuple[str, list[tuple[int, int]]]] = []  # (text, references)
    for ref in toc_pages:
        for title in ref.matched_titles:
            references = [(r.page_index, r.order) for r in title.references]
            if references:
                title_items.append((title.text, references))

    if not title_items:
        return {}

    prompt = _build_llm_prompt(title_items)
    response = llm.request(input=prompt)
    ref2level = _parse_json_response(response, title_items)

    return ref2level


def _build_llm_prompt(title_items: list[tuple[str, list[tuple[int, int]]]]) -> str:
    """Build LLM prompt to analyze title hierarchy structure"""
    prompt_lines = [
        "You are a table of contents (TOC) hierarchy analyzer. Given a list of TOC entries below, analyze the hierarchy level of each entry.",
        "",
        "Hierarchy levels:",
        '- Level 0: Top-level chapters (e.g., "Chapter 1", "Part I")',
        '- Level 1: Sections (e.g., "1.1 Introduction", "Section A")',
        '- Level 2: Subsections (e.g., "1.1.1 Background", "1.1.2 Methodology")',
        "- Higher numbers indicate deeper nesting",
        "",
        "TOC entries (indexed from 0):",
    ]

    for idx, (title_text, _) in enumerate(title_items):
        prompt_lines.append(f"{idx}: {title_text}")

    prompt_lines.extend(
        [
            "",
            "Return ONLY a JSON array of integers representing the level for each entry.",
            "Example: [0, 1, 2, 1, 0]",
            "",
            "Your response (JSON array only, no explanation):",
        ]
    )
    return "\n".join(prompt_lines)


def _parse_json_response(
    response: str,
    title_items: list[tuple[str, list[tuple[int, int]]]],
) -> Ref2Level:
    """Parse JSON array response from LLM"""
    ref2level: Ref2Level = {}

    # Extract JSON array from response
    levels = json.loads(response.strip())

    # Map levels to references
    for idx, (_, references) in enumerate(title_items):
        if idx < len(levels):
            level = levels[idx]
            for page_index, order in references:
                ref2level[(page_index, order)] = level

    return ref2level
