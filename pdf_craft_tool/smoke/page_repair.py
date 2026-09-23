"""Smoke route for pinned-JEV admission followed by live LLM page repair."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pdf_craft import LLM
from pdf_craft.extractor.chapter.generation import prepare_chapter_analysis
from pdf_craft.extractor.chapter.page_analysis import (
    PageAnalysis,
    layout_reference_spans,
)
from pdf_craft.extractor.chapter.page_repair import JevLlmRepairProcessor
from pdf_craft.extractor.chapter.page_review import JEV_REVIEW_THRESHOLD
from pdf_craft.extractor.toc import TocInfo
from pdf_craft.llm import runtime_for

from ..jev import PinnedJevEvaluator


def run_page_repair_smoke(
    ocr_path: Path,
    run_path: Path,
    config: dict[str, Any] | None,
    report,
) -> tuple[str, list[str], dict[str, Any]]:
    """Replay JEV routing, run live LLM repair, and compare semantic changes."""

    return asyncio.run(_run_page_repair_smoke(ocr_path, run_path, config, report))


async def _run_page_repair_smoke(
    ocr_path: Path,
    run_path: Path,
    config: dict[str, Any] | None,
    report,
) -> tuple[str, list[str], dict[str, Any]]:

    if not config or not isinstance(config.get("llm"), dict):
        for stage in ("configure", "repair", "check"):
            report.skipped(stage, "missing page_repair.llm")
        return (
            "skipped",
            ["Page repair smoke requires page_repair.llm with explicit credentials"],
            {},
        )
    baseline_path = Path(str(config.get("jev_baseline", "")))
    expected_path = Path(str(config.get("expected", "")))
    if not baseline_path.is_file():
        raise ValueError(f"Page repair JEV baseline does not exist: {baseline_path}")
    if not expected_path.is_file():
        raise ValueError(f"Page repair expectation does not exist: {expected_path}")

    with report.stage("configure"):
        evaluator = PinnedJevEvaluator(
            baseline_path,
            str(config["jev_run"]) if config.get("jev_run") else None,
        )
        llm = LLM(**config["llm"])
        runtime = runtime_for(llm, protocol_version="page-repair-json-v1")
        raw_path = run_path / "llm-raw"
        raw_path.mkdir()

    async def request(messages, index, maximum):
        payload = json.loads(messages[1].message)
        page_index = payload["target_page"]["page_index"]
        stem = f"page_{page_index:03d}-attempt_{index + 1:02d}"
        _write_json(raw_path / f"{stem}-request.json", [
            {"role": message.role.name.lower(), "content": message.message}
            for message in messages
        ])
        response = await runtime.request(
            messages,
            max_tokens=16000,
            retry_index=index,
            retry_max=maximum,
            use_cache=False,
        )
        (raw_path / f"{stem}-response.txt").write_text(
            response + "\n", encoding="utf-8"
        )
        return response

    processor = JevLlmRepairProcessor(
        evaluator,
        request,
        threshold=float(config.get("threshold", JEV_REVIEW_THRESHOLD)),
        max_retries=int(config.get("max_retries", 4)),
    )
    analysis = prepare_chapter_analysis(ocr_path, TocInfo([], []))
    with report.stage("repair"):
        repaired = await processor(
            analysis.source_pages,
            analysis.pages,
            analysis.page_pixel_sizes,
        )
    actual = {
        "review_page_indexes": [
            result.page_index
            for result in processor.results
            if result.requires_review
        ],
        "changes": _semantic_changes(analysis.pages, repaired),
    }
    result_path = run_path / "page-repair-result.json"
    _write_json(result_path, actual)

    with report.stage("check"):
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        errors = [] if actual == expected else [
            "Page repair final semantic result differs from committed expectation"
        ]
    attempts = {
        str(page_index): len(list(raw_path.glob(
            f"page_{page_index:03d}-attempt_*-response.txt"
        )))
        for page_index in actual["review_page_indexes"]
    }
    return (
        "passed" if not errors else "failed",
        errors,
        {
            "page_repair": {
                "jev_run": evaluator.run_name,
                "result": str(result_path),
                "review_page_indexes": actual["review_page_indexes"],
                "attempts": attempts,
            }
        },
    )


def _semantic_changes(
    before: list[PageAnalysis],
    after: list[PageAnalysis],
) -> list[dict[str, Any]]:
    before_pages = {page.page_index: page for page in before}
    changes: list[dict[str, Any]] = []
    for page in after:
        original = before_pages[page.page_index]
        original_layouts = {
            _layout_id(layout): _layout_semantics(layout)
            for layout in original.layouts
        }
        changed_layouts: dict[str, dict[str, Any]] = {}
        for layout in page.layouts:
            layout_id = _layout_id(layout)
            semantics = _layout_semantics(layout)
            if semantics != original_layouts.get(layout_id):
                changed_layouts[layout_id] = semantics
        original_citations = [_citation_semantics(item) for item in original.citations]
        repaired_citations = [_citation_semantics(item) for item in page.citations]
        original_references = _reference_semantics(original)
        repaired_references = _reference_semantics(page)
        page_change: dict[str, Any] = {"page_index": page.page_index}
        if changed_layouts:
            page_change["layouts"] = changed_layouts
        if repaired_citations != original_citations:
            page_change["citations"] = repaired_citations
        if repaired_references != original_references:
            page_change["references"] = repaired_references
        if len(page_change) > 1:
            changes.append(page_change)
    return changes


def _layout_id(layout) -> str:
    return f"layout-{layout.document_order}"


def _layout_semantics(layout) -> dict[str, Any]:
    return {
        "ownership": layout.ownership,
        "continues_from_previous": layout.continues_from_previous,
        "continues_to_next": layout.continues_to_next,
        "paragraph_role": layout.paragraph_role,
        "paragraph_level": layout.paragraph_level,
        "citation_id": layout.citation_id,
        "citation_index": layout.citation_index,
    }


def _citation_semantics(citation) -> dict[str, Any]:
    return {
        "citation_id": citation.citation_id,
        "index": citation.index,
        "mark": citation.mark if isinstance(citation.mark, str) else (
            citation.mark.char if citation.mark is not None else None
        ),
        "stream_order": citation.stream_order,
    }


def _reference_semantics(page: PageAnalysis) -> list[dict[str, Any]]:
    return [
        {
            "layout_id": _layout_id(layout),
            "start": span.start,
            "end": span.end,
            "citation_index": span.citation_index,
            "mark": span.mark_text,
        }
        for layout in page.layouts
        for span in layout_reference_spans(layout)
    ]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
