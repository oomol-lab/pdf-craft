"""Temporary oo-backed JEV adapter for PageAnalysis experiments."""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pdf_craft.extractor.chapter.page_review import JEV_QUESTION_NAME


class OoJevEvaluator:
    """Call ``jev.evaluate`` through the local oo CLI and retain raw evidence."""

    def __init__(
        self,
        output_path: Path | None = None,
        *,
        reuse_existing: bool = False,
    ) -> None:
        self._output_path = output_path
        self._reuse_existing = reuse_existing

    def __call__(self, page_index: int, request: dict[str, Any]) -> float:
        cached = self._cached_response(page_index, request)
        if cached is not None:
            return self._probability(page_index, cached)
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            encoding="utf-8",
            delete=False,
        ) as request_file:
            json.dump(request, request_file, ensure_ascii=False)
            request_path = Path(request_file.name)
        try:
            completed = subprocess.run(
                [
                    "oo", "connector", "run", "jev",
                    "--action", "evaluate",
                    "--data", f"@{request_path}",
                    "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            response = json.loads(completed.stdout)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
            raise RuntimeError(f"JEV evaluation failed for page {page_index}") from error
        finally:
            request_path.unlink(missing_ok=True)

        if self._output_path is not None:
            self._output_path.mkdir(parents=True, exist_ok=True)
            self._write_json(
                self._output_path / f"page_{page_index:03d}-request.json",
                request,
            )
            self._write_json(
                self._output_path / f"page_{page_index:03d}-response.json",
                response,
            )
        return self._probability(page_index, response)

    def _cached_response(
        self,
        page_index: int,
        request: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not self._reuse_existing or self._output_path is None:
            return None
        request_path = self._output_path / f"page_{page_index:03d}-request.json"
        response_path = self._output_path / f"page_{page_index:03d}-response.json"
        if not request_path.is_file() or not response_path.is_file():
            return None
        try:
            if json.loads(request_path.read_text(encoding="utf-8")) != request:
                return None
            response = json.loads(response_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return response if isinstance(response, dict) else None

    @staticmethod
    def _probability(page_index: int, response: dict[str, Any]) -> float:
        try:
            probability = response["data"]["answers"][JEV_QUESTION_NAME]["noul"]
        except (KeyError, TypeError) as error:
            raise RuntimeError(
                f"JEV returned an invalid response for page {page_index}"
            ) from error
        if not isinstance(probability, (int, float)):
            raise RuntimeError(
                f"JEV returned a non-numeric probability for page {page_index}"
            )
        return float(probability)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
