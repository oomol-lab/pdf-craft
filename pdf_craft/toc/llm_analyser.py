import json
from dataclasses import dataclass
from typing import Generator

from json_repair import repair_json
from pydantic import BaseModel, ValidationError, field_validator

from ..llm import LLM, Message, MessageRole
from ..pdf import Page
from .toc_levels import Ref2Level
from .toc_pages import PageRef

_MAX_RETRIES = 3


class _TocLevelsSchema(BaseModel):
    levels: list[int]

    @field_validator("levels")
    @classmethod
    def validate_levels(cls, v: list[int]) -> list[int]:
        # Rule 1: Check all are valid integers in range
        for i, level in enumerate(v):
            if not isinstance(level, int):
                raise ValueError(
                    f"Level at index {i} is not an integer: {level}. "
                    f"All values must be integers."
                )
            if level < 0:
                raise ValueError(
                    f"Level at index {i} is negative: {level}. "
                    f"All levels must be non-negative (0, 1, 2, ...)."
                )
            if level > 5:
                raise ValueError(
                    f"Level at index {i} is too deep: {level}. "
                    f"Maximum level is 5. Most TOCs have 3-4 levels."
                )

        # Rule 2: Check for reasonable transitions (no jump > 2)
        for i in range(1, len(v)):
            jump = v[i] - v[i - 1]
            if jump > 2:
                raise ValueError(
                    f"Level jump too large at index {i}: "
                    f"from {v[i - 1]} to {v[i]} (jump of {jump}). "
                    f"Level changes should not exceed 2 at once."
                )

        # Rule 3: First level should typically be 0
        if v and v[0] > 1:
            raise ValueError(
                f"First level is {v[0]}, but TOCs typically start at level 0. "
                f"Consider starting with 0 for the first chapter."
            )

        return v


@dataclass
class _TocEntry:
    text: str
    references: list[tuple[int, int]] | None
    indent: float
    font_size: float
    is_matched: bool


class LLMAnalysisError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def analyse_toc_by_llm(
    llm: LLM,
    toc_page_refs: list[PageRef],
    toc_page_contents: list[Page],
) -> Ref2Level:
    toc_entries = list(_extract_toc_entries(toc_page_contents))
    if not toc_entries:
        return {}

    matched_title2references: list[tuple[str, list[tuple[int, int]]]] = []
    for page_ref in toc_page_refs:
        for matched_title in page_ref.matched_titles:
            references = [(r.page_index, r.order) for r in matched_title.references]
            if references:
                matched_title2references.append((matched_title.text, references))

    if not matched_title2references:
        return {}

    last_error: str | None = None
    tail_messages: list[Message] = []
    head_messages: list[Message] = [
        Message(
            role=MessageRole.SYSTEM,
            message=_build_system_prompt(),
        ),
        Message(
            role=MessageRole.USER,
            message=_build_user_prompt(
                toc_entries=toc_entries,
                matched_titles=matched_title2references,
            ),
        ),
    ]
    for attempt in range(_MAX_RETRIES):
        try:
            response = llm.request(input=head_messages + tail_messages)
            levels, error_msg = _validate_and_parse(
                response=response,
                matched_titles_count=len(matched_title2references),
            )
            if levels is not None:
                return _map_to_ref2level(levels, matched_title2references)

            if attempt < _MAX_RETRIES - 1:
                tail_messages = [
                    Message(role=MessageRole.ASSISTANT, message=response),
                    Message(
                        role=MessageRole.USER,
                        message=_build_error_feedback(error_msg or "Unknown error"),
                    ),
                ]
            last_error = error_msg

        except Exception as e:
            last_error = str(e)

    error_detail = f"Last error: {last_error}" if last_error else "Unknown error"
    raise LLMAnalysisError(
        f"LLM analysis failed after {_MAX_RETRIES} attempts. {error_detail}"
    )


def _extract_toc_entries(
    toc_page_contents: list[Page],
) -> Generator[_TocEntry, None, None]:
    for page in toc_page_contents:
        for layout in page.body_layouts:
            text = layout.text.strip()
            if not text:
                continue
            left, top, _, bottom = layout.det
            indent = left
            font_size = bottom - top

            yield _TocEntry(
                text=text,
                references=None,  # Will be filled by LLM's response
                indent=indent,
                font_size=font_size,
                is_matched=False,  # Not used in new approach
            )


def _build_system_prompt() -> str:
    prompt_lines = [
        "You are analyzing a table of contents (TOC) from a book.",
        "",
        "TASK (2 steps):",
        "",
        "STEP 1 - Analyze the complete TOC structure:",
        "- Review all entries provided by the user",
        "- Identify which are actual TOC items (chapters/sections) vs. noise (headers/footers/page numbers)",
        "- Assign hierarchy levels to ALL actual TOC items",
        "- Output your analysis in any format you prefer (this is your draft/scratch work)",
        "",
        "STEP 2 - Extract results for TARGET TITLES:",
        "- Find each TARGET TITLE in your analysis",
        "- Extract their levels",
        "- Return a JSON object mapping each title ID to its level",
        "",
        "Understanding hierarchy:",
        "- Hierarchy levels represent parent-child relationships between entries",
        "- If entry A contains entry B as a subsection, then B's level = A's level + 1",
        "- Entries at the same level are siblings (neither is a child of the other)",
        "- The specific level numbers (0, 1, 2, ...) are relative - what matters is the relationships",
        "- Start numbering from the shallowest level you identify (typically 0 or 1)",
        "",
        "How to determine hierarchy:",
        "- Indent values are an important clue about hierarchy",
        "- Font size may indicate hierarchy",
        "- Text patterns (numbering, structural markers) reveal relationships",
        "- Semantic meaning helps understand the logical structure",
        "",
        "Think about:",
        "- What patterns do you see in the indent values?",
        "- How might the editor have decided to indent different levels?",
        "- Which entries logically belong together as siblings?",
        "- Which entries are children of which parents?",
        "",
        "Important considerations:",
        "- When indent patterns conflict with your semantic interpretation, pause and reconsider",
        "- Could there be multiple valid ways to interpret the structure?",
        "- Book structures can be unconventional - the semantic meaning you assume might not be the only possibility",
        "- If you notice conflicting signals, explore whether alternative interpretations exist",
        "",
        "Your response format:",
        "ANALYSIS:",
        "(your analysis of the complete TOC structure - use any format you like)",
        "",
        "RESULT:",
        '{"A": level, "B": level, "C": level, ...}',
    ]
    return "\n".join(prompt_lines)


def _build_user_prompt(
    toc_entries: list[_TocEntry],
    matched_titles: list[tuple[str, list[tuple[int, int]]]],
) -> str:
    prompt_lines = ["COMPLETE TOC (all entries):"]

    for idx, toc_entry in enumerate(toc_entries):
        prompt_lines.append(
            f"  {idx}: [Indent:{toc_entry.indent:.1f}, Size:{toc_entry.font_size:.1f}] {toc_entry.text}"
        )

    prompt_lines.extend(
        (
            "",
            f"TARGET TITLES (need levels for these {len(matched_titles)} titles):",
        )
    )
    for idx, (title, _) in enumerate(matched_titles):
        letter_id = _index_to_letter_id(idx)
        prompt_lines.append(f"  {letter_id}: {title}")

    return "\n".join(prompt_lines)


def _index_to_letter_id(index: int) -> str:
    """
    Convert a zero-based index to a letter ID (A, B, C, ..., Z, AA, AB, ...).

    Similar to Excel column naming:
    - 0 -> A
    - 25 -> Z
    - 26 -> AA
    - 27 -> AB
    - 51 -> AZ
    - 52 -> BA

    Args:
        index: Zero-based index

    Returns:
        Letter ID string
    """
    result = ""
    index += 1  # Convert to 1-based for easier calculation

    while index > 0:
        index -= 1  # Adjust for 0-based alphabet
        result = chr(ord("A") + (index % 26)) + result
        index //= 26

    return result


def _validate_and_parse(
    response: str, matched_titles_count: int
) -> tuple[list[int] | None, str | None]:
    """
    Validate and parse LLM response.

    Expects response format:
    ANALYSIS:
    (LLM's analysis)

    RESULT:
    {"A": level, "B": level, ...}

    Returns:
        (levels, error_message)
        - If successful: (levels, None)
        - If failed: (None, error_message_for_llm)
    """
    try:
        result_marker = "RESULT:"
        if result_marker in response:
            # Find the LAST occurrence of RESULT: to avoid matching it in ANALYSIS section
            result_start = response.rindex(result_marker) + len(result_marker)
            result_section = response[result_start:].strip()
        else:
            return None, (
                "Response is missing the RESULT section. "
                "Please follow the format:\n"
                "ANALYSIS:\n"
                "(your analysis)\n\n"
                "RESULT:\n"
                '{"A": level, "B": level, ...}'
            )

        repaired = repair_json(result_section)
        data = json.loads(repaired)

        if not isinstance(data, dict):
            return None, (
                'Response must be a JSON object mapping IDs to levels, e.g., {"A": 1, "B": 0}. '
                f"You returned: {type(data).__name__}"
            )

        expected_ids = [_index_to_letter_id(i) for i in range(matched_titles_count)]
        expected_ids_set = set(expected_ids)
        actual_ids_set = set(data.keys())
        missing_ids = expected_ids_set - actual_ids_set

        if missing_ids:
            missing_str = ", ".join(sorted(missing_ids))
            return None, (
                f"Missing IDs in response: {missing_str}. "
                f"You must provide levels for all TARGET TITLES: {', '.join(expected_ids)}"
            )

        unexpected_ids = actual_ids_set - expected_ids_set
        if unexpected_ids:
            unexpected_str = ", ".join(sorted(unexpected_ids))
            return None, (
                f"Unexpected IDs in response: {unexpected_str}. "
                f"Valid IDs are: {', '.join(expected_ids)}"
            )

        levels = [data[letter_id] for letter_id in expected_ids]
        schema = _TocLevelsSchema(levels=levels)
        min_level = min(schema.levels)
        if min_level != 0:
            normalized_levels = [level - min_level for level in schema.levels]
        else:
            normalized_levels = schema.levels

        return normalized_levels, None

    except json.JSONDecodeError as e:
        return None, (
            f"Invalid JSON syntax: {str(e)}. "
            'Please return a valid JSON object in the RESULT section like {"A": 1, "B": 0, "C": 1}.'
        )

    except ValidationError as e:
        errors = e.errors()
        if errors and "msg" in errors[0]:
            return None, errors[0]["msg"]
        return None, str(e)

    except Exception as e:
        return None, f"Unexpected error: {str(e)}"


def _map_to_ref2level(
    levels: list[int],
    matched_titles: list[tuple[str, list[tuple[int, int]]]],
) -> Ref2Level:
    ref2level: Ref2Level = {}
    for idx, (_, references) in enumerate(matched_titles):
        level = levels[idx]
        for page_index, order in references:
            ref2level[(page_index, order)] = level
    return ref2level


def _build_error_feedback(error_message: str) -> str:
    return "\n".join(
        (
            "Your previous response had an error:",
            error_message,
            "",
            "Please correct the response. Remember the format:",
            "ANALYSIS:",
            "(your analysis)",
            "",
            "RESULT:",
            '{"A": level, "B": level, ...}',
            "",
            "Requirements:",
            "- All values must be between 0 and 5",
            "- Level changes should not jump more than 2 at once",
            "- TOCs typically start at level 0",
        )
    )
