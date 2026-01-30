import json
import logging

from json_repair import repair_json
from pydantic import BaseModel, ValidationError, field_validator

from ..llm import LLM, Message, MessageRole
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
    """Raised when LLM analysis fails after all retries"""

    pass


def analyse_toc_levels_by_llm(toc_pages: list[PageRef], llm: LLM) -> Ref2Level:
    """
    Analyze TOC hierarchy levels using LLM with validation and retry mechanism.

    Raises:
        LLMAnalysisError: If analysis fails after all retries
    """
    title_items: list[tuple[str, list[tuple[int, int]]]] = []  # (text, references)
    for ref in toc_pages:
        for title in ref.matched_titles:
            references = [(r.page_index, r.order) for r in title.references]
            if references:
                title_items.append((title.text, references))

    if not title_items:
        return {}

    # Build initial conversation with the task prompt
    initial_prompt = _build_llm_prompt(title_items)
    messages: list[Message] = [
        Message(role=MessageRole.USER, message=initial_prompt)
    ]
    last_error = None

    for attempt in range(_MAX_RETRIES):
        try:
            response = llm.request(input=messages)

            # Validate and parse response
            levels, error_msg = _validate_and_parse(response, title_items)

            if levels is not None:
                # Success! Map to Ref2Level
                print(f"LLM analysis succeeded on attempt {attempt + 1}")
                return _map_to_ref2level(levels, title_items)

            # Validation failed
            last_error = error_msg
            print(
                f"LLM response validation failed (attempt {attempt + 1}/{_MAX_RETRIES}): {error_msg}"
            )

            # Build conversation history for next attempt
            # Only keep: initial prompt + last assistant response + last error feedback
            if attempt < _MAX_RETRIES - 1:
                error_feedback = _build_error_feedback(error_msg or "Unknown error")
                messages = [
                    messages[0],  # Keep initial user prompt
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


def _build_error_feedback(error_message: str) -> str:
    """Build error feedback for LLM to correct its response"""
    prompt_lines = [
        "Your previous response had an error:",
        error_message,
        "",
        "Please correct the response. Remember:",
        "- Return ONLY a JSON array of integers",
        "- All values must be between 0 and 5",
        "- Level changes should not jump more than 2 at once",
        "- TOCs typically start at level 0",
        "",
        "Corrected response (JSON array only):",
    ]
    return "\n".join(prompt_lines)


def _validate_and_parse(
    response: str,
    title_items: list[tuple[str, list[tuple[int, int]]]],
) -> tuple[list[int] | None, str | None]:
    """
    Validate and parse LLM response.

    Returns:
        (levels, error_message)
        - If successful: (levels, None)
        - If failed: (None, error_message_for_llm)
    """
    try:
        # Step 1: Repair JSON format issues
        repaired = repair_json(response.strip())

        # Step 2: Parse JSON
        data = json.loads(repaired)

        # Step 3: Type check
        if not isinstance(data, list):
            return None, (
                "Response must be a JSON array of integers, e.g., [0, 1, 2]. "
                f"You returned: {type(data).__name__}"
            )

        # Step 4: Validate with Pydantic (types, ranges, transitions)
        schema = TocLevelsSchema(levels=data)

        # Step 5: Context-aware validation (length match)
        expected_len = len(title_items)
        actual_len = len(schema.levels)
        if actual_len != expected_len:
            return None, (
                f"Array length mismatch: you provided {actual_len} levels, "
                f"but there are {expected_len} TOC entries. "
                f"You must provide exactly one level for each entry (0 to {expected_len - 1})."
            )

        # Success!
        return schema.levels, None

    except json.JSONDecodeError as e:
        return None, (
            f"Invalid JSON syntax: {str(e)}. "
            f"Please return a valid JSON array like [0, 1, 2, 1, 0]."
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
    title_items: list[tuple[str, list[tuple[int, int]]]],
) -> Ref2Level:
    """Map validated levels to Ref2Level dictionary"""
    ref2level: Ref2Level = {}

    for idx, (_, references) in enumerate(title_items):
        level = levels[idx]
        for page_index, order in references:
            ref2level[(page_index, order)] = level

    return ref2level
