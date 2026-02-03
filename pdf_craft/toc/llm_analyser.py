import json
import re
from dataclasses import dataclass
from typing import Callable, Generator, Generic, Iterable, TypeVar

from json_repair import repair_json
from pydantic import BaseModel, ValidationError, field_validator

from ..common import XMLReader, split_by_cv
from ..config import MAX_LEVELS, MAX_TITLE_CV
from ..llm import LLM, Message, MessageRole
from ..pdf import TITLE_TAGS, Page
from .toc_levels import Ref2Level
from .toc_pages import PageRef

_MAX_RETRIES = 3
_P = TypeVar("_P")
_R = TypeVar("_R")


class LLMAnalysisError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def analyse_title_levels_by_llm(llm: LLM, pages: XMLReader[Page]) -> Ref2Level:
    titles: list[_Title] = []
    for page in pages.read():
        for layout in page.body_layouts:
            if layout.ref not in TITLE_TAGS:
                continue
            _, top, _, bottom = layout.det
            titles.append(
                _Title(
                    text=re.sub(r"\s+", " ", layout.text).strip(),
                    ref=(page.index, layout.order),
                    height=bottom - top,
                )
            )

    if not titles:
        return {}

    # Use CV to group titles by font size (preliminary grouping)
    grouped_titles = list(
        reversed(
            split_by_cv(  # 字体最大的是 Level 0，故颠倒
                payload_items=[(title.height, title) for title in titles],
                max_groups=MAX_LEVELS,
                max_cv=MAX_TITLE_CV,
            )
        )
    )
    analyser = _LLMAnalyser(
        llm=llm,
        validate=_validate_title_response,
    )
    levels = analyser.request(
        payload=len(titles),
        messages=(
            Message(
                role=MessageRole.SYSTEM,
                message=_build_title_system_prompt(),
            ),
            Message(
                role=MessageRole.USER,
                message=_build_title_user_prompt(
                    titles=titles,
                    grouped_titles=grouped_titles,
                ),
            ),
        ),
    )
    ref2level: Ref2Level = {}

    for idx, title in enumerate(titles):
        level = levels[idx]
        if level >= 0:  # Only include non-noise titles
            ref2level[title.ref] = level

    return ref2level


def analyse_toc_levels_by_llm(
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

    analyser = _LLMAnalyser(
        llm=llm,
        validate=_validate_toc_response,
    )
    levels = analyser.request(
        payload=len(matched_title2references),
        messages=(
            Message(
                role=MessageRole.SYSTEM,
                message=_build_toc_system_prompt(),
            ),
            Message(
                role=MessageRole.USER,
                message=_build_toc_user_prompt(
                    toc_entries=toc_entries,
                    matched_titles=matched_title2references,
                ),
            ),
        ),
    )
    ref2level: Ref2Level = {}

    for idx, (_, references) in enumerate(matched_title2references):
        level = levels[idx]
        for page_index, order in references:
            ref2level[(page_index, order)] = level

    return ref2level


def _extract_toc_entries(
    toc_page_contents: list[Page],
) -> Generator["_TocEntry", None, None]:
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


def _build_title_system_prompt() -> str:
    prompt_lines = [
        "You are analyzing the structure of a book by examining its headings/titles.",
        "",
        "TASK (2 steps):",
        "",
        "STEP 1 - Understand the document structure:",
        "- You are seeing all the headings extracted from a book",
        "- Some may be actual chapter/section titles that form the book's hierarchy",
        "- Some may be noise (figure captions, exercise numbers, page headers, index entries, etc.)",
        "- Your goal is to identify which headings are part of the main structural hierarchy",
        "- Output your thinking process - what patterns do you see? What helps you distinguish structure from noise?",
        "",
        "STEP 2 - Assign hierarchy levels:",
        "- For each heading ID provided, determine its level in the hierarchy",
        "- Return a JSON object mapping each ID to its level (0-based, where 0 is the top level)",
        "- If a heading is noise (not part of the main structure), assign it level -1",
        "",
        "Understanding hierarchy:",
        "- Hierarchy represents parent-child relationships between headings",
        "- If heading A contains heading B as a subsection, then B's level = A's level + 1",
        "- Headings at the same level are siblings",
        "- Top-level chapters/parts are level 0",
        "",
        "What helps you determine hierarchy:",
        "- Font size is a strong signal (we've grouped headings by similar font sizes for your reference)",
        "- Numbering patterns (1, 1.1, 1.1.1 vs Chapter 1, Section 1.1)",
        '- Semantic meaning ("Chapter" vs "Section" vs "Subsection")',
        "- Position in the document (early vs late pages)",
        '- Density (a page with dozens of "headings" might be an index)',
        "",
        "Think about:",
        "- What is the purpose of this book's structure?",
        "- What patterns indicate a heading is structural vs decorative?",
        "- Are there headings that don't fit the main narrative flow?",
        "- How does the author organize ideas hierarchically?",
        "",
        "Your response format:",
        "ANALYSIS:",
        "(your thinking process about the structure - what patterns you observe, what you consider noise, how you determined the hierarchy)",
        "",
        "RESULT:",
        '{"0": level, "1": level, "2": level, ...}',
        "",
        "Remember: Use -1 for headings that are noise (not part of the structural hierarchy).",
    ]
    return "\n".join(prompt_lines)


def _build_title_user_prompt(
    titles: list["_Title"],
    grouped_titles: list[list["_Title"]],
) -> str:
    prompt_lines = ["HEADINGS (grouped by font size):"]
    prompt_lines.append("")

    for group_idx, group in enumerate(grouped_titles):
        if not group:
            continue
        sample_title = group[0]
        prompt_lines.append(
            f"Group {group_idx} (Font size ~{sample_title.height:.1f}, {len(group)} headings):"
        )

    prompt_lines.append("")
    prompt_lines.append(f"ALL HEADINGS (total: {len(titles)}):")

    for idx, title in enumerate(titles):
        # Find which group this title belongs to
        group_num = -1
        for group_idx, group in enumerate(grouped_titles):
            if title in group:
                group_num = group_idx
                break

        prompt_lines.append(
            f"  {idx}: [Group:{group_num}, Page:{title.ref[0]}, Size:{title.height:.1f}] {title.text}"
        )

    return "\n".join(prompt_lines)


def _validate_title_response(
    response: str, titles_count: int
) -> tuple[list[int] | None, str | None]:
    """
    Validate and parse LLM response for title analysis.

    Expects response format:
    ANALYSIS:
    (LLM's analysis)

    RESULT:
    {"0": level, "1": level, ...}

    Returns:
        (levels, error_message)
        - If successful: (levels, None)
        - If failed: (None, error_message_for_llm)
    """
    try:
        result_marker = "RESULT:"
        if result_marker in response:
            result_start = response.rindex(result_marker) + len(result_marker)
            result_section = response[result_start:].strip()
        else:
            return None, (
                "Response is missing the RESULT section. "
                "Please follow the format:\n"
                "ANALYSIS:\n"
                "(your analysis)\n\n"
                "RESULT:\n"
                '{"0": level, "1": level, ...}'
            )

        repaired = repair_json(result_section)
        data = json.loads(repaired)

        if not isinstance(data, dict):
            return None, (
                'Response must be a JSON object mapping IDs to levels, e.g., {"0": 0, "1": 1}. '
                f"You returned: {type(data).__name__}"
            )

        expected_ids = [str(i) for i in range(titles_count)]
        expected_ids_set = set(expected_ids)
        actual_ids_set = set(data.keys())
        missing_ids = expected_ids_set - actual_ids_set

        if missing_ids:
            missing_str = ", ".join(sorted(missing_ids, key=int))
            return None, (
                f"Missing IDs in response: {missing_str}. "
                f"You must provide levels for all headings (0 to {titles_count - 1})"
            )

        unexpected_ids = actual_ids_set - expected_ids_set
        if unexpected_ids:
            unexpected_str = ", ".join(sorted(unexpected_ids))
            return None, (
                f"Unexpected IDs in response: {unexpected_str}. "
                f"Valid IDs are: 0 to {titles_count - 1}"
            )

        levels = [data[str(i)] for i in range(titles_count)]
        schema = _TitleLevelsSchema(levels=levels)

        # Normalize levels: exclude -1 (noise), then normalize others
        valid_levels = [level for level in schema.levels if level >= 0]
        if valid_levels:
            min_level = min(valid_levels)
            if min_level != 0:
                normalized_levels = [
                    level - min_level if level >= 0 else -1 for level in schema.levels
                ]
            else:
                normalized_levels = schema.levels
        else:
            normalized_levels = schema.levels

        max_allowed_level = MAX_LEVELS - 1
        capped_levels = [
            min(level, max_allowed_level) if level >= 0 else -1
            for level in normalized_levels
        ]
        return capped_levels, None

    except json.JSONDecodeError as e:
        return None, (
            f"Invalid JSON syntax: {str(e)}. "
            'Please return a valid JSON object in the RESULT section like {"0": 0, "1": 1, "2": -1}.'
        )

    except ValidationError as e:
        errors = e.errors()
        if errors and "msg" in errors[0]:
            return None, errors[0]["msg"]
        return None, str(e)

    except Exception as e:
        return None, f"Unexpected error: {str(e)}"


def _build_toc_system_prompt() -> str:
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


def _build_toc_user_prompt(
    toc_entries: list["_TocEntry"],
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


def _validate_toc_response(
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

        max_allowed_level = MAX_LEVELS - 1
        capped_levels = [min(level, max_allowed_level) for level in normalized_levels]

        return capped_levels, None

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


class _LLMAnalyser(Generic[_P, _R]):
    def __init__(
        self,
        llm: LLM,
        validate: Callable[[str, _P], tuple[_R | None, str | None]],
    ) -> None:
        self._llm = llm
        self._validate: Callable[[str, _P], tuple[_R | None, str | None]] = validate

    def request(self, payload: _P, messages: Iterable[Message]) -> _R:
        last_error: str | None = None
        head_messages = list(messages)
        tail_messages: list[Message] = []

        for attempt in range(_MAX_RETRIES):
            response = self._llm.request(input=head_messages + tail_messages)
            result, error_msg = self._validate(response, payload)
            if result is not None:
                return result

            if attempt < _MAX_RETRIES - 1:
                tail_messages = [
                    Message(role=MessageRole.ASSISTANT, message=response),
                    Message(
                        role=MessageRole.USER,
                        message=_build_error_feedback(error_msg or "Unknown error"),
                    ),
                ]
            last_error = error_msg

        error_detail = f"Last error: {last_error}" if last_error else "Unknown error"
        raise LLMAnalysisError(
            f"LLM analysis failed after {_MAX_RETRIES} attempts. {error_detail}"
        )


@dataclass
class _Title:
    text: str
    ref: tuple[int, int]
    height: int


@dataclass
class _TocEntry:
    text: str
    references: list[tuple[int, int]] | None
    indent: float
    font_size: float
    is_matched: bool


class _TitleLevelsSchema(BaseModel):
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
            if level < -1:
                raise ValueError(
                    f"Level at index {i} is invalid: {level}. "
                    f"Levels must be -1 (noise) or non-negative (0, 1, 2, ...)."
                )
            if level > 5:
                raise ValueError(
                    f"Level at index {i} is too deep: {level}. "
                    f"Maximum level is 5. Most books have 3-4 levels."
                )

        # Rule 2: Check for reasonable transitions (no jump > 2), excluding -1
        last_valid_level = None
        for i, level in enumerate(v):
            if level == -1:
                continue
            if last_valid_level is not None:
                jump = level - last_valid_level
                if jump > 2:
                    raise ValueError(
                        f"Level jump too large at index {i}: "
                        f"from {last_valid_level} to {level} (jump of {jump}). "
                        f"Level changes should not exceed 2 at once."
                    )
            last_valid_level = level

        # Rule 3: First non-noise level should typically be 0
        first_valid_level = next((level for level in v if level >= 0), None)
        if first_valid_level is not None and first_valid_level > 1:
            raise ValueError(
                f"First valid level is {first_valid_level}, but books typically start at level 0. "
                f"Consider starting with 0 for the first chapter."
            )

        return v


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
