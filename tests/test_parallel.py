"""Tests for page partitioning and parallel OCR orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pdf_craft.parallel import partition_pages, run_parallel_ocr


def test_partition_pages_ten_over_three() -> None:
    assert partition_pages(10, 3) == [[1, 2, 3, 4], [5, 6, 7], [8, 9, 10]]


def test_partition_pages_caps_workers() -> None:
    assert partition_pages(2, 5) == [[1], [2]]


def test_partition_pages_single_worker() -> None:
    assert partition_pages(7, 1) == [list(range(1, 8))]


def test_partition_pages_empty() -> None:
    assert partition_pages(0, 3) == []


def test_partition_pages_invalid_workers() -> None:
    with pytest.raises(ValueError, match="workers"):
        partition_pages(5, 0)


@patch("pdf_craft.parallel.OCR")
@patch("pdf_craft.parallel.pdf_pages_count", return_value=3)
def test_run_parallel_ocr_workers_one_invokes_recognize(
    _mock_pages: MagicMock,
    mock_ocr_cls: MagicMock,
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "dummy.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    ap = tmp_path / "analysing"
    mock_inst = MagicMock()
    mock_inst.recognize.return_value = iter(())
    mock_ocr_cls.return_value = mock_inst

    run_parallel_ocr(
        pdf_path=pdf,
        analysing_path=ap,
        models_cache_path=None,
        local_only=False,
        workers=1,
    )

    mock_inst.recognize.assert_called_once()
    kw = mock_inst.recognize.call_args.kwargs
    assert kw["pdf_path"] == pdf
    assert kw["ocr_path"] == ap / "ocr"


def test_gpu_ids_length_mismatch_raises(tmp_path: Path) -> None:
    pdf = tmp_path / "dummy.pdf"
    pdf.write_bytes(b"x")
    with (
        patch("pdf_craft.parallel.pdf_pages_count", return_value=10),
        pytest.raises(ValueError, match="gpu_ids length"),
    ):
        run_parallel_ocr(
            pdf_path=pdf,
            analysing_path=tmp_path / "a",
            models_cache_path=None,
            local_only=False,
            workers=3,
            gpu_ids=(0, 1),
        )


def test_pdf_handler_forbidden_with_multiple_workers(tmp_path: Path) -> None:
    pdf = tmp_path / "dummy.pdf"
    pdf.write_bytes(b"x")
    handler = MagicMock()
    with (
        patch("pdf_craft.parallel.pdf_pages_count", return_value=4),
        pytest.raises(ValueError, match="pdf_handler"),
    ):
        run_parallel_ocr(
            pdf_path=pdf,
            analysing_path=tmp_path / "a",
            models_cache_path=None,
            local_only=False,
            workers=2,
            pdf_handler=handler,
        )
