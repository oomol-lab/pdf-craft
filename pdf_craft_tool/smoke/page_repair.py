"""Fixed page admission and live LLM repair, separate from JEV protocol tests."""

from __future__ import annotations

from pdf_craft import RetryPolicy

import asyncio
import json
from pathlib import Path
from typing import Any

from pdf_craft import ConcurrentExecutor, FixedCapacity, LLM
from pdf_craft.extractor.chapter.generation import prepare_chapter_analysis
from pdf_craft.extractor.chapter.analysis_types import PageAnalysis
from pdf_craft.extractor.chapter.analysis_content import layout_reference_spans
from pdf_craft.extractor.chapter.page_repair import AllPageLlmRepairProcessor
from pdf_craft.extractor.toc import TocInfo
from pdf_craft.llm import runtime_for

from ..runtime import model_request_options
from ..jev import PinnedPageSelection


def run_page_repair_smoke(
    ocr_path: Path,
    run_path: Path,
    config: dict[str, Any] | None,
    report,
) -> tuple[str, list[str], dict[str, Any]]:
    """Replay historical page selection, then check final repair targets."""

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
        selection = PinnedPageSelection(
            baseline_path,
            str(config["jev_run"]) if config.get("jev_run") else None,
        )
        llm = LLM(**config["llm"])
        runtime = runtime_for(
            llm,
            ConcurrentExecutor(FixedCapacity(int(config.get("concurrency", 1)))),
            protocol_version="current-page-refinement-plan-v3",
        )
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
            use_cache=False, **model_request_options(),
        )
        (raw_path / f"{stem}-response.txt").write_text(
            response + "\n", encoding="utf-8"
        )
        return response

    processor = AllPageLlmRepairProcessor(
        request,
        page_indexes=selection.page_indexes,
        retry_policy=RetryPolicy(**config.get("retry_policy", {})),
    )
    evidence = config.get('printed_evidence')
    if evidence is not None:
        from .printed_evidence import recover_printed_evidence
        ocr_path = recover_printed_evidence(ocr_path, run_path / 'printed-source' / 'ocr',
                                           Path(evidence['pdf']), evidence['dpi'])
    analysis = prepare_chapter_analysis(ocr_path, TocInfo([], []))
    with report.stage("repair"):
        repaired = await processor(
            analysis.source_pages,
            analysis.pages,
            analysis.page_pixel_sizes,
        )
    actual = {
        "review_page_indexes": processor.page_indexes,
        "changes": _semantic_changes(analysis.pages, repaired),
    }
    result_path = run_path / "page-repair-result.json"
    _write_json(result_path, actual)

    with report.stage("check"):
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        errors = _check_final_state(repaired, expected, source_pages=analysis.pages)
        if actual['review_page_indexes'] != expected['review_page_indexes']:
            errors.append('Fixed page repair admission differs from committed selection')
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
                "jev_run": selection.run_name,
                "admission": "fixed-historical-page-selection",
                "result": str(result_path),
                "review_page_indexes": actual["review_page_indexes"],
                "attempts": attempts,
                "printed_evidence": str(run_path / "printed-source" / "printed-evidence.json") if evidence else None,
            }
        },
    )


def _check_final_state(pages: list[PageAnalysis], expected: dict[str, Any], *,
                       source_pages: list[PageAnalysis] | None = None) -> list[str]:
    """Check target facts even when the initial algorithm already establishes them.

    Changes remain diagnostic output. Expectations describe final state, not a
    requirement to break an initially correct page and repair it again.
    """
    if not expected.get('pages'):
        raise ValueError('Page repair expectation needs final page targets')
    actual = {page.page_index: page for page in pages}
    errors = []
    for target in expected['pages']:
        index = target['page_index']
        page = actual.get(index)
        if page is None:
            errors.append(f'Missing final page {index}')
            continue
        layouts = {_layout_id(layout): _layout_semantics(layout) for layout in page.layouts}
        for identity, fields in target.get('layouts', {}).items():
            if identity not in layouts:
                errors.append(f'Page {index}: missing final {identity}')
                continue
            for name, value in fields.items():
                if name not in layouts[identity] or layouts[identity][name] != value:
                    errors.append(f'Page {index}: {identity}.{name} differs from final target')
        for name, value in (('citations', [_citation_semantics(c) for c in page.citations]),
                            ('references', _reference_semantics(page))):
            if name in target and target[name] != value:
                errors.append(f'Page {index}: final {name} differ from target')
        if 'paragraph_answers' in target:
            source = next((item for item in source_pages or [] if item.page_index == index), None)
            if source is None:
                raise ValueError('Paragraph delivery checks require original source pages')
            errors.extend(_check_paragraph_answers(page, source, target))
    return errors


def _check_paragraph_answers(page, source, target):
    """Allow reviewed paragraph variants, with exact source/content preservation.

    This fixture-level oracle does not decide prose semantics in production.
    Compare actual assembly output, never a model explanation or join flag alone.
    """
    from copy import deepcopy
    from xml.etree.ElementTree import tostring
    from pdf_craft.extractor.chapter.chapter import SourceTextFragment, TextFlowItem, _encode_fragment
    from pdf_craft.extractor.chapter.page_analysis import restore_streams
    required = target['body_sources']
    answers = target['paragraph_answers']
    span_only = target.get('paragraph_scope') == 'span'
    if target.get('paragraph_scope', 'page') not in ('page', 'span'):
        raise ValueError('Unknown paragraph answer scope')
    if (not answers or len(set(required)) != len(required)
            or any([identity for group in answer for identity in group] != required
                   or any(not group for group in answer) for answer in answers)):
        raise ValueError('Paragraph answers must partition every required source in order')

    def delivered(value):
        stream = restore_streams([value])
        groups, fragments = [], []
        if stream.citations and not span_only:
            raise ValueError('Unexpected note in body-only paragraph fixture')
        for item in stream.paragraphs:
            if span_only:
                identities = {
                    f'layout-{(fragment.page_index << 32) + fragment.source_order}'
                    for fragment in getattr(item, 'children', ())
                    if isinstance(fragment, SourceTextFragment)
                }
                if not identities.intersection(required):
                    continue
                if not identities.issubset(required):
                    raise ValueError('Reviewed paragraph span crosses its surrounding content')
            if not isinstance(item, TextFlowItem) or item.role != 'body':
                raise ValueError('Expected body text paragraphs only')
            group = []
            for fragment in item.children:
                if not isinstance(fragment, SourceTextFragment):
                    raise ValueError('Unexpected asset in text-only paragraph fixture')
                group.append(f'layout-{(fragment.page_index << 32) + fragment.source_order}')
                fragments.append(tostring(_encode_fragment(fragment)))
            groups.append(group)
        return groups, fragments

    try:
        groups, fragments = delivered(page)
        original = deepcopy(source)
        original.layouts = [item for item in original.layouts if _layout_id(item) in required]
        source_groups, source_fragments = delivered(original)
    except (ValueError, TypeError) as error:
        return [f'Page {page.page_index}: paragraph delivery failed: {error}']
    errors = []
    if ([identity for group in source_groups for identity in group] != required
            or [identity for group in groups for identity in group] != required
            or fragments != source_fragments):
        errors.append(f'Page {page.page_index}: body source content/order differs from original')
    if groups not in answers:
        errors.append(f'Page {page.page_index}: delivered paragraphs match no reviewed answer')
    return errors


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
    return layout.source_id or f"layout-{layout.document_order}"


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
