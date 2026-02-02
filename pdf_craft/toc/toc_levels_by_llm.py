import json
import logging

from json_repair import repair_json
from pydantic import BaseModel, ValidationError, field_validator

from ..llm import LLM, Message, MessageRole
from ..pdf import Page
from .toc_levels import Ref2Level
from .toc_pages import PageRef

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3


class TocLevelsSchema(BaseModel):
    """Pydantic schema for TOC levels validation"""

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


class LLMAnalysisError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


# Type alias for TOC entries with layout info
# Format: (text, references_or_none, indent, font_size, is_matched)
# - references_or_none: None if not matched to content, otherwise list of (page_index, order)
# - is_matched: True if this entry needs to be mapped to Ref2Level
TocEntry = tuple[str, list[tuple[int, int]] | None, float, float, bool]


def _extract_all_toc_entries(
    toc_page_refs: list[PageRef],
    toc_page_contents: list[Page],
) -> list[TocEntry]:
    """
    Extract ALL entries from TOC pages with layout information.

    This includes both matched titles (that appear in content) and unmatched titles.
    The LLM needs to see the complete TOC structure to understand hierarchy.

    Returns:
        List of (text, references_or_none, indent, font_size, is_matched) tuples
    """
    all_entries: list[TocEntry] = []

    for page in toc_page_contents:
        # Extract ALL body layouts from this TOC page
        for layout in page.body_layouts:
            text = layout.text.strip()
            if not text:
                continue

            # Extract layout information
            left, top, _, bottom = layout.det
            indent = left
            font_size = bottom - top

            # We don't match here - let LLM do the matching
            all_entries.append((
                text,
                None,  # Will be filled by LLM's response
                indent,
                font_size,
                False,  # Not used in new approach
            ))

    return all_entries


def analyse_toc_levels_by_llm(
        llm: LLM,
        toc_page_refs: list[PageRef],
        toc_page_contents: list[Page],
    ) -> Ref2Level:
    """
    Analyze TOC hierarchy levels using LLM with validation and retry mechanism.

    This function extracts ALL entries from TOC pages (not just matched titles)
    to provide complete context for the LLM to understand hierarchy structure.

    Raises:
        LLMAnalysisError: If analysis fails after all retries
    """
    # Extract ALL TOC entries from pages
    all_entries = _extract_all_toc_entries(toc_page_refs, toc_page_contents)

    if not all_entries:
        return {}

    # Collect all matched titles that need evaluation
    matched_titles_list: list[tuple[str, list[tuple[int, int]]]] = []
    for page_ref in toc_page_refs:
        for matched_title in page_ref.matched_titles:
            references = [(r.page_index, r.order) for r in matched_title.references]
            if references:
                matched_titles_list.append((matched_title.text, references))

    if not matched_titles_list:
        return {}

    # Build initial conversation with the task prompt
    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(all_entries, matched_titles_list)
    messages: list[Message] = [
        Message(role=MessageRole.SYSTEM, message=system_prompt),
        Message(role=MessageRole.USER, message=user_prompt),
    ]
    last_error = None

    for attempt in range(_MAX_RETRIES):
        try:
            response = llm.request(input=messages)

            # Validate and parse response
            levels, error_msg = _validate_and_parse(response, matched_titles_list)

            if levels is not None:
                # Success! Map to Ref2Level
                print(f"LLM analysis succeeded on attempt {attempt + 1}")
                return _map_to_ref2level(levels, matched_titles_list)

            # Validation failed
            last_error = error_msg
            print(
                f"LLM response validation failed (attempt {attempt + 1}/{_MAX_RETRIES}): {error_msg}"
            )

            # Build conversation history for next attempt
            # Only keep: system + initial user prompt + last assistant response + last error feedback
            if attempt < _MAX_RETRIES - 1:
                error_feedback = _build_error_feedback(error_msg or "Unknown error")
                messages = [
                    messages[0],  # Keep system prompt
                    messages[1],  # Keep initial user prompt
                    Message(role=MessageRole.ASSISTANT, message=response),
                    Message(role=MessageRole.USER, message=error_feedback),
                ]

        except Exception as e:
            last_error = str(e)
            print(
                f"Unexpected error during LLM analysis (attempt {attempt + 1}/{_MAX_RETRIES}): {e}"
            )
            if attempt == _MAX_RETRIES - 1:
                break

    # All attempts failed
    error_detail = f"Last error: {last_error}" if last_error else "Unknown error"
    raise LLMAnalysisError(
        f"LLM analysis failed after {_MAX_RETRIES} attempts. {error_detail}"
    )


def _build_system_prompt() -> str:
    """Build system prompt with task instructions and rules"""
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
        "- Return a JSON array",
        "",
        "Hierarchy levels:",
        '- Level 0: Top-level volumes/books (e.g., "第一卷", "第二卷", "Volume I")',
        '- Level 1: Major parts/sections (e.g., "第 I 部分", "第 II 部分", "Part A")',
        '- Level 2: Subsections (e.g., "1.1 Introduction", "1.2 Methods")',
        '- Level 3+: Deeper subsections',
        "",
        "Analysis clues:",
        '- Text patterns: "第一卷/第二卷" (volumes) > "第 I/II/III 部分" (parts) > subsections',
        '- Numbering: Roman numerals (I, II, III) often indicate parts/sections',
        '- Font size: Larger = higher level (but not always reliable)',
        '- Indent: Larger indent = deeper level (but not always reliable)',
        "",
        "Your response format:",
        "ANALYSIS:",
        "(your analysis of the complete TOC structure - use any format you like)",
        "",
        "RESULT:",
        "[array of integers for TARGET TITLES, in order]",
    ]
    return "\n".join(prompt_lines)


def _build_user_prompt(
    all_entries: list[TocEntry],
    matched_titles: list[tuple[str, list[tuple[int, int]]]],
) -> str:
    """Build user prompt with TOC data"""
    prompt_lines = ["COMPLETE TOC (all entries):"]

    for idx, (text, _, indent, font_size, _) in enumerate(all_entries):
        prompt_lines.append(f"  {idx}: [Indent:{indent:.1f}, Size:{font_size:.1f}] {text}")

    prompt_lines.extend([
        "",
        f"TARGET TITLES (need levels for these {len(matched_titles)} titles):",
    ])

    for idx, (title, _) in enumerate(matched_titles):
        prompt_lines.append(f"  {idx}: {title}")

    return "\n".join(prompt_lines)


def _build_error_feedback(error_message: str) -> str:
    """Build error feedback for LLM to correct its response"""
    prompt_lines = [
        "Your previous response had an error:",
        error_message,
        "",
        "Please correct the response. Remember the format:",
        "ANALYSIS:",
        "(your analysis)",
        "",
        "RESULT:",
        "[array of integers]",
        "",
        "Requirements:",
        "- All values must be between 0 and 5",
        "- Level changes should not jump more than 2 at once",
        "- TOCs typically start at level 0",
    ]
    return "\n".join(prompt_lines)


def _validate_and_parse(
    response: str,
    matched_titles: list[tuple[str, list[tuple[int, int]]]],
) -> tuple[list[int] | None, str | None]:
    """
    Validate and parse LLM response.

    Expects response format:
    ANALYSIS:
    (LLM's analysis)

    RESULT:
    [array of integers]

    Returns:
        (levels, error_message)
        - If successful: (levels, None)
        - If failed: (None, error_message_for_llm)
    """
    try:
        # Step 1: Extract RESULT section
        result_marker = "RESULT:"
        if result_marker in response:
            # Find the RESULT section
            result_start = response.index(result_marker) + len(result_marker)
            result_section = response[result_start:].strip()
        else:
            # Fallback: try to parse the entire response as JSON
            result_section = response.strip()

        # Step 2: Repair JSON format issues
        repaired = repair_json(result_section)

        # Step 3: Parse JSON
        data = json.loads(repaired)

        # Step 4: Type check
        if not isinstance(data, list):
            return None, (
                "Response must be a JSON array of integers, e.g., [0, 1, 2]. "
                f"You returned: {type(data).__name__}"
            )

        # Step 5: Validate with Pydantic (types, ranges, transitions)
        schema = TocLevelsSchema(levels=data)

        # Step 6: Context-aware validation (length match)
        expected_len = len(matched_titles)
        actual_len = len(schema.levels)
        if actual_len != expected_len:
            return None, (
                f"Array length mismatch: you provided {actual_len} levels, "
                f"but there are {expected_len} TARGET TITLES. "
                f"You must provide exactly one level for each TARGET TITLE."
            )

        # Success!
        return schema.levels, None

    except json.JSONDecodeError as e:
        return None, (
            f"Invalid JSON syntax: {str(e)}. "
            f"Please return a valid JSON array in the RESULT section like [0, 1, 2, 1, 0]."
        )

    except ValidationError as e:
        # Extract first error message (already LLM-friendly from our validators)
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
    """
    Map validated levels to Ref2Level dictionary.

    The levels list contains levels for matched_titles (in order).
    """
    ref2level: Ref2Level = {}

    for idx, (_, references) in enumerate(matched_titles):
        level = levels[idx]
        for page_index, order in references:
            ref2level[(page_index, order)] = level

    return ref2level
